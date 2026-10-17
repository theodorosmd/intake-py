"""Le cerveau et la voix de l'agent : LLM et synthèse vocale, via LiteLLM."""

import logging
import os
import time

import litellm
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("uvicorn.error")

CONSIGNES = (
    "You are the phone intake assistant of a New York personal injury law firm. "
    "You are an AI and you say so if asked. This is a phone call: answer in one or "
    "two short sentences, without lists or symbols. Ask one question at a time to "
    "learn the caller's name, phone number, accident date and what happened. "
    "Never give legal advice."
)
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


async def reponse_llm(historique: list[dict]) -> str:
    """Envoie la conversation au LLM et renvoie sa réponse."""
    parametres = parametres_llm()
    debut = time.perf_counter()
    reponse = await litellm.acompletion(
        **parametres,
        messages=[{"role": "system", "content": CONSIGNES}, *historique],
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
