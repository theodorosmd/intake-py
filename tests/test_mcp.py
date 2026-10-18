import asyncio
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from mcp import Client

import main

JETON = "jeton-de-test"
ENTETES = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def donnees_vides(monkeypatch):
    main.STOCKAGE.vider()
    monkeypatch.setenv("MCP_TOKEN", JETON)


def lundi_prochain() -> date:
    jour = date.today() + timedelta(days=7)
    while jour.weekday() != 0:
        jour += timedelta(days=1)
    return jour


def appeler(outil: str, arguments: dict | None = None):
    async def _appel():
        async with Client(main.MCP) as client:
            return await client.call_tool(outil, arguments or {})

    return asyncio.run(_appel())


def test_les_six_outils_sont_exposes():
    async def _liste():
        async with Client(main.MCP) as client:
            return await client.list_tools()

    noms = {outil.name for outil in asyncio.run(_liste()).tools}
    assert noms == {
        "list_intake_cases",
        "get_intake_case",
        "list_call_summaries",
        "list_consultations",
        "find_next_slot",
        "book_consultation",
    }


def test_les_dossiers_du_telephone_sont_lisibles_et_filtrables():
    main.STOCKAGE.enregistrer_dossier("a1", {
        "caller_name": "John Miller",
        "phone": "+15551234567",
        "accident_date": date.today().isoformat(),
        "accident_type": "car",
        "injured": True,
    })
    main.STOCKAGE.enregistrer_dossier("b2", {
        "caller_name": "Ann Lee",
        "phone": "+15559876543",
        "accident_date": (date.today() - timedelta(days=60)).isoformat(),
        "accident_type": "work",
        "injured": False,
    })
    tous = appeler("list_intake_cases").structured_content["result"]
    assert [c["case_id"] for c in tous] == ["b2", "a1"]  # le plus récent d'abord
    urgents = appeler("list_intake_cases", {"urgent_only": True}).structured_content["result"]
    assert [c["case_id"] for c in urgents] == ["a1"]


def test_un_dossier_inconnu_renvoie_une_erreur():
    resultat = appeler("get_intake_case", {"case_id": "absent"})
    assert resultat.is_error


def test_reserver_puis_lister_les_rendez_vous():
    jour = lundi_prochain()
    reservation = appeler(
        "book_consultation",
        {"caller_name": "John Miller", "phone": "+15551234567", "preferred_date": jour.isoformat()},
    ).structured_content
    assert reservation["status"] == "booked"
    rendez_vous = appeler("list_consultations").structured_content["result"]
    assert rendez_vous == [{"slot": reservation["slot"], "caller_name": "John Miller"}]
    # Le créneau suivant n'est plus celui qui vient d'être pris
    libre = appeler("find_next_slot", {"preferred_date": jour.isoformat()}).structured_content
    assert libre["slot"] != reservation["slot"]


def test_les_resumes_d_appels_sont_lisibles():
    main.STOCKAGE.enregistrer_resume("v3:abc", {"call_id": "v3:abc", "injured": True, "accident_type": "car"})
    resumes = appeler("list_call_summaries").structured_content["result"]
    assert resumes[0]["call_id"] == "v3:abc"


def test_http_sans_jeton_refuse():
    with TestClient(main.app) as client:
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=ENTETES)
    assert r.status_code == 401


def test_http_mauvais_jeton_refuse():
    with TestClient(main.app) as client:
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={**ENTETES, "Authorization": "Bearer autre"},
        )
    assert r.status_code == 401


def test_http_sans_jeton_configure_reste_ferme(monkeypatch):
    monkeypatch.delenv("MCP_TOKEN")
    with TestClient(main.app) as client:
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={**ENTETES, "Authorization": "Bearer "},
        )
    assert r.status_code == 503


def test_http_avec_jeton_repond():
    with TestClient(main.app) as client:
        r = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
            headers={**ENTETES, "Authorization": f"Bearer {JETON}"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["serverInfo"]["name"] == "pi-intake"
