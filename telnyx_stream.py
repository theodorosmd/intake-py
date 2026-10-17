"""Étape 2 bis : décrocher un appel par code et recevoir son audio en direct."""

import base64
import logging
import os
import wave
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

load_dotenv()

logger = logging.getLogger("uvicorn.error")
router = APIRouter()

TELNYX_API = "https://api.telnyx.com/v2"
ENREGISTREMENTS = Path("enregistrements")


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
    paquets = 0
    try:
        while True:
            message = await ws.receive_json()
            evenement = message.get("event")
            if evenement == "start":
                logger.info("Stream demarre : %s", message["start"].get("media_format"))
            elif evenement == "media":
                paquets += 1
                audio += base64.b64decode(message["media"]["payload"])
            elif evenement == "stop":
                logger.info("Stream arrete par Telnyx")
                break
            else:
                logger.info("Evenement stream : %s", evenement)
    except WebSocketDisconnect:
        logger.info("WebSocket fermee")
    logger.info("%d paquets, %d octets, %.1f s d'audio", paquets, len(audio), len(audio) / 8000)
    if audio:
        logger.info("Audio enregistre : %s", enregistrer(bytes(audio)))
