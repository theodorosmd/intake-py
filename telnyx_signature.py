"""Vérifie qu'un webhook vient bien de Telnyx (signature Ed25519).

Telnyx signe « {horodatage}|{corps brut} » avec une clé privée à lui ; on vérifie avec la clé
publique de notre compte (portail Telnyx, Keys & Credentials, Public Key). Sans cette
vérification, n'importe qui peut poster un faux événement sur l'URL publique.

Variables : TELNYX_PUBLIC_KEY (obligatoire, base64). WEBHOOK_SIGNATURE_OFF=1 désactive la
vérification pour déboguer en local uniquement.
"""

import base64
import binascii
import json
import logging
import os
import time

from fastapi import HTTPException, Request
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

logger = logging.getLogger("uvicorn.error")

TOLERANCE_SECONDES = 300  # au-delà, la requête est rejetée (rejeu d'une capture ancienne)


class SignatureInvalide(Exception):
    """La requête ne prouve pas qu'elle vient de Telnyx."""


def verifier(
    corps: bytes,
    signature: str | None,
    horodatage: str | None,
    cle_publique: str,
    maintenant: float | None = None,
) -> None:
    """Lève SignatureInvalide si la signature, la clé ou l'horodatage ne conviennent pas."""
    if not signature or not horodatage:
        raise SignatureInvalide("en-têtes de signature absents")
    try:
        age = abs((maintenant if maintenant is not None else time.time()) - int(horodatage))
    except ValueError as erreur:
        raise SignatureInvalide("horodatage illisible") from erreur
    if age > TOLERANCE_SECONDES:
        raise SignatureInvalide("horodatage trop ancien")
    try:
        cle = VerifyKey(base64.b64decode(cle_publique))
        cle.verify(horodatage.encode() + b"|" + corps, base64.b64decode(signature))
    except (BadSignatureError, binascii.Error, ValueError) as erreur:
        raise SignatureInvalide("signature invalide") from erreur


async def evenement_verifie(request: Request) -> dict:
    """Dépendance FastAPI : renvoie l'événement JSON, seulement si Telnyx l'a signé."""
    corps = await request.body()
    if os.getenv("WEBHOOK_SIGNATURE_OFF") != "1":
        cle = os.getenv("TELNYX_PUBLIC_KEY")
        if not cle:
            logger.error("TELNYX_PUBLIC_KEY absente : webhook refusé")
            raise HTTPException(status_code=503, detail="vérification non configurée")
        try:
            verifier(
                corps,
                request.headers.get("telnyx-signature-ed25519"),
                request.headers.get("telnyx-timestamp"),
                cle,
            )
        except SignatureInvalide as erreur:
            logger.warning("Webhook refusé : %s", erreur)
            raise HTTPException(status_code=401, detail="signature invalide") from erreur
    try:
        return json.loads(corps)
    except ValueError as erreur:
        raise HTTPException(status_code=400, detail="JSON invalide") from erreur
