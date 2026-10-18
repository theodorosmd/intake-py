import logging

import pytest

import confidentialite as c


@pytest.fixture(autouse=True)
def oublier_les_noms():
    c.NOMS_CONNUS.clear()
    yield
    c.NOMS_CONNUS.clear()


def test_numeros_masques_sous_toutes_les_formes():
    assert c.masquer("My number is 555-010-0045.") == "My number is [TEL]."
    assert c.masquer("APPELANT : 0033612345678.") == "APPELANT : [TEL]."
    assert c.masquer("de +15551234567 vers +18005550199") == "de [TEL] vers [TEL]"
    assert c.masquer("Zero zero three three six sixty one zero two") == "[TEL]"


def test_les_mesures_et_les_dates_ne_sont_pas_des_numeros():
    for ligne in (
        "10575 paquets, 1692000 octets, 211.5 s d'audio",
        "Consultation reservee le 2026-10-20T10:00:00-04:00",
        "Tour 1 : premier son apres 3546 ms (attente fin de phrase 491)",
        "Audio enregistre : enregistrements/appel-20261017-213930.wav",
    ):
        assert c.masquer(ligne) == ligne


def test_le_nom_appris_a_la_presentation_est_masque_ensuite():
    assert c.masquer("APPELANT : Hello? I'm Tilda.") == "APPELANT : Hello? I'm [NOM]."
    assert c.masquer("AGENT : Hi Tilda, can you spell it?") == "AGENT : Hi [NOM], can you spell it?"


def test_le_nom_d_un_outil_est_masque_avec_ses_parties():
    ligne = 'OUTIL save_intake : 13 ms -> {"caller_name": "Linh Nguyen", "urgent": true}'
    assert "Nguyen" not in c.masquer(ligne)
    assert c.masquer("AGENT : Thank you, Linh.") == "AGENT : Thank you, [NOM]."


def test_les_mots_courants_ne_deviennent_pas_des_noms():
    c.masquer("It's Tuesday. I'm sorry. This is New York.")
    assert c.masquer("Tuesday at 10 AM in New York") == "Tuesday at 10 AM in New York"


def test_epele_et_email():
    assert c.masquer("It's T-H-E-O-D-O-R-E.") == "It's [EPELE]."
    assert c.masquer("mail me at theo@example.com") == "mail me at [EMAIL]"


def test_toutes_les_lignes_de_journal_sont_masquees(caplog, monkeypatch):
    monkeypatch.delenv("LOGS_EN_CLAIR", raising=False)
    origine = logging.getLogRecordFactory()
    try:
        c.installer()
        with caplog.at_level(logging.INFO):
            logging.getLogger("uvicorn.error").info("APPELANT : %s", "I'm Maria, 555-010-0001")
        assert caplog.records[-1].getMessage() == "APPELANT : I'm [NOM], [TEL]"
    finally:
        logging.setLogRecordFactory(origine)


def test_les_lignes_d_acces_uvicorn_gardent_leurs_arguments(monkeypatch):
    monkeypatch.delenv("LOGS_EN_CLAIR", raising=False)
    origine = logging.getLogRecordFactory()
    try:
        c.installer()
        record = logging.getLogger("uvicorn.access").makeRecord(
            "uvicorn.access", logging.INFO, "", 0,
            '%s - "%s %s HTTP/%s" %d',
            ("10.42.0.1:54336", "GET", "/health", "1.1", 200), None,
        )
        assert len(record.args) == 5  # l'AccessFormatter d'uvicorn en dépend
        assert record.getMessage() == '10.42.0.1:54336 - "GET /health HTTP/1.1" 200'
    finally:
        logging.setLogRecordFactory(origine)
