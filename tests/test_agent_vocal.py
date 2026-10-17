import asyncio
import json
from types import SimpleNamespace

import agent_vocal


def test_sans_proxy_le_llm_est_appele_en_direct(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-6-luna")
    monkeypatch.delenv("LLM_API_BASE", raising=False)
    assert agent_vocal.parametres_llm() == {"model": "openai/gpt-6-luna"}


def test_avec_proxy_le_llm_passe_par_litellm(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "openai/llm-agent")
    monkeypatch.setenv("LLM_API_BASE", "http://localhost:4000")
    monkeypatch.setenv("LLM_API_KEY", "sk-virtuelle")
    assert agent_vocal.parametres_llm() == {
        "model": "openai/llm-agent",
        "api_base": "http://localhost:4000",
        "api_key": "sk-virtuelle",
    }


def test_decoupage_en_phrases():
    phrases, reste = agent_vocal.decouper_phrases(
        "Sorry to hear that. Can I have your na"
    )
    assert phrases == ["Sorry to hear that."]
    assert reste == "Can I have your na"


def test_phrase_trop_courte_attend_la_suivante():
    phrases, reste = agent_vocal.decouper_phrases("Sure. Tell me what hap")
    assert phrases == []
    assert reste == "Sure. Tell me what hap"


def test_phrase_courte_garde_l_espace_de_fin():
    phrases, reste = agent_vocal.decouper_phrases("One moment. I will book Tuesday at ")
    assert phrases == []
    assert reste == "One moment. I will book Tuesday at "
    assert (reste + "10 AM.").endswith("at 10 AM.")


def test_les_consignes_donnent_la_date_et_interdisent_les_conseils_medicaux():
    texte = agent_vocal.consignes()
    assert "Today is" in texte
    assert "medical advice" in texte
    assert "save_intake" in texte


def test_outil_inconnu_ou_arguments_invalides():
    assert "error" in asyncio.run(agent_vocal.executer_outil("rm_rf", "{}"))
    assert "error" in asyncio.run(
        agent_vocal.executer_outil("save_intake", "pas du json")
    )


def morceau(contenu=None, appel=None):
    delta = SimpleNamespace(content=contenu, tool_calls=[appel] if appel else None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def appel_outil(index, id_, nom, arguments):
    return SimpleNamespace(
        index=index, id=id_, function=SimpleNamespace(name=nom, arguments=arguments)
    )


def test_le_llm_demande_un_outil_puis_repond_avec_son_resultat(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "openai/llm-agent")
    monkeypatch.delenv("LLM_API_BASE", raising=False)
    rondes = [
        [  # 1re ronde : l'outil demandé en trois morceaux, comme en vrai
            morceau(appel=appel_outil(0, "call_1", "save_intake", '{"caller_na')),
            morceau(appel=appel_outil(0, None, None, 'me": "Dan"}')),
        ],
        [morceau("Your file is recorded. "), morceau("Anything else?")],
    ]
    envoyes = []
    executes = []

    async def faux_acompletion(**kwargs):
        envoyes.append(kwargs["messages"])
        ronde = rondes.pop(0)

        async def flux():
            for m in ronde:
                yield m

        return flux()

    async def faux_outil(nom, arguments):
        executes.append((nom, json.loads(arguments)))
        return {"status": "received", "case_id": "ab12"}

    monkeypatch.setattr(agent_vocal.litellm, "acompletion", faux_acompletion)
    monkeypatch.setattr(agent_vocal, "executer_outil", faux_outil)

    async def scenario():
        faits = []
        phrases = [
            p
            async for p in agent_vocal.phrases_llm(
                [{"role": "user", "content": "My name is Dan"}], faits
            )
        ]
        return phrases, faits

    phrases, faits = asyncio.run(scenario())
    assert executes == [("save_intake", {"caller_name": "Dan"})]
    assert phrases == ["Your file is recorded.", "Anything else?"]
    assert "save_intake" in faits[0] and "ab12" in faits[0]
    dernier = envoyes[1][-1]  # la 2e ronde a reçu le résultat de l'outil
    assert dernier["role"] == "tool" and "ab12" in dernier["content"]


def test_la_reservation_reste_modifiable_apres_confirmation():
    note = agent_vocal.note_outil("book_consultation", {"status": "booked"})
    assert "Do not call it again" not in note
    assert "call book_consultation again" in note
    assert "Do not call it again" in agent_vocal.note_outil("save_intake", {})


def test_annonce_de_reservation_sans_outil_retenue_et_corrigee(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "openai/llm-agent")
    monkeypatch.delenv("LLM_API_BASE", raising=False)
    rondes = [
        # 1re ronde : le LLM annonce le changement sans appeler l'outil
        [morceau("One moment, I will change it now. "),
         morceau("Your consultation is booked for Wednesday at 4 PM.")],
        # après la correction : il appelle l'outil
        [morceau(appel=appel_outil(0, "c1", "book_consultation", '{"preferred_date": "2026-10-21"}'))],
        [morceau("Your consultation is booked for Wednesday, October 21 at 4 PM.")],
    ]
    envoyes = []

    async def faux_acompletion(**kwargs):
        envoyes.append(list(kwargs["messages"]))
        ronde = rondes.pop(0)

        async def flux():
            for m in ronde:
                yield m

        return flux()

    async def faux_outil(nom, arguments):
        return {"status": "rescheduled", "slot_spoken": "Wednesday, October 21 at 4 PM"}

    monkeypatch.setattr(agent_vocal.litellm, "acompletion", faux_acompletion)
    monkeypatch.setattr(agent_vocal, "executer_outil", faux_outil)

    async def scenario():
        return [p async for p in agent_vocal.phrases_llm([{"role": "user", "content": "Move it"}], [])]

    phrases = asyncio.run(scenario())
    assert phrases == [
        "One moment, I will change it now.",
        "Your consultation is booked for Wednesday, October 21 at 4 PM.",
    ]
    assert envoyes[1][-1]["content"] == agent_vocal.CORRECTION_ANNONCE


def test_les_consignes_listent_les_jours_de_semaine():
    texte = agent_vocal.consignes()
    for jour in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
        assert jour in texte
