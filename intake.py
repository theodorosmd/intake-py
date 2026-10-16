from dataclasses import dataclass
from datetime import date
from typing import Literal

AccidentType = Literal["car", "slip_and_fall", "work", "other"]
URGENCE_JOURS = 14


@dataclass
class IntakeCase:
    caller_name: str
    phone: str
    accident_date: date
    accident_type: AccidentType
    injured: bool
    has_insurance: bool | None = None


def is_urgent(c: IntakeCase) -> bool:
    jours_ecoules = (date.today() - c.accident_date).days
    return c.injured and jours_ecoules < URGENCE_JOURS


urgent = IntakeCase("John Smith", "+15551234567", date(2026, 10, 14), "car", True)
pas_urgent = IntakeCase(
    "Mary Jones", "+15559876543", date(2026, 8, 1), "slip_and_fall", True
)

print(is_urgent(urgent))  # attendu : True
print(is_urgent(pas_urgent))  # attendu : False
