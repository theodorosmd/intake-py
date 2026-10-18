"""Le même contrat pour les deux stockages. Postgres n'est testé que si TEST_DATABASE_URL est
défini (la CI démarre une vraie base ; en local, sans base, ces cas sont sautés)."""

import os
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from stockage import CreneauPris, Memoire, Postgres

NEW_YORK = ZoneInfo("America/New_York")
URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(params=["memoire", "postgres"])
def stockage(request):
    if request.param == "memoire":
        s = Memoire()
    elif URL:
        s = Postgres(URL)
    else:
        pytest.skip("TEST_DATABASE_URL absent")
    s.preparer()
    s.vider()
    yield s
    s.vider()
    s.fermer()


def creneau(jours: int, heure: int = 10) -> datetime:
    jour = datetime.now(NEW_YORK).date() + timedelta(days=jours)
    return datetime(jour.year, jour.month, jour.day, heure, tzinfo=NEW_YORK)


def test_dossiers_du_plus_recent_au_plus_ancien(stockage):
    stockage.enregistrer_dossier("a", {"caller_name": "John"})
    stockage.enregistrer_dossier("b", {"caller_name": "Ann"})
    assert [case_id for case_id, _ in stockage.dossiers()] == ["b", "a"]
    assert stockage.dossier("a") == {"caller_name": "John"}
    assert stockage.dossier("absent") is None


def test_reserver_puis_deplacer_le_meme_numero(stockage):
    assert stockage.reserver("15551234567", "John", creneau(3)) is None
    ancien = stockage.reserver("15551234567", "John", creneau(4))
    assert ancien == creneau(3)
    assert stockage.rendez_vous() == [(creneau(4), "John")]
    assert stockage.creneaux_pris() == {creneau(4)}


def test_un_creneau_pris_est_refuse(stockage):
    stockage.reserver("15551234567", "John", creneau(3))
    with pytest.raises(CreneauPris):
        stockage.reserver("15559876543", "Ann", creneau(3))
    # Le refus ne doit rien avoir défait : John garde son rendez-vous
    assert stockage.rendez_vous() == [(creneau(3), "John")]


def test_un_refus_ne_supprime_pas_l_ancien_rendez_vous(stockage):
    stockage.reserver("15551234567", "John", creneau(3))
    stockage.reserver("15559876543", "Ann", creneau(4))
    with pytest.raises(CreneauPris):
        stockage.reserver("15559876543", "Ann", creneau(3))
    assert stockage.rendez_vous() == [(creneau(3), "John"), (creneau(4), "Ann")]


def test_deux_reservations_simultanees_un_seul_gagnant(stockage):
    resultats: list[str] = []

    def reserver(tel: str) -> None:
        try:
            stockage.reserver(tel, tel, creneau(5))
            resultats.append("ok")
        except CreneauPris:
            resultats.append("pris")

    fils = [threading.Thread(target=reserver, args=(f"1555000000{i}",)) for i in range(4)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    assert sorted(resultats) == ["ok", "pris", "pris", "pris"]
    assert len(stockage.rendez_vous()) == 1


def test_les_resumes_sont_remplaces_par_appel(stockage):
    stockage.enregistrer_resume("v3:a", {"call_id": "v3:a", "injured": False})
    stockage.enregistrer_resume("v3:a", {"call_id": "v3:a", "injured": True})
    assert stockage.resumes() == [{"call_id": "v3:a", "injured": True}]


def test_les_dates_reviennent_a_l_heure_de_new_york(stockage):
    stockage.reserver("15551234567", "John", creneau(3, 14))
    (slot, _), = stockage.rendez_vous()
    assert slot.astimezone(NEW_YORK).hour == 14
    assert slot.utcoffset() == creneau(3, 14).utcoffset()
