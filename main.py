import logging
import os
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="call-gateway")
logger = logging.getLogger("uvicorn.error")

AccidentType = Literal["car", "slip_and_fall", "work", "other"]
URGENCE_JOURS = 14
WORKER_URL = os.environ.get("WORKER_URL", "http://127.0.0.1:8001")
NEW_YORK = ZoneInfo("America/New_York")
HEURES_CRENEAUX = [10, 14, 16]

DOSSIERS: dict[str, dict] = {}
RESERVATIONS: dict[datetime, str] = {}


class IntakeCase(BaseModel):
    caller_name: str
    phone: str
    accident_date: date
    accident_type: AccidentType
    injured: bool
    has_insurance: bool | None = None
    accident_location: str | None = None
    injury_details: str | None = None


class CallEnded(BaseModel):
    call_id: str
    transcript: str = Field(min_length=1)


class ConsultationRequest(BaseModel):
    caller_name: str
    phone: str
    preferred_date: date


def is_urgent(c: IntakeCase) -> bool:
    jours_ecoules = (date.today() - c.accident_date).days
    return c.injured and jours_ecoules < URGENCE_JOURS


def premier_creneau_libre(a_partir_du: date) -> datetime:
    maintenant = datetime.now(NEW_YORK)
    jour = max(a_partir_du, maintenant.date())
    for _ in range(30):
        if jour.weekday() < 5:
            for heure in HEURES_CRENEAUX:
                creneau = datetime(
                    jour.year, jour.month, jour.day, heure, tzinfo=NEW_YORK
                )
                if creneau > maintenant and creneau not in RESERVATIONS:
                    return creneau
        jour += timedelta(days=1)
    raise HTTPException(status_code=409, detail="Aucun creneau libre sur 30 jours")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/intake")
def receive_intake(case: IntakeCase):
    urgent = is_urgent(case)
    case_id = uuid.uuid4().hex[:8]
    DOSSIERS[case_id] = case.model_dump(mode="json")
    logger.info("Dossier %s enregistre | urgent=%s", case_id, urgent)
    return {
        "status": "received",
        "case_id": case_id,
        "caller_name": case.caller_name,
        "urgent": urgent,
    }


@app.post("/consultations")
def book_consultation(demande: ConsultationRequest):
    creneau = premier_creneau_libre(demande.preferred_date)
    RESERVATIONS[creneau] = demande.caller_name
    logger.info("Consultation reservee le %s", creneau.isoformat())
    return {
        "status": "booked",
        "slot": creneau.isoformat(),
        "slot_spoken": creneau.strftime("%A, %B %d at %I %p").replace(" 0", " "),
    }


@app.post("/call-ended")
def call_ended(c: CallEnded):
    debut = time.perf_counter()
    try:
        reponse = httpx.post(
            f"{WORKER_URL}/summarize",
            json={"call_id": c.call_id, "text": c.transcript},
            timeout=30,
        )
        reponse.raise_for_status()
    except httpx.HTTPError as erreur:
        logger.warning("summary-worker injoignable : %r", erreur)
        raise HTTPException(
            status_code=502, detail="summary-worker indisponible"
        ) from erreur
    duree_ms = round((time.perf_counter() - debut) * 1000)
    return {
        "status": "summarized",
        "call_id": c.call_id,
        "summary": reponse.json(),
        "worker_ms": duree_ms,
    }


@app.post("/telnyx/webhook")
def telnyx_webhook(event: dict):
    data = event.get("data", {})
    payload = data.get("payload", {})
    logger.info(
        "Telnyx %s | de %s vers %s | call_control_id=%s",
        data.get("event_type"),
        payload.get("from"),
        payload.get("to"),
        payload.get("call_control_id"),
    )
    return {"received": True}
