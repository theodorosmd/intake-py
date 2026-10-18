"""La base de connaissances, testée sans réseau : un faux modèle d'embeddings (sac de mots
haché dans 1536 dimensions) remplace l'API. Il suffit à vérifier découpage, indexation
incrémentale, seuil et branchements ; la qualité réelle de la recherche se mesure au rejeu."""

import asyncio
import hashlib
import math
import os
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agent_vocal
import main
import rag
from stockage import Memoire, Postgres

URL = os.environ.get("TEST_DATABASE_URL")
MOTS_VIDES = {"the", "a", "an", "of", "to", "and", "or", "is", "are", "do", "does", "i", "you", "for", "it", "how", "what", "can"}


def faux_vecteur(texte: str) -> list[float]:
    v = [0.0] * rag.DIMENSIONS
    for mot in re.findall(r"[a-z]+", texte.lower()):
        if mot not in MOTS_VIDES:
            v[int(hashlib.md5(mot.encode()).hexdigest(), 16) % rag.DIMENSIONS] += 1.0
    norme = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norme for x in v]


@pytest.fixture(autouse=True)
def faux_embeddings(monkeypatch):
    appels: list[int] = []

    async def vectoriser(textes):
        appels.append(len(textes))
        return [faux_vecteur(t) for t in textes]

    monkeypatch.setattr(rag, "vectoriser", vectoriser)
    monkeypatch.setattr(rag, "SEUIL", 0.15)  # le faux modèle donne des scores plus bas que le vrai
    return appels


@pytest.fixture(params=["memoire", "postgres"])
def stockage(request):
    if request.param == "memoire":
        s = Memoire()
    elif URL:
        s = Postgres(URL)
    else:
        pytest.skip("TEST_DATABASE_URL absent")
    s.preparer()
    s.supprimer_connaissances(s.empreintes_connaissances())
    yield s
    s.supprimer_connaissances(s.empreintes_connaissances())
    s.fermer()


def test_chaque_section_devient_un_morceau_avec_sa_source():
    morceaux = rag.decouper()
    assert len(morceaux) >= 10
    assert len({m.id for m in morceaux}) == len(morceaux)
    assert all(m.texte.startswith(m.titre) for m in morceaux)
    assert not any(m.source.lower() == "readme.md" for m in morceaux)
    assert "honoraires.md#how-the-firm-is-paid" in {m.id for m in morceaux}


def test_aucun_conseil_juridique_dans_la_base():
    """Garde-fou sur le contenu : la base parle du cabinet, pas du droit."""
    texte = " ".join(m.texte for m in rag.decouper()).lower()
    for interdit in ("statute of limitations is", "you should sue", "you will win", "years to file"):
        assert interdit not in texte


def test_l_index_n_est_recalcule_que_pour_ce_qui_change(stockage, faux_embeddings, tmp_path):
    dossier = tmp_path / "connaissances"
    shutil.copytree(rag.DOSSIER, dossier)
    premier = asyncio.run(rag.indexer(stockage, dossier))
    assert premier["calculees"] == premier["sections"] > 0

    assert asyncio.run(rag.indexer(stockage, dossier))["calculees"] == 0

    fichier = dossier / "cabinet.md"
    fichier.write_text(fichier.read_text().replace("9 AM to 6 PM", "8 AM to 7 PM"))
    assert asyncio.run(rag.indexer(stockage, dossier))["calculees"] == 1

    (dossier / "honoraires.md").unlink()
    bilan = asyncio.run(rag.indexer(stockage, dossier))
    assert bilan["retirees"] == 3 and bilan["calculees"] == 0
    assert not any(i.startswith("honoraires.md") for i in stockage.empreintes_connaissances())


def test_la_question_sur_les_honoraires_trouve_la_bonne_section(stockage):
    asyncio.run(rag.indexer(stockage))
    reponse = asyncio.run(rag.rechercher(stockage, "How is the firm paid, is there a fee upfront?"))
    assert reponse["results"][0]["source"] == "honoraires.md#how-the-firm-is-paid"
    assert reponse["note"] is None


def test_une_question_hors_sujet_ne_renvoie_rien(stockage):
    asyncio.run(rag.indexer(stockage))
    reponse = asyncio.run(rag.rechercher(stockage, "Quel temps fera-t-il demain à Marseille ?"))
    assert reponse["results"] == []
    assert "do not have that information" in reponse["note"]


def test_la_route_de_recherche_repond():
    asyncio.run(rag.indexer(main.STOCKAGE))
    client = TestClient(main.app)
    r = client.post("/knowledge/search", json={"question": "Do the attorneys speak Spanish?"})
    assert r.status_code == 200
    assert r.json()["results"][0]["source"] == "cabinet.md#languages"


def test_la_route_refuse_une_question_vide():
    r = TestClient(main.app).post("/knowledge/search", json={"question": ""})
    assert r.status_code == 422


def test_l_agent_connait_l_outil_et_sa_route():
    noms = {o["function"]["name"] for o in agent_vocal.OUTILS}
    assert "search_firm_info" in noms
    assert agent_vocal.CHEMINS_OUTILS["search_firm_info"] == "/knowledge/search"
    assert "search_firm_info" in agent_vocal.consignes()


def test_le_dossier_de_connaissances_est_dans_l_image():
    assert "connaissances" in Path(__file__).parents[1].joinpath("Dockerfile").read_text()


def test_le_rechauffage_appelle_l_api_regulierement(faux_embeddings):
    async def scenario():
        tache = asyncio.create_task(rag.garder_au_chaud(intervalle=0.01))
        await asyncio.sleep(0.05)
        tache.cancel()

    asyncio.run(scenario())
    assert len(faux_embeddings) >= 2  # au moins deux réchauffages en 50 ms


def test_le_rechauffage_survit_a_une_panne(monkeypatch):
    appels = []

    async def en_panne(textes):
        appels.append(1)
        raise RuntimeError("API indisponible")

    monkeypatch.setattr(rag, "vectoriser", en_panne)

    async def scenario():
        tache = asyncio.create_task(rag.garder_au_chaud(intervalle=0.01))
        await asyncio.sleep(0.05)
        assert not tache.done()  # la panne est journalisée, la boucle continue
        tache.cancel()

    asyncio.run(scenario())
    assert len(appels) >= 2
