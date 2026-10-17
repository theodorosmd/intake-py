"""Le cerveau et la voix de l'agent : LLM et synthèse vocale, via LiteLLM."""

import json
import logging
import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import litellm
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("uvicorn.error")

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://127.0.0.1:8000")
MAX_RONDES_OUTILS = 3  # le LLM peut enchaîner quelques outils, pas tourner en boucle


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
        "Never give legal or medical advice. If the caller mentions an injury, show "
        "sympathy and ask whether they have received medical care, but never tell "
        "them what to do about it. "
        "Never promise anything you cannot do yourself: no callbacks, no outcomes. "
        "Once you have the name, phone number, accident date, accident type and "
        "whether they were injured, call save_intake once, then offer a consultation "
        "and book it with book_consultation. Say that the information was recorded, "
        "or that a consultation is booked, only after the tool confirmed it. If a "
        "tool returns an error, fix the input or say you could not complete it. "
        "Consultations are on weekdays at 10 AM, 2 PM or 4 PM New York time. Offer "
        "these slots and call book_consultation only after the caller has agreed to "
        "a specific day and one of these times; never book to check availability. "
        "If the caller asks for another time, say which times exist and let them choose. "
        "If a name sounds unusual or unclear, or if the caller corrects their name, "
        "ask them to spell their first name letter by letter before you save it. Before calling a tool, say one very short sentence such as "
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
                    "accident_date": {"type": "string", "description": "YYYY-MM-DD"},
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
                    "accident_date",
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
]
CHEMINS_OUTILS = {"save_intake": "/intake", "book_consultation": "/consultations"}
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


async def executer_outil(nom: str, arguments: str) -> dict:
    """Exécute un outil demandé par le LLM : un appel HTTP vers les routes de la passerelle."""
    chemin = CHEMINS_OUTILS.get(nom)
    if chemin is None:
        return {"error": f"unknown tool {nom}"}
    try:
        donnees = json.loads(arguments or "{}")
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


async def phrases_llm(historique: list[dict], faits: list[str] | None = None):
    """Demande la réponse au LLM en flux et donne les phrases dès qu'elles sont finies.

    Si le LLM demande un outil, on l'exécute et on repart avec son résultat. Chaque
    outil exécuté est noté dans `faits` (pour que la suite de l'appel le sache).
    """
    parametres = parametres_llm()
    messages = [{"role": "system", "content": consignes()}, *historique]
    for _ in range(MAX_RONDES_OUTILS + 1):
        flux = await litellm.acompletion(
            **parametres, messages=messages, tools=OUTILS, stream=True, timeout=15
        )
        tampon, dit = "", ""
        appels: dict[int, dict] = {}
        async for morceau in flux:
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
            dit += texte
            tampon += texte
            phrases, tampon = decouper_phrases(tampon)
            for phrase in phrases:
                yield phrase
        if tampon.strip():
            yield tampon.strip()
        if not appels:
            return
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
            if faits is not None and "error" not in resultat:
                faits.append(
                    f"[Tool {a['name']} already done and confirmed: "
                    f"{json.dumps(resultat)[:200]}. Do not call it again.]"
                )


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
