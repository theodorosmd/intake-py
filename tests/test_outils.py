from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import main
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def agenda_vide():
    main.RESERVATIONS.clear()


def prochain(jour_semaine):
    jour = date.today() + timedelta(days=7)
    while jour.weekday() != jour_semaine:
        jour += timedelta(days=1)
    return jour


def reserver(jour):
    return client.post(
        "/consultations",
        json={
            "caller_name": "John Miller",
            "phone": "+15551234567",
            "preferred_date": jour.isoformat(),
        },
    )


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
    creneau = datetime.fromisoformat(reserver(lundi).json()["slot"])
    assert creneau.date() == lundi
    assert creneau.hour == 10


def test_deux_reservations_ne_prennent_pas_le_meme_creneau():
    lundi = prochain(0)
    premier = reserver(lundi).json()["slot"]
    second = reserver(lundi).json()["slot"]
    assert premier != second
    assert datetime.fromisoformat(second).hour == 14


def test_samedi_reporte_au_lundi():
    samedi = prochain(5)
    creneau = datetime.fromisoformat(reserver(samedi).json()["slot"])
    assert creneau.date() == samedi + timedelta(days=2)


def test_date_manquante_refusee():
    r = client.post("/consultations", json={"caller_name": "John", "phone": "+1555"})
    assert r.status_code == 422
