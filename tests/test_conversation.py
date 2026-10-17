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
        await conv.envoyer_voix(b"mp3", "tour-1", "Can I have your name?")
        assert conv.parle
        conv.debut_son -= 2  # le son joue depuis 2 s : plus dans la période de grâce
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
        conv.debut_son -= 2
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

    async def llm_lent(messages, faits=None):
        questions.append(messages[-1]["content"])
        await asyncio.sleep(0.2)
        yield "Thanks."

    async def voix(texte):
        return b"mp3"

    monkeypatch.setattr(agent_vocal, "phrases_llm", llm_lent)
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


def test_les_phrases_partent_une_par_une_et_l_historique_les_garde(monkeypatch):
    async def llm(messages, faits=None):
        yield "Sorry to hear that."
        yield "Can I have your name?"

    async def voix(texte):
        return texte.encode()

    monkeypatch.setattr(agent_vocal, "phrases_llm", llm)
    monkeypatch.setattr(agent_vocal, "synthese_vocale", voix)

    async def scenario():
        ws = FauxWS()
        conv = Conversation(ws)
        conv.nouveau_tour("I had an accident", 0.0)
        await conv.tache
        return ws, conv

    ws, conv = asyncio.run(scenario())
    assert evenements(ws) == ["media", "mark", "media", "mark"]
    assert conv.historique[-1] == {
        "role": "assistant",
        "content": "Sorry to hear that. Can I have your name?",
    }
    assert len(conv.mesures) == 1  # un seul chiffre : le délai du premier son


def test_interrompu_apres_la_premiere_phrase_l_historique_garde_ce_qui_a_ete_dit(
    monkeypatch,
):
    async def llm(messages, faits=None):
        yield "Sorry to hear that."
        await asyncio.sleep(10)
        yield "Never said."

    async def voix(texte):
        return b"mp3"

    monkeypatch.setattr(agent_vocal, "phrases_llm", llm)
    monkeypatch.setattr(agent_vocal, "synthese_vocale", voix)

    async def scenario():
        conv = Conversation(FauxWS())
        conv.nouveau_tour("I had an accident", 0.0)
        await asyncio.sleep(0.05)
        conv.debut_son -= 2
        await conv.appelant_parle("No wait")
        await asyncio.sleep(0.05)
        return conv

    conv = asyncio.run(scenario())
    assert conv.historique[-1]["content"] == "Sorry to hear that."


def test_la_fin_d_un_son_ne_fait_pas_croire_que_les_suivants_sont_finis():
    async def scenario():
        conv = Conversation(FauxWS())
        await conv.envoyer_voix(b"a", "t-1")
        await conv.envoyer_voix(b"b", "t-2")
        conv.fin_de_lecture("t-1")
        return conv.parle

    assert asyncio.run(scenario()) is True


def test_pas_d_interruption_dans_la_premiere_seconde_du_son():
    async def scenario():
        ws = FauxWS()
        conv = Conversation(ws)
        await conv.envoyer_voix(b"mp3", "tour-1", "Hello, how can I help you?")
        await conv.appelant_parle(
            "Paying off the phone"
        )  # tout de suite : écho probable
        return ws, conv

    ws, conv = asyncio.run(scenario())
    assert "clear" not in evenements(ws)
    assert conv.interruptions == 0


def test_l_echo_de_l_agent_n_interrompt_pas_mais_une_vraie_phrase_oui():
    async def scenario():
        ws = FauxWS()
        conv = Conversation(ws)
        await conv.envoyer_voix(
            b"mp3", "tour-1", "Can I have your phone number please?"
        )
        conv.debut_son -= 2
        await conv.appelant_parle("your phone number")  # ce que dit l'agent : écho
        assert conv.interruptions == 0
        await conv.appelant_parle("No wait my name is Dan")  # vraie parole
        return conv

    assert asyncio.run(scenario()).interruptions == 1


def test_un_outil_execute_est_note_dans_l_historique_meme_si_le_tour_est_coupe(
    monkeypatch,
):
    async def llm(messages, faits=None):
        faits.append(
            "[Tool save_intake already done and confirmed: {}. Do not call it again.]"
        )
        await asyncio.sleep(10)  # coupé avant la moindre parole
        yield "never said"

    monkeypatch.setattr(agent_vocal, "phrases_llm", llm)

    async def scenario():
        conv = Conversation(FauxWS())
        conv.nouveau_tour("My name is Dan", 0.0)
        await asyncio.sleep(0.05)
        conv.tache.cancel()
        await asyncio.sleep(0.05)
        return conv

    conv = asyncio.run(scenario())
    assert conv.historique[-2] == {"role": "user", "content": "My name is Dan"}
    assert conv.historique[-1]["role"] == "system"
    assert "save_intake" in conv.historique[-1]["content"]
    assert conv.non_repondu == []
