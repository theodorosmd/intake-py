from datetime import date, timedelta

from fastapi.testclient import TestClient

from main import IntakeCase, app, is_urgent

client = TestClient(app)


def il_y_a(jours: int) -> str:
    """Date d'il y a N jours, au format texte '2026-10-14'."""
    return (date.today() - timedelta(days=jours)).isoformat()


def dossier(**changements):
    """Un dossier valide, dont on peut modifier certains champs."""
    base = {
        "caller_name": "John Smith",
        "phone": "+15551234567",
        "accident_date": il_y_a(2),
        "accident_type": "car",
        "injured": True,
    }
    base.update(changements)
    return base


# --- Tests de l'API ---


def test_health():
    reponse = client.get("/health")
    assert reponse.status_code == 200
    assert reponse.json() == {"status": "ok"}


def test_dossier_valide_et_urgent():
    reponse = client.post("/intake", json=dossier())
    assert reponse.status_code == 200
    assert reponse.json()["urgent"] is True


def test_accident_ancien_pas_urgent():
    reponse = client.post("/intake", json=dossier(accident_date=il_y_a(60)))
    assert reponse.status_code == 200
    assert reponse.json()["urgent"] is False


def test_telephone_manquant_refuse():
    donnees = dossier()
    del donnees["phone"]
    reponse = client.post("/intake", json=donnees)
    assert reponse.status_code == 422


def test_type_accident_invalide_refuse():
    reponse = client.post("/intake", json=dossier(accident_type="boat"))
    assert reponse.status_code == 422


# --- Tests de la règle d'urgence ---


def test_limite_13_jours_urgent():
    c = IntakeCase.model_validate(dossier(accident_date=il_y_a(13)))
    assert is_urgent(c) is True


def test_limite_14_jours_pas_urgent():
    c = IntakeCase.model_validate(dossier(accident_date=il_y_a(14)))
    assert is_urgent(c) is False


# --- À toi d'écrire ---


def test_pas_blesse_pas_urgent():
    c = IntakeCase.model_validate(dossier(injured=False, accident_date=il_y_a(1)))
    assert is_urgent(c) is False
