import asyncio
import base64
import json
from typing import ClassVar

from fastapi.testclient import TestClient

import agent_vocal
import telnyx_stream
from main import app

client = TestClient(app)


class FauxSTT:
    """Remplace la WebSocket de transcription : note ce qu'on envoie, renvoie des réponses prévues."""

    def __init__(self, reponses, pause=0.0):
        self.reponses = reponses
        self.pause = pause  # temps d'attente après la dernière réponse
        self.envoye = []
        self.ferme = False

    async def send(self, donnees):
        self.envoye.append(donnees)

    async def close(self):
        self.ferme = True

    async def __aiter__(self):
        for reponse in self.reponses:
            yield json.dumps(reponse)
        await asyncio.sleep(self.pause)


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
    entrant = {
        "data": {
            "event_type": "call.initiated",
            "payload": {"call_control_id": "v3:abc", "direction": "incoming"},
        }
    }
    raccroche = {
        "data": {"event_type": "call.hangup", "payload": {"call_control_id": "v3:abc"}}
    }
    assert client.post("/telnyx/voice", json=entrant).status_code == 200
    assert client.post("/telnyx/voice", json=raccroche).status_code == 200
    assert decroches == ["v3:abc"]


def test_seules_les_phrases_definitives_sont_gardees():
    stt = FauxSTT(
        [
            {"transcript": "My name", "is_final": False},
            {"transcript": "My name is Dan Nguyen.", "is_final": True},
            {"transcript": "", "is_final": True, "utterance_end": True},
        ]
    )
    assert asyncio.run(telnyx_stream.lire_transcriptions(stt)) == [
        "My name is Dan Nguyen."
    ]


def test_fin_de_phrase_envoie_un_tour_a_l_agent():
    stt = FauxSTT(
        [
            {"transcript": "My name is", "is_final": True, "speech_final": False},
            {"transcript": "Dan Nguyen.", "is_final": True, "speech_final": True},
            {"transcript": "I was", "is_final": False},
        ]
    )
    tours = asyncio.Queue()
    asyncio.run(telnyx_stream.lire_transcriptions(stt, tours))
    phrase, _ = tours.get_nowait()
    assert phrase == "My name is Dan Nguyen."
    assert tours.empty()  # "I was" n'est pas fini : pas de tour


def test_silence_apres_un_morceau_definitif_envoie_un_tour(monkeypatch):
    monkeypatch.setattr(telnyx_stream, "DELAI_FIN_DE_PHRASE", 0.05)
    stt = FauxSTT(
        [{"transcript": "My name is Theodore Smith.", "is_final": True}], pause=0.2
    )
    tours = asyncio.Queue()
    asyncio.run(telnyx_stream.lire_transcriptions(stt, tours))
    phrase, _ = tours.get_nowait()
    assert phrase == "My name is Theodore Smith."


def test_pas_de_tour_si_l_appelant_continue(monkeypatch):
    monkeypatch.setattr(telnyx_stream, "DELAI_FIN_DE_PHRASE", 0.05)
    stt = FauxSTT(
        [
            {"transcript": "My name is", "is_final": True},
            {"transcript": "Theodore", "is_final": False},  # il parle encore
        ],
        pause=0.2,
    )
    tours = asyncio.Queue()
    asyncio.run(telnyx_stream.lire_transcriptions(stt, tours))
    assert tours.empty()


def test_appel_complet_salutation_puis_reponse(monkeypatch, tmp_path):
    monkeypatch.setattr(telnyx_stream, "ENREGISTREMENTS", tmp_path)
    stt = FauxSTT([{"transcript": "Hello", "is_final": True, "speech_final": True}])

    async def faux_ouvrir_stt():
        return stt

    async def faux_llm(historique, faits=None):
        assert historique[-1] == {"role": "user", "content": "Hello"}
        yield "Hi, what is your name?"

    async def fausse_voix(texte):
        return texte.encode()  # à la place d'un vrai mp3

    monkeypatch.setattr(telnyx_stream, "ouvrir_stt", faux_ouvrir_stt)
    monkeypatch.setattr(agent_vocal, "phrases_llm", faux_llm)
    monkeypatch.setattr(agent_vocal, "synthese_vocale", fausse_voix)
    paquet = base64.b64encode(bytes([0xFF] * 160)).decode()  # 20 ms de silence
    with client.websocket_connect("/telnyx/media") as ws:
        ws.send_json({"event": "connected", "version": "1.0.0"})
        ws.send_json(
            {
                "event": "start",
                "start": {
                    "media_format": {
                        "encoding": "PCMU",
                        "sample_rate": 8000,
                        "channels": 1,
                    }
                },
            }
        )
        salutation = ws.receive_json()
        marque_1 = ws.receive_json()
        reponse = ws.receive_json()
        marque_2 = ws.receive_json()
        ws.send_json({"event": "mark", "mark": {"name": "tour-1-1"}})  # son joué
        for _ in range(50):  # 50 x 20 ms = 1 seconde
            ws.send_json({"event": "media", "media": {"payload": paquet}})
        ws.send_json({"event": "stop"})

    assert salutation["event"] == "media"
    assert base64.b64decode(salutation["media"]["payload"]).decode() == (
        agent_vocal.SALUTATION
    )
    assert base64.b64decode(reponse["media"]["payload"]) == b"Hi, what is your name?"
    assert marque_1["event"] == marque_2["event"] == "mark"
    audio_envoye = [d for d in stt.envoye if isinstance(d, bytes)]
    assert sum(len(d) for d in audio_envoye) == 8000  # tout le son est parti
    assert all(len(d) == 800 for d in audio_envoye)  # par morceaux de 100 ms
    assert json.loads(stt.envoye[-1]) == {"type": "CloseStream"}
    assert stt.ferme
    assert len(list(tmp_path.glob("appel-*.wav"))) == 1


class FauxClientHttp:
    """Remplace httpx.AsyncClient : note la requête, renvoie une réponse prévue ou lève une erreur."""

    envoye: ClassVar[list] = []
    erreur: ClassVar[Exception | None] = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json):
        if FauxClientHttp.erreur:
            raise FauxClientHttp.erreur
        FauxClientHttp.envoye.append((url, json))
        import httpx

        return httpx.Response(
            200, json={"method": "llm"}, request=httpx.Request("POST", url)
        )


def test_la_transcription_est_envoyee_au_resume(monkeypatch):
    FauxClientHttp.envoye, FauxClientHttp.erreur = [], None
    monkeypatch.setattr(telnyx_stream.httpx, "AsyncClient", FauxClientHttp)
    asyncio.run(telnyx_stream.resumer_appel("v3:abc", "I was hurt in a car crash"))
    url, corps = FauxClientHttp.envoye[0]
    assert url.endswith("/summarize")
    assert corps == {"call_id": "v3:abc", "text": "I was hurt in a car crash"}


def test_pas_de_resume_sans_transcription(monkeypatch):
    FauxClientHttp.envoye, FauxClientHttp.erreur = [], None
    monkeypatch.setattr(telnyx_stream.httpx, "AsyncClient", FauxClientHttp)
    asyncio.run(telnyx_stream.resumer_appel("v3:abc", "   "))
    assert FauxClientHttp.envoye == []


def test_un_worker_en_panne_ne_casse_pas_la_fin_de_l_appel(monkeypatch):
    import httpx

    FauxClientHttp.envoye, FauxClientHttp.erreur = [], httpx.ConnectError("down")
    monkeypatch.setattr(telnyx_stream.httpx, "AsyncClient", FauxClientHttp)
    asyncio.run(telnyx_stream.resumer_appel("v3:abc", "hello"))  # ne doit pas lever
