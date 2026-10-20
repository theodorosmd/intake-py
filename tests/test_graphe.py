"""Le pipeline en graphe (dossiers/graphe/) : même résultat que le code simple, plus la pause et la reprise.

Faux modèle, aucun réseau. Les tests de Postgres ne tournent qu'avec TEST_DATABASE_URL, comme ceux des dossiers.
Sans LangGraph installé (pip install -r requirements-graphe.txt), tout ce fichier est ignoré.
"""

import asyncio
import os
import uuid
from collections import Counter

import pytest

pytest.importorskip("langgraph")

from langgraph.types import Command
from test_dossiers import faux_analyste, faux_modele

from dossiers import fixtures
from dossiers.enjeux import analyser
from dossiers.graphe import (
    attente,
    configuration,
    construire_graphe,
    entree,
    proposition_en_attente,
)
from dossiers.graphe.sauvegarde import sauvegarde_memoire, sauvegarde_postgres
from dossiers.importer import CONSIGNE_CLASSEMENT, CONSIGNE_EVENEMENTS, importer
from dossiers.magasin import MagasinMemoire, MagasinPostgres
from stockage import Postgres

URL = os.environ.get("TEST_DATABASE_URL")
QUESTIONS = ["Le retard a-t-il été accepté par l'acheteur ?", "La machine a-t-elle été livrée en retard ?"]
VALIDER = {"decision": "valider"}


class Modele:
    """Le faux modèle des dossiers (import + analyse), qui compte ses appels et peut tomber en panne à la demande."""

    def __init__(self, panne_sur=None):
        self.appels = Counter()
        self.panne_sur = panne_sur  # (type d'appel, numéro) : « evenements », 3 = la 3e demande d'événements échoue
        self._import, self._analyste = faux_modele(), faux_analyste()

    async def __call__(self, consigne, texte):
        genre = ("classement" if consigne == CONSIGNE_CLASSEMENT else "evenements" if consigne == CONSIGNE_EVENEMENTS else "analyse")
        self.appels[genre] += 1
        if self.panne_sur == (genre, self.appels[genre]):
            raise RuntimeError(f"panne simulée ({genre} n°{self.appels[genre]})")
        return await (self._analyste if genre == "analyse" else self._import)(consigne, texte)


@pytest.fixture(params=["memoire", "postgres"])
def magasin(request):
    if request.param == "memoire":
        yield MagasinMemoire()
        return
    if not URL:
        pytest.skip("TEST_DATABASE_URL absent")
    s = Postgres(URL)
    s.preparer()
    m = MagasinPostgres(s)
    m.vider()
    yield m
    m.vider()
    s.fermer()


def instantane(magasin):
    """Tout ce que le pipeline a écrit, sous une forme comparable (sans les identifiants ni l'état de validation)."""
    docs = magasin.documents("A1")
    noms = {d["doc_id"]: d["filename"] for d in docs}
    return {
        "pieces": sorted((d["filename"], d["status"], d["doc_type"], tuple(d["flags"])) for d in docs),
        "evenements": sorted((str(e["event_date"]), e["title"], noms[e["doc_id"]], e["page_no"], e["quote"], e["note"])
                             for e in magasin.evenements("A1")),
        "enjeux": [(q["question"], q["manques"], sorted((noms[p["doc_id"]], p["page_no"], p["stance"], p["quote"], p["why"], p["warning"])
                                                         for p in q["preuves"])) for q in magasin.enjeux("A1")],
    }


def code_simple(questions=QUESTIONS, pieces=None):
    """Le même travail sans graphe : ce que le graphe doit reproduire. Renvoie (instantané, modèle)."""
    magasin, modele = MagasinMemoire(), Modele()

    async def faire():
        magasin.creer_affaire("A1", "Atelier Marin")
        for nom, octets in (pieces or fixtures.generer()).items():
            await importer(magasin, "A1", nom, octets, modele)
        for q in questions:
            await analyser(magasin, "A1", magasin.ajouter_enjeu("A1", q), modele)

    asyncio.run(faire())
    return instantane(magasin), modele


def lancer(magasin, modele, thread="t", questions=QUESTIONS, pieces=None, sauvegarde=None):
    """Construit le graphe et le fait avancer jusqu'à la pause. Renvoie (graphe, configuration, résultat)."""
    pieces = pieces or fixtures.generer()
    graphe = construire_graphe(magasin, modele, pieces, sauvegarde or sauvegarde_memoire())
    config = configuration(thread)
    resultat = asyncio.run(graphe.ainvoke(entree("A1", "Atelier Marin", list(pieces), questions), config))
    return graphe, config, resultat


def reprendre(graphe, config, reponse):
    return asyncio.run(graphe.ainvoke(Command(resume=reponse), config))


# ---------- Même résultat que le code simple ----------

def test_le_graphe_donne_le_meme_resultat_que_le_code_simple(magasin):
    attendu, modele_simple = code_simple()
    modele = Modele()
    graphe, config, pause = lancer(magasin, modele)
    assert attente(pause) is not None
    fin = reprendre(graphe, config, VALIDER)
    assert fin["statut"] == "terminé"
    assert instantane(magasin) == attendu
    assert modele.appels == modele_simple.appels  # pas un appel de plus au modèle, donc pas un centime de plus


# ---------- La pause ----------

def test_la_pause_attend_l_avocat_avant_toute_analyse(magasin):
    modele = Modele()
    graphe, _config, pause = lancer(magasin, modele)
    proposition = attente(pause)
    assert proposition["affaire"] == "A1" and proposition["questions"] == QUESTIONS
    assert {p["piece"]: p["statut"] for p in proposition["pieces"]} == {
        "bon-livraison-copie.pdf": "doublon", "scan-illisible.pdf": "illisible", "contrat-vente-projet.pdf": "version",
        **{n: "ok" for n in fixtures.generer() if n not in fixtures.PIEGES}}
    assert proposition["chronologie"] and all(e["event_id"] and e["piece"] for e in proposition["chronologie"])
    assert modele.appels["analyse"] == 0 and magasin.enjeux("A1") == []
    assert all(e["status"] == "proposed" for e in magasin.evenements("A1"))
    # un autre processus relit la même proposition dans le point de reprise
    assert asyncio.run(proposition_en_attente(graphe, "t")) == proposition


def test_valider_confirme_le_sans_reserve_et_laisse_le_signale_a_l_avocat(magasin):
    graphe, config, _pause = lancer(magasin, Modele(), questions=[])
    fin = reprendre(graphe, config, VALIDER)
    assert fin["statut"] == "terminé" and fin["validation"]["decision"] == "valider"
    evenements = magasin.evenements("A1")
    assert {e["status"] for e in evenements if not e["note"]} == {"confirmed"}
    signales = [e for e in evenements if e["note"]]
    assert signales and {e["status"] for e in signales} == {"proposed"}  # les dates du projet restent à trancher
    assert fin["validation"]["confirmes"] == len(evenements) - len(signales)


def test_les_corrections_de_l_avocat_sont_appliquees_avant_l_analyse(magasin):
    graphe, config, pause = lancer(magasin, Modele())
    cible = next(e for e in attente(pause)["chronologie"] if e["piece"] == "contrat-vente-projet.pdf")
    fin = reprendre(graphe, config, {"decision": "valider", "corrections": {
        cible["event_id"]: {"event_date": "2026-01-21", "status": "disputed"}}})
    corrige = next(e for e in magasin.evenements("A1") if e["event_id"] == cible["event_id"])
    assert str(corrige["event_date"]) == "2026-01-21" and corrige["status"] == "disputed"
    assert fin["validation"]["corriges"] == 1 and len(fin["analyses"]) == 2


def test_refuser_arrete_le_graphe_sans_analyse(magasin):
    modele = Modele()
    graphe, config, _pause = lancer(magasin, modele)
    fin = reprendre(graphe, config, {"decision": "refuser"})
    assert fin["statut"] == "refusé" and modele.appels["analyse"] == 0 and magasin.enjeux("A1") == []
    assert asyncio.run(proposition_en_attente(graphe, "t")) is None
    assert all(e["status"] == "proposed" for e in magasin.evenements("A1"))


def test_une_reponse_inutilisable_repose_la_question_au_lieu_de_bloquer_le_fil(magasin):
    graphe, config, pause = lancer(magasin, Modele())
    connu = attente(pause)["chronologie"][0]["event_id"]
    for mauvaise in ({"decision": "peut-être"}, "oui", {"decision": "valider", "corrections": {"A1-e999": {"title": "x"}}},
                     {"decision": "valider", "corrections": {connu: {"event_date": "pas une date"}}},
                     {"decision": "valider", "corrections": {connu: {"auteur": "x"}}}):
        encore = reprendre(graphe, config, mauvaise)
        assert attente(encore)["erreur"]  # la proposition revient, avec le motif
    assert all(e["status"] == "proposed" for e in magasin.evenements("A1"))  # rien n'a été écrit
    fin = reprendre(graphe, config, VALIDER)
    assert fin["statut"] == "terminé"


# ---------- La reprise après panne ----------

def test_une_panne_pendant_le_releve_reprend_la_ou_elle_s_est_arretee(magasin):
    attendu, modele_simple = code_simple()
    sauvegarde = sauvegarde_memoire()
    panne = Modele(panne_sur=("evenements", 3))
    pieces = fixtures.generer()
    graphe = construire_graphe(magasin, panne, pieces, sauvegarde)
    config = configuration("t")
    with pytest.raises(RuntimeError, match="panne simulée"):
        asyncio.run(graphe.ainvoke(entree("A1", "Atelier Marin", list(pieces), QUESTIONS), config))
    classees = len(magasin.documents("A1"))
    assert 0 < classees < len(pieces)  # le plantage est bien survenu au milieu de l'import

    # Reprise par un graphe neuf, avec le même point de reprise : ni la mémoire ni la file d'attente ne viennent du processus précédent.
    modele = Modele()
    graphe2 = construire_graphe(magasin, modele, pieces, sauvegarde)
    pause = asyncio.run(graphe2.ainvoke(None, config))
    assert attente(pause) is not None
    fin = reprendre(graphe2, config, VALIDER)
    assert fin["statut"] == "terminé"
    assert instantane(magasin) == attendu
    # Aucune pièce refaite : le total des appels des deux essais = ceux du code simple + la demande qui a échoué.
    total = panne.appels + modele.appels
    assert total["classement"] == modele_simple.appels["classement"]
    assert total["evenements"] == modele_simple.appels["evenements"] + 1
    assert total["analyse"] == modele_simple.appels["analyse"]


def test_une_panne_apres_l_ecriture_des_evenements_ne_les_ecrit_pas_deux_fois(magasin):
    attendu, modele_simple = code_simple()
    sauvegarde, pieces, modele = sauvegarde_memoire(), fixtures.generer(), Modele()
    ecrire, tombee = magasin.enregistrer_evenements, []

    def ecrire_puis_tomber(matter_id, doc_id, evenements):
        ecrire(matter_id, doc_id, evenements)
        if evenements and not tombee:
            tombee.append(doc_id)
            raise RuntimeError("panne simulée après l'écriture")

    magasin.enregistrer_evenements = ecrire_puis_tomber
    graphe = construire_graphe(magasin, modele, pieces, sauvegarde)
    config = configuration("t")
    with pytest.raises(RuntimeError, match="après l'écriture"):
        asyncio.run(graphe.ainvoke(entree("A1", "Atelier Marin", list(pieces), QUESTIONS), config))
    pause = asyncio.run(graphe.ainvoke(None, config))
    assert attente(pause) is not None
    reprendre(graphe, config, VALIDER)
    assert instantane(magasin) == attendu  # aucun événement en double
    assert modele.appels["evenements"] == modele_simple.appels["evenements"]  # et le modèle n'a pas été redemandé pour cette pièce


def test_une_panne_pendant_l_analyse_ne_rouvre_pas_la_question(magasin):
    attendu, _modele_simple = code_simple()
    sauvegarde, pieces = sauvegarde_memoire(), fixtures.generer()
    modele = Modele(panne_sur=("analyse", 2))
    graphe = construire_graphe(magasin, modele, pieces, sauvegarde)
    config = configuration("t")
    asyncio.run(graphe.ainvoke(entree("A1", "Atelier Marin", list(pieces), QUESTIONS), config))
    with pytest.raises(RuntimeError, match="panne simulée"):
        reprendre(graphe, config, VALIDER)
    assert len(magasin.enjeux("A1")) == 2  # la 2e question est ouverte, pas encore analysée
    fin = asyncio.run(graphe.ainvoke(None, config))
    assert fin["statut"] == "terminé" and len(magasin.enjeux("A1")) == 2
    assert instantane(magasin) == attendu


def test_une_panne_pendant_la_prise_en_compte_de_la_reponse_se_rejoue_telle_quelle(magasin):
    graphe, config, _pause = lancer(magasin, Modele(), questions=[])
    corriger, tombee = magasin.corriger_evenement, []

    def corriger_ou_tomber(event_id, champs):
        if not tombee:
            tombee.append(event_id)
            raise RuntimeError("panne simulée de la base")
        corriger(event_id, champs)

    magasin.corriger_evenement = corriger_ou_tomber
    with pytest.raises(RuntimeError, match="base"):
        reprendre(graphe, config, VALIDER)
    # Pas de nouvelle réponse à donner : LangGraph rejoue celle de l'avocat.
    fin = asyncio.run(graphe.ainvoke(None, config))
    assert fin["statut"] == "terminé" and fin["validation"]["decision"] == "valider"


def test_des_pieces_introuvables_sont_refusees_avant_tout_import(magasin):
    graphe = construire_graphe(magasin, Modele(), {"a.pdf": b""}, sauvegarde_memoire())
    with pytest.raises(ValueError, match="introuvables"):
        asyncio.run(graphe.ainvoke(entree("A1", "x", ["a.pdf", "b.pdf"], []), configuration("t")))
    assert magasin.documents("A1") == []


# ---------- Le point de reprise dans Postgres ----------

def test_postgres_le_fil_survit_a_la_perte_du_processus():
    if not URL:
        pytest.skip("TEST_DATABASE_URL absent")
    attendu, _ = code_simple()
    thread, pieces = f"test-{uuid.uuid4()}", fixtures.generer()
    stockage = Postgres(URL)
    stockage.preparer()
    magasin = MagasinPostgres(stockage)
    magasin.vider()

    async def premier_processus():
        async with sauvegarde_postgres(URL) as sauvegarde:
            graphe = construire_graphe(magasin, Modele(), pieces, sauvegarde)
            return attente(await graphe.ainvoke(entree("A1", "Atelier Marin", list(pieces), QUESTIONS), configuration(thread)))

    async def second_processus():
        # Tout est neuf : connexion, graphe, faux modèle. Seuls la base et le nom du fil sont communs.
        async with sauvegarde_postgres(URL) as sauvegarde:
            modele = Modele()
            graphe = construire_graphe(MagasinPostgres(stockage), modele, pieces, sauvegarde)
            en_attente = await proposition_en_attente(graphe, thread)
            fin = await graphe.ainvoke(Command(resume=VALIDER), configuration(thread))
            return en_attente, fin, modele

    try:
        proposition = asyncio.run(premier_processus())
        assert proposition is not None and magasin.enjeux("A1") == []
        en_attente, fin, modele = asyncio.run(second_processus())
        assert en_attente == proposition and fin["statut"] == "terminé"
        assert modele.appels["classement"] == 0 and modele.appels["evenements"] == 0  # l'import n'a pas été refait
        assert instantane(magasin) == attendu
    finally:
        with stockage._connexion() as c:  # le point de reprise de ce fil ne doit pas rester dans la base de test
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                c.execute(f"DELETE FROM {table} WHERE thread_id = %s", (thread,))
        magasin.vider()
        stockage.fermer()
