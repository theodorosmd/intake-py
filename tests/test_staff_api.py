"""API du tableau de bord : jeton, droits par rôle, journal du dossier, rendez-vous."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import main

JETON = "jeton-staff"
client = TestClient(main.app)


def entetes(role: str = "staff", nom: str = "Maria Lopez") -> dict:
    return {"Authorization": f"Bearer {JETON}", "X-Staff-User": nom, "X-Staff-Role": role}


AVOCAT = entetes("attorney", "Dana Reyes")
SECRETARIAT = entetes()


@pytest.fixture(autouse=True)
def donnees_vides(monkeypatch):
    main.STOCKAGE.vider()
    monkeypatch.setenv("STAFF_API_TOKEN", JETON)


def il_y_a(jours: int) -> str:
    return (date.today() - timedelta(days=jours)).isoformat()


def lundi_prochain() -> date:
    jour = date.today() + timedelta(days=7)
    while jour.weekday() != 0:
        jour += timedelta(days=1)
    return jour


def nouveau_dossier(**changements) -> str:
    dossier = {
        "caller_name": "John Miller",
        "phone": "+1 (555) 123-4567",
        "accident_date": il_y_a(3),
        "accident_type": "car",
        "injured": True,
    } | changements
    reponse = client.post("/intake", json=dossier)
    assert reponse.status_code == 200
    return reponse.json()["case_id"]


# --- Accès ---


def test_sans_jeton_configure_tout_est_ferme(monkeypatch):
    monkeypatch.delenv("STAFF_API_TOKEN")
    assert client.get("/staff/cases", headers=SECRETARIAT).status_code == 503


def test_mauvais_jeton_refuse():
    reponse = client.get("/staff/cases", headers={"Authorization": "Bearer faux"})
    assert reponse.status_code == 401


def test_une_ecriture_sans_identite_est_refusee():
    case_id = nouveau_dossier()
    reponse = client.patch(
        f"/staff/cases/{case_id}", json={"status": "contacted"},
        headers={"Authorization": f"Bearer {JETON}"},
    )
    assert reponse.status_code == 422


# --- Lecture ---


def test_liste_avec_urgence_et_etape():
    urgent = nouveau_dossier()
    ancien = nouveau_dossier(caller_name="Ann Lee", phone="+15559876543", accident_date=il_y_a(40))
    cas = {c["case_id"]: c for c in client.get("/staff/cases", headers=SECRETARIAT).json()}
    assert cas[urgent]["urgent"] is True and cas[urgent]["days_since_accident"] == 3
    assert cas[ancien]["urgent"] is False
    assert cas[urgent]["status"] == "new" and cas[urgent]["assignee"] is None
    assert cas[urgent]["consultation"] is None


def test_dossier_inconnu():
    assert client.get("/staff/cases/absent", headers=SECRETARIAT).status_code == 404


def test_le_resume_et_la_transcription_de_l_appel_sont_rattaches():
    case_id = nouveau_dossier(call_id="v3:abc")
    main.STOCKAGE.enregistrer_resume(
        "v3:abc", {"call_id": "v3:abc", "injured": True, "transcript": "I was rear-ended"}
    )
    cas = client.get(f"/staff/cases/{case_id}", headers=SECRETARIAT).json()
    assert cas["has_call_summary"] is True
    assert cas["call_summary"]["transcript"] == "I was rear-ended"
    (appel,) = client.get("/staff/calls", headers=SECRETARIAT).json()
    assert appel["case_id"] == case_id


# --- Étapes et droits ---


def test_le_secretariat_fait_avancer_et_le_journal_le_garde():
    case_id = nouveau_dossier()
    reponse = client.patch(
        f"/staff/cases/{case_id}", json={"status": "contacted", "assignee": "Dana Reyes"},
        headers=SECRETARIAT,
    )
    assert reponse.status_code == 200
    cas = reponse.json()
    assert (cas["status"], cas["assignee"]) == ("contacted", "Dana Reyes")
    assert [n["body"] for n in cas["notes"]] == ["Status: New → Contacted", "Assigned to Dana Reyes"]
    assert all(n["author"] == "Maria Lopez" and n["kind"] == "event" for n in cas["notes"])


def test_seul_un_avocat_accepte_ou_refuse():
    case_id = nouveau_dossier()
    refus = client.patch(f"/staff/cases/{case_id}", json={"status": "retained"}, headers=SECRETARIAT)
    assert refus.status_code == 403
    accord = client.patch(f"/staff/cases/{case_id}", json={"status": "retained"}, headers=AVOCAT)
    assert accord.status_code == 200 and accord.json()["status"] == "retained"


def test_une_etape_inconnue_est_refusee():
    case_id = nouveau_dossier()
    reponse = client.patch(f"/staff/cases/{case_id}", json={"status": "won"}, headers=AVOCAT)
    assert reponse.status_code == 422


def test_rien_ne_change_rien_n_est_journalise():
    case_id = nouveau_dossier()
    cas = client.patch(f"/staff/cases/{case_id}", json={"status": "new"}, headers=SECRETARIAT).json()
    assert cas["notes"] == []


def test_retirer_l_avocat():
    case_id = nouveau_dossier()
    client.patch(f"/staff/cases/{case_id}", json={"assignee": "Dana Reyes"}, headers=SECRETARIAT)
    cas = client.patch(f"/staff/cases/{case_id}", json={"assignee": ""}, headers=SECRETARIAT).json()
    assert cas["assignee"] is None
    assert cas["notes"][-1]["body"] == "Unassigned"


def test_ajouter_une_note():
    case_id = nouveau_dossier()
    reponse = client.post(
        f"/staff/cases/{case_id}/notes", json={"body": "  Police report requested  "}, headers=AVOCAT
    )
    assert reponse.status_code == 201
    assert reponse.json()["body"] == "Police report requested"
    assert client.post(f"/staff/cases/{case_id}/notes", json={"body": ""}, headers=AVOCAT).status_code == 422


# --- Rendez-vous ---


def test_reserver_depuis_le_dossier_puis_annuler():
    case_id = nouveau_dossier()
    reponse = client.post(
        f"/staff/cases/{case_id}/consultation",
        json={"preferred_date": lundi_prochain().isoformat(), "preferred_hour": 14},
        headers=SECRETARIAT,
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["booking"]["status"] == "booked"
    assert corps["case"]["status"] == "consult_booked"
    assert corps["case"]["consultation"]["slot"].startswith(f"{lundi_prochain().isoformat()}T14:00")
    (rdv,) = client.get("/staff/consultations", headers=SECRETARIAT).json()
    assert rdv["case_id"] == case_id

    cas = client.delete(f"/staff/cases/{case_id}/consultation", headers=SECRETARIAT).json()
    assert cas["consultation"] is None and cas["status"] == "contacted"
    assert client.delete(f"/staff/cases/{case_id}/consultation", headers=SECRETARIAT).status_code == 404


def test_le_rendez_vous_pris_au_telephone_apparait_sur_le_dossier():
    case_id = nouveau_dossier()
    client.post("/consultations", json={
        "caller_name": "John Miller", "phone": "5551234567", "preferred_date": lundi_prochain().isoformat(),
    })
    cas = client.get(f"/staff/cases/{case_id}", headers=SECRETARIAT).json()
    assert cas["consultation"] is not None


def test_vue_d_ensemble():
    a = nouveau_dossier()
    nouveau_dossier(caller_name="Ann Lee", phone="+15559876543", accident_date=il_y_a(40))
    client.patch(f"/staff/cases/{a}", json={"assignee": "Dana Reyes"}, headers=SECRETARIAT)
    vue = client.get("/staff/overview", headers=SECRETARIAT).json()
    assert vue["total_cases"] == 2
    assert vue["by_status"]["new"] == 2
    assert vue["urgent_open"] == 1
    assert vue["unassigned_open"] == 1
    assert sum(vue["cases_by_day"].values()) == 2


def test_cle_telephone_sans_indicatif():
    from staff_api import cle_tel

    assert cle_tel("+1 (555) 123-4567") == cle_tel("555.123.4567") == "5551234567"
    assert cle_tel("+44 20 7946 0958") == "442079460958"
