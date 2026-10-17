from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import main
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def agenda_vide():
    main.RESERVATIONS.clear()
    main.RESERVATION_PAR_TEL.clear()


def prochain(jour_semaine):
    jour = date.today() + timedelta(days=7)
    while jour.weekday() != jour_semaine:
        jour += timedelta(days=1)
    return jour


def reserver(jour, phone="+15551234567", heure=None):
    demande = {
        "caller_name": "John Miller",
        "phone": phone,
        "preferred_date": jour.isoformat(),
    }
    if heure is not None:
        demande["preferred_hour"] = heure
    return client.post("/consultations", json=demande)


def test_intake_renvoie_un_numero_de_dossier():
    r = client.post(
        "/intake",
        json={
            "caller_name": "John Miller",
            "phone": "+15551234567",
            "accident_date": (date.today() - timedelta(days=2)).isoformat(),
            "accident_type": "car",
            "injured": True,
            "accident_location": "I-95, Bronx",
            "injury_details": "neck pain",
        },
    )
    assert r.status_code == 200
    case_id = r.json()["case_id"]
    assert main.DOSSIERS[case_id]["accident_location"] == "I-95, Bronx"


def test_reserve_le_premier_creneau_du_jour():
    lundi = prochain(0)
    r = reserver(lundi)
    assert r.json()["status"] == "booked"
    creneau = datetime.fromisoformat(r.json()["slot"])
    assert creneau.date() == lundi
    assert creneau.hour == 10


def test_deux_personnes_n_ont_pas_le_meme_creneau():
    lundi = prochain(0)
    premier = reserver(lundi, phone="+15550000001").json()["slot"]
    second = reserver(lundi, phone="+15550000002").json()["slot"]
    assert premier != second
    assert datetime.fromisoformat(second).hour == 14


def test_meme_appelant_deplace_sa_reservation():
    lundi = prochain(0)
    reserver(lundi)
    data = reserver(lundi + timedelta(days=1)).json()
    assert data["status"] == "rescheduled"
    assert data["previous_slot_spoken"] is not None
    assert len(main.RESERVATIONS) == 1


def test_meme_numero_ecrit_differemment():
    lundi = prochain(0)
    reserver(lundi, phone="+1 555 123 4567")
    reserver(lundi, phone="+15551234567")
    assert len(main.RESERVATIONS) == 1


def test_heure_minimum():
    lundi = prochain(0)
    creneau = datetime.fromisoformat(reserver(lundi, heure=15).json()["slot"])
    assert creneau.hour == 16


def test_samedi_reporte_au_lundi():
    samedi = prochain(5)
    creneau = datetime.fromisoformat(reserver(samedi).json()["slot"])
    assert creneau.date() == samedi + timedelta(days=2)


def test_date_manquante_refusee():
    r = client.post("/consultations", json={"caller_name": "John", "phone": "+1555"})
    assert r.status_code == 422
