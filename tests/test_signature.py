import base64
import json
import time

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

import telnyx_signature as sig
from main import app

client = TestClient(app)
EVENEMENT = {"data": {"event_type": "call.hangup", "payload": {"call_control_id": "v3:abc"}}}


@pytest.fixture
def cles(monkeypatch):
    """Une paire de clés de test ; la clé publique est celle que le serveur croit être Telnyx."""
    privee = SigningKey.generate()
    publique = base64.b64encode(bytes(privee.verify_key)).decode()
    monkeypatch.delenv("WEBHOOK_SIGNATURE_OFF", raising=False)
    monkeypatch.setenv("TELNYX_PUBLIC_KEY", publique)
    return privee


def signer(privee, corps: bytes, horodatage: int | None = None):
    horodatage = int(time.time()) if horodatage is None else horodatage
    signature = privee.sign(f"{horodatage}|".encode() + corps).signature
    return {
        "telnyx-signature-ed25519": base64.b64encode(signature).decode(),
        "telnyx-timestamp": str(horodatage),
        "content-type": "application/json",
    }


def poster(chemin, entetes, corps: bytes):
    return client.post(chemin, content=corps, headers=entetes)


def test_un_evenement_bien_signe_passe(cles):
    corps = json.dumps(EVENEMENT).encode()
    assert poster("/telnyx/voice", signer(cles, corps), corps).status_code == 200
    assert poster("/telnyx/webhook", signer(cles, corps), corps).status_code == 200


def test_sans_signature_c_est_refuse(cles):
    corps = json.dumps(EVENEMENT).encode()
    assert poster("/telnyx/voice", {"content-type": "application/json"}, corps).status_code == 401


def test_un_corps_modifie_est_refuse(cles):
    corps = json.dumps(EVENEMENT).encode()
    entetes = signer(cles, corps)
    assert poster("/telnyx/voice", entetes, corps + b" ").status_code == 401


def test_la_signature_d_une_autre_cle_est_refusee(cles):
    corps = json.dumps(EVENEMENT).encode()
    assert poster("/telnyx/voice", signer(SigningKey.generate(), corps), corps).status_code == 401


def test_une_requete_trop_ancienne_est_refusee(cles):
    corps = json.dumps(EVENEMENT).encode()
    vieux = int(time.time()) - sig.TOLERANCE_SECONDES - 60
    assert poster("/telnyx/voice", signer(cles, corps, vieux), corps).status_code == 401


def test_sans_cle_configuree_le_service_refuse(monkeypatch):
    monkeypatch.delenv("WEBHOOK_SIGNATURE_OFF", raising=False)
    monkeypatch.delenv("TELNYX_PUBLIC_KEY", raising=False)
    corps = json.dumps(EVENEMENT).encode()
    assert poster("/telnyx/voice", {"content-type": "application/json"}, corps).status_code == 503


def test_une_cle_mal_formee_ne_fait_pas_planter(cles, monkeypatch):
    monkeypatch.setenv("TELNYX_PUBLIC_KEY", "pas-du-base64!!")
    corps = json.dumps(EVENEMENT).encode()
    assert poster("/telnyx/voice", signer(cles, corps), corps).status_code == 401
