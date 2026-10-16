from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

EXEMPLE = {
    "data": {
        "event_type": "call.initiated",
        "payload": {
            "call_control_id": "v3:test",
            "from": "+15551234567",
            "to": "+15557654321",
            "direction": "incoming",
        },
    }
}


def test_webhook_telnyx_accepte_un_appel():
    r = client.post("/telnyx/webhook", json=EXEMPLE)
    assert r.status_code == 200
    assert r.json() == {"received": True}


def test_webhook_telnyx_ne_plante_pas_sur_un_evenement_inconnu():
    r = client.post("/telnyx/webhook", json={"data": {"event_type": "autre"}})
    assert r.status_code == 200
