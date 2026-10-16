import logging
import os
import time
from datetime import date
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="call-gateway")

AccidentType = Literal["car", "slip_and_fall", "work", "other"]
URGENCE_JOURS = 14
WORKER_URL = os.environ.get("WORKER_URL", "http://127.0.0.1:8001")


class IntakeCase(BaseModel):
    caller_name: str
    phone: str
    accident_date: date
    accident_type: AccidentType
    injured: bool
    has_insurance: bool | None = None


class CallEnded(BaseModel):
    call_id: str
    transcript: str = Field(min_length=1)


def is_urgent(c: IntakeCase) -> bool:
    jours_ecoules = (date.today() - c.accident_date).days
    return c.injured and jours_ecoules < URGENCE_JOURS


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/intake")
def receive_intake(case: IntakeCase):
    urgent = is_urgent(case)
    return {"status": "received", "caller_name": case.caller_name, "urgent": urgent}


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
        logging.warning("summary-worker injoignable : %r", erreur)
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
