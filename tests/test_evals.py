"""Le rejeu, testé sans réseau : un faux appelant et un faux agent qui appelle les outils."""

import asyncio
import json
from datetime import date

import agent_vocal
from evals import rejeu


def tour(agent, outils=()):
    return {"appelant": "Yes, please.", "entendu": "Yes, please.", "agent": agent,
            "outils": list(outils)}


def outil(nom, arguments, resultat):
    return {"nom": nom, "arguments": arguments, "resultat": resultat}


DOSSIER_OK = outil(
    "save_intake",
    {"caller_name": "Maria Lopez", "phone": "+15550100001", "accident_date": "2026-10-14",
     "accident_type": "car", "injured": True},
    {"status": "received"},
)
RESA_LUNDI_10 = outil(
    "book_consultation", {}, {"status": "booked", "slot": "2026-10-19T10:00:00-04:00"}
)
SCENARIO = {
    "id": "t", "categorie": "normal",
    "attendu": {
        "dossier": {"caller_name": "Maria", "phone_digits": "5550100001",
                    "accident_type": "car", "injured": True, "accident_days_ago": 3},
        "consultation": {"weekday": "Monday", "hour": 10},
        "appels_reservation_max": 1,
    },
}


def test_appel_conforme_sans_echec():
    tours = [tour("One moment.", [DOSSIER_OK]), tour("Booked.", [RESA_LUNDI_10])]
    assert rejeu.verifier(SCENARIO, tours, aujourdhui=date(2026, 10, 17)) == []


def test_mauvais_creneau_et_mauvaise_date():
    dossier = outil("save_intake", {**DOSSIER_OK["arguments"], "accident_date": "2026-10-15"},
                    {"status": "received"})
    resa = outil("book_consultation", {}, {"status": "booked", "slot": "2026-10-19T14:00:00-04:00"})
    echecs = rejeu.verifier(SCENARIO, [tour("ok", [dossier, resa])], aujourdhui=date(2026, 10, 17))
    assert any("date d'accident" in e for e in echecs)
    assert any("créneau final" in e for e in echecs)


def test_annonce_sans_outil_detectee():
    tours = [tour("ok", [DOSSIER_OK, RESA_LUNDI_10]),
             tour("I have updated your consultation to 11 AM.")]
    echecs = rejeu.verifier(SCENARIO, tours, aujourdhui=date(2026, 10, 17))
    assert any("annonce sans outil" in e for e in echecs)


def test_une_proposition_n_est_pas_une_annonce():
    tours = [tour("ok", [DOSSIER_OK, RESA_LUNDI_10]),
             tour("I will book it for you. Is 2 PM good?")]
    assert rejeu.verifier(SCENARIO, tours, aujourdhui=date(2026, 10, 17)) == []


def test_aucune_reservation_attendue():
    scenario = {"attendu": {"dossier": None, "consultation": None}}
    echecs = rejeu.verifier(scenario, [tour("ok", [DOSSIER_OK, RESA_LUNDI_10])])
    assert "dossier enregistré alors qu'il ne fallait pas" in echecs
    assert "consultation réservée alors qu'il ne fallait pas" in echecs


def test_transcription_erronee_une_seule_fois():
    deja = set()
    assert rejeu.entendre("I'm Theodore.", {"Theodore": "Tilda"}, deja) == "I'm Tilda."
    assert rejeu.entendre("No, Theodore!", {"Theodore": "Tilda"}, deja) == "No, Theodore!"


def test_un_appel_complet_rejoue_avec_les_vrais_outils(monkeypatch):
    """Faux appelant + faux LLM, mais vraie passerelle en mémoire."""
    repliques = iter(["Hi, I'm Maria.", "Monday 10 AM please.", rejeu.RACCROCHE])

    async def faux_appelant(scenario, tours):
        return next(repliques)

    async def faux_llm(historique, faits=None):
        if "Monday" in historique[-1]["content"]:
            args = {"caller_name": "Maria", "phone": "+15550100001",
                    "preferred_date": "2026-10-19", "preferred_hour": 10}
            resultat = await agent_vocal.executer_outil("book_consultation", json.dumps(args))
            if faits is not None:
                faits.append("[done]")
            yield f"Booked for {resultat['slot_spoken']}."
        else:
            yield "Hello Maria, when would you like to come in?"

    monkeypatch.setattr(rejeu, "replique_appelant", faux_appelant)
    monkeypatch.setattr(agent_vocal, "phrases_llm", faux_llm)
    monkeypatch.setattr(agent_vocal, "executer_outil", agent_vocal.executer_outil)
    tours = asyncio.run(rejeu.jouer({"appelant": "Maria"}))
    assert len(tours) == 2
    assert tours[1]["outils"][0]["resultat"]["status"] == "booked"
    assert "[TOOL] book_consultation" in rejeu.transcription(tours)


def test_reservation_sans_accord_detectee():
    tours = [tour("ok", [DOSSIER_OK]),
             {"appelant": "x", "entendu": "Hmm, what are the options?",
              "agent": "Booked.", "outils": [RESA_LUNDI_10]}]
    echecs = rejeu.verifier(SCENARIO, tours, aujourdhui=date(2026, 10, 17))
    assert any("sans accord" in e for e in echecs)


def test_reservation_apres_accord_acceptee():
    tours = [tour("ok", [DOSSIER_OK]),
             {"appelant": "x", "entendu": "Monday at 10 AM works for me.",
              "agent": "Booked.", "outils": [RESA_LUNDI_10]}]
    assert rejeu.verifier(SCENARIO, tours, aujourdhui=date(2026, 10, 17)) == []
