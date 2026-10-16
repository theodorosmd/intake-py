from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ValidationError

AccidentType = Literal["car", "slip_and_fall", "work", "other"]


class IntakeCase(BaseModel):
    caller_name: str
    phone: str
    accident_date: date
    accident_type: AccidentType
    injured: bool
    has_insurance: Optional[bool] = None


bon = {
    "caller_name": "John Smith",
    "phone": "+15551234567",
    "accident_date": "2026-10-14",
    "accident_type": "car",
    "injured": True,
}

dossier = IntakeCase.model_validate(bon)
print(dossier)
print(dossier.accident_date, type(dossier.accident_date))

mauvais = {
    "caller_name": "Mary Jones",
    "accident_date": "pas une date",
    "accident_type": "boat",
    "injured": "peut-être",
}

try:
    IntakeCase.model_validate(mauvais)
except ValidationError as erreur:
    print(erreur)
