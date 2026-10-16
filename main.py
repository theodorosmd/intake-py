from datetime import date
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="call-gateway")

AccidentType = Literal["car", "slip_and_fall", "work", "other"]
URGENCE_JOURS = 14


class IntakeCase(BaseModel):
    caller_name: str
    phone: str
    accident_date: date
    accident_type: AccidentType
    injured: bool
    has_insurance: bool | None = None


def is_urgent(c: IntakeCase) -> bool:
    jours_ecoules = (date.today() - c.accident_date).days
    return c.injured and jours_ecoules < URGENCE_JOURS


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/intake")
def receive_intake(case: IntakeCase):
    urgent = is_urgent(case)
    return {
        "status": "received",
        "caller_name": case.caller_name,
        "urgent": urgent,
    }
