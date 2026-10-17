import asyncio

import agent_vocal
from conversation import Conversation


class FauxWS:
    """Note tout ce que la conversation envoie à Telnyx."""

    def __init__(self):
        self.envoye = []

    async def send_json(self, message):
        self.envoye.append(message)


def evenements(ws):
    return [m["event"] for m in ws.envoye]


def test_interrompre_pendant_que_l_agent_parle_coupe_le_son():
    async def scenario():
        ws = FauxWS()
        conv = Conversation(ws)
        await conv.envoyer_voix(b"mp3", "tour-1")
        assert conv.parle
        await conv.appelant_parle("No wait")  # 2 mots : interruption
        return ws, conv

    ws, conv = asyncio.run(scenario())
    assert evenements(ws) == ["media", "mark", "clear"]
    assert not conv.parle
    assert conv.interruptions == 1


def test_un_seul_mot_ne_coupe_pas_l_agent():
    async def scenario():
        ws = FauxWS()
        conv = Conversation(ws)
        await conv.envoyer_voix(b"mp3", "tour-1")
        await conv.appelant_parle("uh")
        return ws

    assert "clear" not in evenements(asyncio.run(scenario()))


def test_pas_d_interruption_quand_l_agent_ecoute():
    async def scenario():
        ws = FauxWS()
        conv = Conversation(ws)
        await conv.envoyer_voix(b"mp3", "tour-1")
        conv.fin_de_lecture()  # Telnyx a renvoyé la marque : son terminé
        await conv.appelant_parle("My name is Dan")
        return ws, conv

    ws, conv = asyncio.run(scenario())
    assert "clear" not in evenements(ws)
    assert conv.interruptions == 0


def test_phrases_coupees_sont_fusionnees(monkeypatch):
    questions = []

    async def llm_lent(messages):
        questions.append(messages[-1]["content"])
        await asyncio.sleep(0.2)
        return "Thanks."

    async def voix(texte):
        return b"mp3"

    monkeypatch.setattr(agent_vocal, "reponse_llm", llm_lent)
    monkeypatch.setattr(agent_vocal, "synthese_vocale", voix)

    async def scenario():
        conv = Conversation(FauxWS())
        conv.nouveau_tour("I was a", 0.0)
        await asyncio.sleep(0.05)
        conv.nouveau_tour("passenger.", 0.0)  # la 1re réponse est abandonnée
        await conv.tache
        return conv

    conv = asyncio.run(scenario())
    assert questions == ["I was a", "I was a passenger."]
    assert conv.historique[-2] == {"role": "user", "content": "I was a passenger."}
    assert conv.non_repondu == []
    assert len(conv.mesures) == 1


def test_bilan_donne_les_medianes():
    conv = Conversation(FauxWS())
    conv.mesures = [
        {"attente": 800, "llm": 2000, "voix": 1000, "total": 3800},
        {"attente": 800, "llm": 1500, "voix": 1200, "total": 3500},
        {"attente": 800, "llm": 3000, "voix": 1100, "total": 4900},
    ]
    assert conv.bilan() == {
        "tours": 3,
        "interruptions": 0,
        "attente": 800,
        "llm": 2000,
        "voix": 1100,
        "total": 3800,
    }
