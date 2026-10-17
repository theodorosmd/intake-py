"""Étape 2 bis : décrocher un appel par code et recevoir son audio en direct."""

import asyncio
import base64
import json
import logging
import os
import time
import wave
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx
import websockets
from dotenv import load_dotenv
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import agent_vocal

load_dotenv()

logger = logging.getLogger("uvicorn.error")
router = APIRouter()

TELNYX_API = "https://api.telnyx.com/v2"
ENREGISTREMENTS = Path("enregistrements")

# Transcription en streaming via l'API STT de Telnyx (même clé API).
# Deepgram accepte le son du téléphone (mulaw 8 kHz) tel quel et renvoie
# des résultats provisoires pendant que l'appelant parle.
STT_URL = "wss://api.telnyx.com/v2/speech-to-text/transcription"
STT_PARAMS = {
    "transcription_engine": "Deepgram",
    "model": "nova-3",
    "input_format": "mulaw",
    "sample_rate": "8000",
    "language": "en-US",
    "interim_results": "true",
    "endpointing": "500",  # 500 ms de silence = fin de phrase
}
DELAI_FIN_DE_PHRASE = 0.8  # secondes sans nouveau texte = l'appelant a fini
TAILLE_ENVOI = 800  # on regroupe 100 ms de son (8 000 octets/s) avant d'envoyer


def ulaw_vers_pcm16(octets: bytes) -> bytes:
    """Décode du G.711 mu-law (PCMU, le son du téléphone) en PCM 16 bits."""
    sortie = bytearray()
    for octet in octets:
        u = ~octet & 0xFF
        exposant = (u >> 4) & 0x07
        mantisse = u & 0x0F
        valeur = (((mantisse << 3) + 0x84) << exposant) - 0x84
        echantillon = -valeur if u & 0x80 else valeur
        sortie += echantillon.to_bytes(2, "little", signed=True)
    return bytes(sortie)


async def repondre(call_control_id: str) -> None:
    """Décroche l'appel et demande à Telnyx d'envoyer l'audio sur notre WebSocket."""
    url = f"{TELNYX_API}/calls/{call_control_id}/actions/answer"
    corps = {
        "stream_url": f"wss://{os.environ['PUBLIC_HOST']}/telnyx/media",
        "stream_track": "inbound_track",
        "stream_codec": "PCMU",
    }
    entetes = {"Authorization": f"Bearer {os.environ['TELNYX_API_KEY']}"}
    async with httpx.AsyncClient(timeout=10) as client:
        reponse = await client.post(url, json=corps, headers=entetes)
    logger.info("Decrochage : HTTP %s", reponse.status_code)
    if reponse.status_code >= 400:
        logger.warning("Refus Telnyx : %s", reponse.text)


async def ouvrir_stt():
    """Ouvre la WebSocket de transcription vers Telnyx."""
    return await websockets.connect(
        f"{STT_URL}?{urlencode(STT_PARAMS)}",
        additional_headers={"Authorization": f"Bearer {os.environ['TELNYX_API_KEY']}"},
    )


async def lire_transcriptions(stt, tours=None) -> list[str]:
    """Affiche le texte au fil de l'eau, et signale chaque fin de phrase.

    Les morceaux définitifs (is_final) s'accumulent. La phrase part dans la
    file `tours` quand la transcription signale la fin (speech_final), ou
    quand aucun nouveau texte n'arrive pendant DELAI_FIN_DE_PHRASE.
    """
    finales = []
    phrase = []
    dernier_final = 0.0
    minuteur = None

    def envoyer_tour():
        if phrase and tours is not None:
            tours.put_nowait((" ".join(phrase), dernier_final))
        phrase.clear()

    async def attendre_silence():
        await asyncio.sleep(DELAI_FIN_DE_PHRASE)
        envoyer_tour()

    async for brut in stt:
        message = json.loads(brut)
        if message.get("errors"):
            logger.warning("Erreur STT : %s", message["errors"])
            continue
        texte = message.get("transcript", "")
        if texte and minuteur:
            minuteur.cancel()  # l'appelant parle encore
            minuteur = None
        if texte and message.get("is_final"):
            finales.append(texte)
            phrase.append(texte)
            dernier_final = time.perf_counter()
            logger.info("APPELANT : %s", texte)
            minuteur = asyncio.create_task(attendre_silence())
        elif texte:
            logger.info("   ... %s", texte)
        if message.get("speech_final") or message.get("utterance_end"):
            if minuteur:
                minuteur.cancel()
                minuteur = None
            envoyer_tour()
    if minuteur:
        minuteur.cancel()
    return finales


async def parler(ws: WebSocket, texte: str) -> None:
    """Synthétise le texte et l'envoie dans l'appel (mp3 en base64)."""
    audio = await agent_vocal.synthese_vocale(texte)
    payload = base64.b64encode(audio).decode()
    await ws.send_json({"event": "media", "media": {"payload": payload}})


async def boucle_agent(ws: WebSocket, tours: asyncio.Queue) -> None:
    """Salue, puis répond à chaque phrase de l'appelant, une à la fois."""
    historique = [{"role": "assistant", "content": agent_vocal.SALUTATION}]
    try:
        await parler(ws, agent_vocal.SALUTATION)
    except Exception:
        logger.exception("Salutation impossible")
    while True:
        phrase, fin_parole = await tours.get()
        try:
            historique.append({"role": "user", "content": phrase})
            reponse = await agent_vocal.reponse_llm(historique)
            historique.append({"role": "assistant", "content": reponse})
            logger.info("AGENT : %s", reponse)
            await parler(ws, reponse)
            delai = (time.perf_counter() - fin_parole) * 1000
            logger.info(
                "Tour complet : %d ms entre le dernier mot compris et l'envoi de la voix",
                delai,
            )
        except Exception:
            logger.exception("Le tour a echoue, l'agent continue")


@router.post("/telnyx/voice")
async def voice_webhook(evenement: dict):
    data = evenement.get("data", {})
    type_evenement = data.get("event_type")
    payload = data.get("payload", {})
    logger.info("Evenement appel : %s", type_evenement)
    if type_evenement == "call.initiated" and payload.get("direction") == "incoming":
        await repondre(payload["call_control_id"])
    return {"received": True}


def enregistrer(audio: bytes) -> Path:
    ENREGISTREMENTS.mkdir(exist_ok=True)
    chemin = ENREGISTREMENTS / f"appel-{datetime.now():%Y%m%d-%H%M%S}.wav"
    with wave.open(str(chemin), "wb") as fichier:
        fichier.setnchannels(1)
        fichier.setsampwidth(2)  # 16 bits
        fichier.setframerate(8000)  # 8 kHz, qualité téléphone
        fichier.writeframes(ulaw_vers_pcm16(audio))
    return chemin


@router.websocket("/telnyx/media")
async def media_stream(ws: WebSocket):
    await ws.accept()
    audio = bytearray()
    tampon = bytearray()
    paquets = 0
    stt = None
    lecteur = None
    tours = asyncio.Queue()
    agent = None
    try:
        while True:
            message = await ws.receive_json()
            evenement = message.get("event")
            if evenement == "start":
                logger.info("Stream demarre : %s", message["start"].get("media_format"))
                agent = asyncio.create_task(boucle_agent(ws, tours))
                try:
                    stt = await ouvrir_stt()
                    lecteur = asyncio.create_task(lire_transcriptions(stt, tours))
                except (OSError, websockets.WebSocketException) as erreur:
                    logger.warning("Transcription indisponible : %s", erreur)
            elif evenement == "media":
                morceau = base64.b64decode(message["media"]["payload"])
                paquets += 1
                audio += morceau
                tampon += morceau
                if stt and len(tampon) >= TAILLE_ENVOI:
                    try:
                        await stt.send(bytes(tampon))
                    except websockets.ConnectionClosed:
                        logger.warning("Transcription coupee")
                        stt = None
                    tampon.clear()
            elif evenement == "stop":
                logger.info("Stream arrete par Telnyx")
                break
            else:
                logger.info("Evenement stream : %s", evenement)
    except WebSocketDisconnect:
        logger.info("WebSocket fermee")
    if agent:
        agent.cancel()
    logger.info(
        "%d paquets, %d octets, %.1f s d'audio", paquets, len(audio), len(audio) / 8000
    )
    if audio:
        logger.info("Audio enregistre : %s", enregistrer(bytes(audio)))
    if stt:
        await terminer_stt(stt, lecteur, tampon)


async def terminer_stt(stt, lecteur, tampon: bytearray) -> None:
    """Envoie le reste du son, demande les derniers résultats, puis ferme."""
    try:
        if tampon:
            await stt.send(bytes(tampon))
        await stt.send(json.dumps({"type": "CloseStream"}))
        finales = await asyncio.wait_for(lecteur, timeout=5)
        logger.info("Transcription complete : %s", " ".join(finales))
    except (TimeoutError, websockets.ConnectionClosed):
        lecteur.cancel()
        logger.warning("Fin de transcription incomplete")
    finally:
        await stt.close()
