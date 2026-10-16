import httpx
from fastapi.testclient import TestClient

import main
from main import app

client = TestClient(app)


class FausseReponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"call_id": "call-1", "injured": True, "method": "llm"}


def test_call_ended_transmet_au_worker(monkeypatch):
    envoye = {}

    def faux_post(url, json, timeout):
        envoye["url"] = url
        envoye["json"] = json
        return FausseReponse()

    monkeypatch.setattr(main.httpx, "post", faux_post)
    r = client.post(
        "/call-ended", json={"call_id": "call-1", "transcript": "I was hurt"}
    )
    assert r.status_code == 200
    assert r.json()["summary"]["method"] == "llm"
    assert envoye["url"].endswith("/summarize")
    assert envoye["json"] == {"call_id": "call-1", "text": "I was hurt"}


def test_worker_en_panne_renvoie_502(monkeypatch):
    def worker_mort(url, json, timeout):
        raise httpx.ConnectError("connexion refusee")

    monkeypatch.setattr(main.httpx, "post", worker_mort)
    r = client.post(
        "/call-ended", json={"call_id": "call-1", "transcript": "I was hurt"}
    )
    assert r.status_code == 502
