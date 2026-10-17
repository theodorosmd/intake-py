import base64
import wave

from fastapi.testclient import TestClient

import telnyx_stream
from main import app

client = TestClient(app)


def test_decodage_ulaw():
    # 0xFF = silence, 0x00 et 0x80 = les deux extrêmes
    pcm = telnyx_stream.ulaw_vers_pcm16(bytes([0xFF, 0x00, 0x80]))
    valeurs = [int.from_bytes(pcm[i : i + 2], "little", signed=True) for i in (0, 2, 4)]
    assert valeurs == [0, -32124, 32124]


def test_appel_entrant_est_decroche(monkeypatch):
    decroches = []

    async def faux_repondre(call_control_id):
        decroches.append(call_control_id)

    monkeypatch.setattr(telnyx_stream, "repondre", faux_repondre)
    entrant = {"data": {"event_type": "call.initiated",
                        "payload": {"call_control_id": "v3:abc", "direction": "incoming"}}}
    raccroche = {"data": {"event_type": "call.hangup",
                          "payload": {"call_control_id": "v3:abc"}}}
    assert client.post("/telnyx/voice", json=entrant).status_code == 200
    assert client.post("/telnyx/voice", json=raccroche).status_code == 200
    assert decroches == ["v3:abc"]


def test_stream_audio_enregistre_en_wav(monkeypatch, tmp_path):
    monkeypatch.setattr(telnyx_stream, "ENREGISTREMENTS", tmp_path)
    paquet = base64.b64encode(bytes([0xFF] * 160)).decode()  # 20 ms de silence
    with client.websocket_connect("/telnyx/media") as ws:
        ws.send_json({"event": "connected", "version": "1.0.0"})
        ws.send_json({"event": "start", "start": {"media_format": {
            "encoding": "PCMU", "sample_rate": 8000, "channels": 1}}})
        for _ in range(50):  # 50 x 20 ms = 1 seconde
            ws.send_json({"event": "media", "media": {"track": "inbound", "payload": paquet}})
        ws.send_json({"event": "stop"})
    fichiers = list(tmp_path.glob("appel-*.wav"))
    assert len(fichiers) == 1
    with wave.open(str(fichiers[0])) as f:
        assert f.getframerate() == 8000
        assert f.getnframes() == 8000  # 1 seconde
