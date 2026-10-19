"""Le cerveau et la voix de l'agent : LLM et synthèse vocale, via LiteLLM."""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import litellm
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("uvicorn.error")

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://127.0.0.1:8000")
MAX_RONDES_OUTILS = 3  # le LLM peut enchaîner quelques outils, pas tourner en boucle


def jours_ouvres(n: int = 5) -> str:
    """Les prochains jours de semaine, en toutes lettres : le LLM inventait parfois
    « seulement du lundi au mercredi »."""
    jour = datetime.now(ZoneInfo("America/New_York")).date()
    jours = []
    while len(jours) < n:
        jour += timedelta(days=1)
        if jour.weekday() < 5:
            jours.append(jour.strftime("%A %B %d").replace(" 0", " "))
    return ", ".join(jours)


def consignes() -> str:
    """Les consignes de l'agent, avec la date du jour (il en a besoin pour « il y a deux jours »)."""
    aujourdhui = datetime.now(ZoneInfo("America/New_York")).strftime("%A, %B %d, %Y")
    return (
        "You are the phone intake assistant of a New York personal injury law firm. "
        "You are an AI and you say so if asked. "
        f"Today is {aujourdhui} (New York time). "
        "This is a phone call: answer in one or two short sentences, without lists "
        "or symbols. Ask one question at a time to learn the caller's name, phone "
        "number, accident date, accident type (car, slip and fall, work or other), "
        "whether they were injured, and what happened. "
        "Safety first: if the caller describes an emergency happening now (heavy "
        "bleeding, dizziness, chest pain, trouble breathing, losing consciousness), "
        "tell them right away to hang up and call 911, before any other question, "
        "and do not continue the intake unless they say they are safe. "
        "Never turn a number of days or weeks into a calendar date yourself: pass "
        "accident_days_ago, and when you confirm, repeat it as the caller said it. "
        "Never give legal or medical advice. If the caller mentions an injury, show "
        "sympathy and ask whether they have received medical care, but never tell "
        "them what to do about it. "
        "Never promise anything you cannot do yourself: no callbacks, no outcomes. "
        "When the caller asks about the firm itself (fees, cost, hours, location, "
        "languages, which cases it takes, how the consultation works, what to bring), "
        "call search_firm_info and answer only from its results, in one or two short "
        "sentences; if it finds nothing, say you do not have that information and that "
        "the attorney can cover it at the consultation. Never guess a fact about the firm. "
        "If the caller asks a legal question (how long they have to file, whether they "
        "can win, what the case is worth, what to tell an insurance company), say you "
        "cannot give legal advice and that the attorney will answer it during the free "
        "consultation; do not guess, even to reassure them. "
        "Once you have the name, phone number, accident date, accident type and "
        "whether they were injured, call save_intake once, right away, even if the "
        "caller does not want a consultation; never ask permission to record. Then "
        "offer a consultation "
        "and book it with book_consultation. Say that the information was recorded, "
        "or that a consultation is booked, only after the tool confirmed it. If a "
        "tool returns an error, fix the input or say you could not complete it. "
        "Consultations are available every weekday, Monday to Friday, at 10 AM, 2 PM "
        f"or 4 PM New York time ({jours_ouvres()}, and the weeks after); never say a "
        "weekday is unavailable and do not offer a shorter list of days. Offer these slots and call book_consultation only after the caller has agreed to "
        "a specific day and one of these times; never book to check availability. "
        "If the caller asks for another time, say which times exist and let them choose. "
        "If a name sounds unusual or unclear, or if the caller corrects their name, "
        "ask them to spell the part that was unclear, letter by letter. Before "
        "calling save_intake, read back the caller's full name and phone number in "
        "one short sentence and let them correct it. Before calling a tool, say one very short sentence such as "
        "\"One moment.\" "
        "If the caller wants to change the consultation date or time, you must call "
        "book_consultation again with the new slot and only say it was changed after "
        "the tool confirms it; never say a booking was updated, moved or cancelled "
        "without calling the tool in that same turn."
    )


OUTILS = [
    {
        "type": "function",
        "function": {
            "name": "save_intake",
            "description": "Record the caller's case file. Call it once, when the required fields are known.",
            "parameters": {
                "type": "object",
                "properties": {
                    "caller_name": {"type": "string"},
                    "phone": {
                        "type": "string",
                        "description": "With country code, e.g. +15551234567",
                    },
                    "accident_date": {
                        "type": "string",
                        "description": "YYYY-MM-DD, only if the caller gave a calendar date",
                    },
                    "accident_days_ago": {
                        "type": "integer",
                        "description": "If the caller gave a relative date (yesterday = 1, "
                        "45 days ago = 45, two weeks ago = 14), pass this instead of "
                        "accident_date: the system computes the date.",
                    },
                    "accident_type": {
                        "type": "string",
                        "enum": ["car", "slip_and_fall", "work", "other"],
                    },
                    "injured": {"type": "boolean"},
                    "has_insurance": {"type": "boolean"},
                    "accident_location": {"type": "string"},
                    "injury_details": {"type": "string"},
                },
                "required": [
                    "caller_name",
                    "phone",
                    "accident_type",
                    "injured",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_consultation",
            "description": "Book (or move) the caller's consultation in the firm's calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "caller_name": {"type": "string"},
                    "phone": {"type": "string"},
                    "preferred_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "preferred_hour": {
                        "type": "integer",
                        "description": "0-23, optional",
                    },
                },
                "required": ["caller_name", "phone", "preferred_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_firm_info",
            "description": "Search the firm's own information: fees and costs, office hours and "
            "location, languages, which cases the firm handles, how the consultation works, "
            "what to prepare. Use it before answering any question about the firm.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The caller's question, rephrased as a short standalone question",
                    }
                },
                "required": ["question"],
            },
        },
    },
]
CHEMINS_OUTILS = {
    "save_intake": "/intake",
    "book_consultation": "/consultations",
    "search_firm_info": "/knowledge/search",
}
OUTILS_SANS_EFFET = {"search_firm_info"}  # lecture seule : n'autorise pas une annonce de réservation
SALUTATION = (
    "Hello, you've reached the firm's intake line. I'm an AI assistant. "
    "How can I help you today?"
)


def parametres_llm() -> dict:
    """Où envoyer les appels LLM : au fournisseur directement, ou au proxy LiteLLM.

    Avec LLM_API_BASE défini, LLM_MODEL est un nom connu du proxy (ex. openai/llm-agent)
    et LLM_API_KEY une virtual key délivrée par le proxy, pas une clé OpenAI.
    """
    parametres = {"model": os.environ["LLM_MODEL"]}
    base = os.getenv("LLM_API_BASE")
    if base:
        parametres["api_base"] = base
        parametres["api_key"] = os.environ["LLM_API_KEY"]
    return parametres


FIN_DE_PHRASE = re.compile(r"(?<=[.!?])\s+")
MIN_CARACTERES = 15  # évite d'envoyer « Sure. » seul à la synthèse vocale


def decouper_phrases(tampon: str) -> tuple[list[str], str]:
    """Sépare le texte reçu en phrases complètes et en un reste pas fini."""
    morceaux = FIN_DE_PHRASE.split(tampon)
    reste = morceaux.pop()
    phrases, courante = [], ""
    for morceau in morceaux:
        courante = f"{courante} {morceau}".strip()
        if len(courante) >= MIN_CARACTERES:
            phrases.append(courante)
            courante = ""
    if courante:
        # Pas de strip() : il effaçait l'espace de fin du reste (« at » + « 10 » → « at10 »).
        reste = f"{courante} {reste}" if reste else courante
    return phrases, reste


# Ce que la suite de l'appel doit savoir d'un outil réussi. La réservation n'est pas
# figée : « ne le rappelle pas » empêchait l'agent de déplacer un rendez-vous, et il
# annonçait alors le changement sans l'avoir fait.
SUITE_OUTIL = {
    "save_intake": "Do not call it again.",
    "search_firm_info": "Answer only from these results. Search again only for a different question.",
    "book_consultation": (
        "If the caller wants another day or time, call book_consultation again "
        "with the new slot before saying it changed."
    ),
}


def note_outil(nom: str, resultat: dict) -> str:
    return (
        f"[Tool {nom} already done and confirmed: {json.dumps(resultat)[:200]}. "
        f"{SUITE_OUTIL.get(nom, 'Do not call it again.')}]"
    )


def normaliser_arguments(nom: str, donnees: dict) -> dict:
    """Le LLM calcule mal les dates (« 45 days ago » est devenu le 4 août au lieu du
    3 septembre) : il donne le nombre de jours, le code fait le calcul."""
    if nom == "save_intake" and "accident_days_ago" in donnees:
        try:
            jours = int(donnees.pop("accident_days_ago"))
        except (TypeError, ValueError):
            return donnees
        aujourdhui = datetime.now(ZoneInfo("America/New_York")).date()
        donnees.setdefault("accident_date", (aujourdhui - timedelta(days=jours)).isoformat())
    return donnees


async def executer_outil(nom: str, arguments: str) -> dict:
    """Exécute un outil demandé par le LLM : un appel HTTP vers les routes de la passerelle."""
    chemin = CHEMINS_OUTILS.get(nom)
    if chemin is None:
        return {"error": f"unknown tool {nom}"}
    try:
        donnees = normaliser_arguments(nom, json.loads(arguments or "{}"))
    except json.JSONDecodeError:
        return {"error": "arguments are not valid JSON"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            reponse = await client.post(GATEWAY_URL + chemin, json=donnees)
    except httpx.HTTPError as erreur:
        return {"error": f"gateway unreachable: {erreur!r}"}
    if reponse.status_code >= 400:
        return {
            "error": reponse.text[:300]
        }  # le LLM peut corriger (date mal formatée...)
    return reponse.json()


# Une phrase qui annonce un rendez-vous pris ou déplacé. Si aucun outil n'a tourné dans
# le tour, elle est fausse : le calendrier n'a pas bougé, mais l'appelant y croirait.
ANNONCE_RESERVATION = re.compile(
    r"\b(I(?:'ve| have)? (?:booked|scheduled|changed|moved|rescheduled|updated)"
    r"|(?:is|has been|was|are) (?:now )?(?:booked|scheduled|changed|moved|rescheduled|updated))\b",
    re.IGNORECASE,
)
CORRECTION_ANNONCE = (
    "Correction: you said a consultation was booked or changed, but book_consultation "
    "was not called in this turn, so the calendar did not change and the caller has not "
    "heard that sentence. If the caller asked for a new or different slot, call "
    "book_consultation now. If you were only repeating a booking confirmed earlier, "
    "repeat it."
)


async def phrases_llm(historique: list[dict], faits: list[str] | None = None):
    """Demande la réponse au LLM en flux et donne les phrases dès qu'elles sont finies.

    Si le LLM demande un outil, on l'exécute et on repart avec son résultat. Chaque
    outil exécuté est noté dans `faits` (pour que la suite de l'appel le sache).

    Garde-fou : une phrase qui annonce une réservation alors qu'aucun outil n'a tourné
    dans ce tour n'est pas dite ; le LLM est relancé une fois avec une correction.
    Les consignes seules ne suffisaient pas (environ un échec sur dix au rejeu).
    """
    parametres = parametres_llm()
    messages = [{"role": "system", "content": consignes()}, *historique]
    outil_fait, corrige = False, False
    for _ in range(MAX_RONDES_OUTILS + 1):
        t_envoi = time.perf_counter()
        flux = await litellm.acompletion(
            **parametres, messages=messages, tools=OUTILS, stream=True, timeout=15
        )
        t_entetes = time.perf_counter()
        t_morceau = t_texte = None
        tampon, dit = "", ""
        retenues: list[str] = []
        appels: dict[int, dict] = {}

        async for morceau in flux:
            if t_morceau is None:
                t_morceau = time.perf_counter()
            if not morceau.choices:
                continue
            delta = morceau.choices[0].delta
            for appel in getattr(delta, "tool_calls", None) or []:
                cumul = appels.setdefault(
                    appel.index, {"id": "", "name": "", "arguments": ""}
                )
                cumul["id"] = appel.id or cumul["id"]
                if appel.function:
                    cumul["name"] += appel.function.name or ""
                    cumul["arguments"] += appel.function.arguments or ""
            texte = delta.content or ""
            if texte and t_texte is None:
                t_texte = time.perf_counter()
                logger.info(
                    "LLM : reponse du proxy %d ms, 1er morceau +%d ms, 1er texte +%d ms (%d messages)",
                    (t_entetes - t_envoi) * 1000,
                    (t_morceau - t_entetes) * 1000,
                    (t_texte - t_morceau) * 1000,
                    len(messages),
                )
            dit += texte
            tampon += texte
            phrases, tampon = decouper_phrases(tampon)
            for phrase in phrases:
                if outil_fait or corrige or not ANNONCE_RESERVATION.search(phrase):
                    yield phrase
                else:
                    retenues.append(phrase)
        fin = tampon.strip()
        if fin:
            if outil_fait or corrige or not ANNONCE_RESERVATION.search(fin):
                yield fin
            else:
                retenues.append(fin)
        if not appels:
            if not retenues:
                return
            logger.warning("Annonce sans outil retenue : %s", " ".join(retenues))
            corrige = True
            messages += [
                {"role": "assistant", "content": dit},
                {"role": "system", "content": CORRECTION_ANNONCE},
            ]
            continue
        messages.append(
            {
                "role": "assistant",
                "content": dit or None,
                "tool_calls": [
                    {
                        "id": a["id"],
                        "type": "function",
                        "function": {"name": a["name"], "arguments": a["arguments"]},
                    }
                    for a in appels.values()
                ],
            }
        )
        for a in appels.values():
            debut = time.perf_counter()
            resultat = await executer_outil(a["name"], a["arguments"])
            logger.info(
                "OUTIL %s : %d ms -> %s",
                a["name"],
                (time.perf_counter() - debut) * 1000,
                json.dumps(resultat)[:200],
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": a["id"],
                    "content": json.dumps(resultat),
                }
            )
            if "error" not in resultat:
                if a["name"] not in OUTILS_SANS_EFFET:
                    outil_fait = True
                if faits is not None:
                    faits.append(note_outil(a["name"], resultat))


async def reponse_llm(historique: list[dict]) -> str:
    """Envoie la conversation au LLM et renvoie sa réponse."""
    parametres = parametres_llm()
    debut = time.perf_counter()
    reponse = await litellm.acompletion(
        **parametres,
        messages=[{"role": "system", "content": consignes()}, *historique],
        timeout=15,
    )
    texte = reponse.choices[0].message.content.strip()
    logger.info(
        "LLM : %d ms (%s)",
        (time.perf_counter() - debut) * 1000,
        "via le proxy" if "api_base" in parametres else "direct",
    )
    return texte


_voix_deja_faites: dict[str, bytes] = {}


async def synthese_vocale(texte: str) -> bytes:
    """Transforme le texte en voix (fichier mp3).

    Les phrases déjà synthétisées (la salutation, par exemple) sont gardées
    en mémoire : la deuxième fois, elles partent sans attendre.
    """
    if texte in _voix_deja_faites:
        logger.info("Voix : deja prete (0 ms)")
        return _voix_deja_faites[texte]
    debut = time.perf_counter()
    reponse = await litellm.aspeech(
        model=os.getenv("TTS_MODEL", "openai/gpt-4o-mini-tts"),
        voice=os.getenv("TTS_VOICE", "alloy"),
        input=texte,
        response_format="mp3",
        timeout=15,
    )
    audio = reponse.content
    if texte == SALUTATION:
        _voix_deja_faites[texte] = audio
    logger.info(
        "Voix : %d ms, %d octets", (time.perf_counter() - debut) * 1000, len(audio)
    )
    return audio
