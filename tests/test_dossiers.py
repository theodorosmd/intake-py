"""Import, classement, doublons, chronologie sourcée : testés avec un faux modèle (aucun réseau).

Le faux modèle relève les lignes qui contiennent une date écrite en toutes lettres. La qualité
du vrai modèle se mesure à part (python -m dossiers.demo) ; ici on teste la logique du code :
ce qu'il garde, ce qu'il refuse, ce qu'il signale.
"""

import asyncio
import os
import re

import pytest

from dossiers import citations, fixtures, lecture
from dossiers.importer import CONSIGNE_CLASSEMENT, importer
from dossiers.magasin import MagasinMemoire, MagasinPostgres
from stockage import Postgres

URL = os.environ.get("TEST_DATABASE_URL")
MOIS = {"janvier": 1, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6}
DATE = re.compile(r"(\d{1,2})(?:er)? (janvier|février|mars|avril|mai|juin) (\d{4})")


def iso(m: re.Match) -> str:
    return f"{m.group(3)}-{MOIS[m.group(2)]:02d}-{int(m.group(1)):02d}"


def faux_modele(inventer: bool = False):
    async def appeler(consigne: str, texte: str) -> dict:
        if consigne == CONSIGNE_CLASSEMENT:
            t = texte.lower()
            type_doc = ("contrat" if "contrat de vente" in t else "bon_livraison" if "bon de livraison" in t
                        else "facture" if "facture" in t else "courriel" if "subject:" in t else "courrier")
            m = DATE.search(texte)
            return {"type": type_doc, "date": iso(m) if m else None, "parties": ["Atelier Marin", "Nordik Équipements"], "titre": type_doc}
        evenements, page = [], 1
        for ligne in texte.splitlines():
            if ligne.startswith("[page "):
                page = int(ligne[6:-1])
            elif (m := DATE.search(ligne)):
                evenements.append({"date": iso(m), "title": ligne[:70], "page": page, "quote": ligne})
        if inventer:
            evenements.append({"date": "2026-03-20", "title": "Mise en demeure envoyée", "page": 1,
                               "quote": "Le Vendeur a reçu une mise en demeure le 20 mars"})
        return {"events": evenements}

    return appeler


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


def importer_tout(magasin, inventer=False, noms=None):
    magasin.creer_affaire("A1", "Atelier Marin c. Nordik")
    pieces = fixtures.generer()
    bilans = {}

    async def tout():
        for nom in noms or pieces:
            bilans[nom] = await importer(magasin, "A1", nom, pieces[nom], faux_modele(inventer))

    asyncio.run(tout())
    return bilans


def test_les_citations_doivent_exister_dans_la_page():
    page = "Le Vendeur livre la machine\nau plus tard le 15 mars 2026."
    assert citations.citation_presente("livre la machine au plus tard le 15 MARS 2026", page)
    assert not citations.citation_presente("livre la machine le 31 mars 2026", page)
    assert not citations.citation_presente("mars", page)  # trop court pour prouver quoi que ce soit
    assert not citations.citation_presente(None, page)


def test_les_formats_sont_lus_et_les_scans_signales():
    pieces = fixtures.generer()
    assert all(p["readable"] for p in lecture.lire("contrat-vente.pdf", pieces["contrat-vente.pdf"]))
    assert not lecture.lire("scan-illisible.pdf", pieces["scan-illisible.pdf"])[0]["readable"]
    assert "Subject: Livraison" in lecture.lire("email-2026-03-10.eml", pieces["email-2026-03-10.eml"])[0]["content"]
    with pytest.raises(ValueError):
        lecture.lire("tableau.xlsx", b"")


def test_le_classement_repere_doublons_versions_et_scans(magasin):
    bilans = importer_tout(magasin)
    assert bilans["contrat-vente.pdf"]["status"] == "ok"
    assert bilans["contrat-vente.pdf"]["doc_type"] == "contrat"
    assert bilans["bon-livraison-copie.pdf"]["status"] == "doublon"
    assert bilans["contrat-vente-projet.pdf"]["status"] == "version"
    assert bilans["scan-illisible.pdf"]["status"] == "illisible"
    assert any("à relire" in f for f in bilans["scan-illisible.pdf"]["flags"])
    # les pièges prévus par les fixtures sont exactement ceux détectés
    detectes = {n: b["status"] for n, b in bilans.items() if b["status"] != "ok"}
    assert detectes == fixtures.PIEGES


def test_un_fichier_identique_est_un_doublon_sans_appel_au_modele(magasin):
    magasin.creer_affaire("A1", "x")
    pieces = fixtures.generer()
    appels = []

    async def appeler(consigne, texte):
        appels.append(consigne)
        return await faux_modele()(consigne, texte)

    async def deux_fois():
        await importer(magasin, "A1", "facture.pdf", pieces["facture.pdf"], appeler)
        n = len(appels)
        b = await importer(magasin, "A1", "facture-bis.pdf", pieces["facture.pdf"], appeler)
        return n, b

    n, bilan = asyncio.run(deux_fois())
    assert bilan["status"] == "doublon" and len(appels) == n
    assert bilan["evenements"] == 0


def test_la_chronologie_ne_garde_que_les_evenements_cites_mot_pour_mot(magasin):
    bilans = importer_tout(magasin, inventer=True, noms=["contrat-vente.pdf", "email-2026-03-10.eml"])
    assert any("mise en demeure" in r.lower() for r in bilans["contrat-vente.pdf"]["refuses"])
    evenements = magasin.evenements("A1")
    assert evenements and all(e["quote"] for e in evenements)
    assert not any("mise en demeure" in e["title"].lower() for e in evenements)
    # chaque événement retenu renvoie à une page qui contient bien sa citation
    for e in evenements:
        pages = {p["page_no"]: p["content"] for p in magasin.pages(e["doc_id"])}
        assert citations.citation_presente(e["quote"], pages[e["page_no"]])


def test_la_chronologie_est_triee_et_ignore_les_doublons_et_les_scans(magasin):
    importer_tout(magasin)
    evenements = magasin.evenements("A1")
    dates = [e["event_date"] for e in evenements if e["event_date"]]
    assert dates == sorted(dates)
    documents = {d["doc_id"]: d for d in magasin.documents("A1")}
    assert all(documents[e["doc_id"]]["status"] not in {"doublon", "illisible"} for e in evenements)
    assert any(e["event_date"].isoformat() == "2026-03-28" for e in evenements)


def test_l_original_est_conserve_a_l_identique(magasin):
    importer_tout(magasin, noms=["facture.pdf"])
    (doc,) = magasin.documents("A1")
    assert magasin.original(doc["doc_id"]) == fixtures.generer()["facture.pdf"]


def test_corriger_un_evenement_et_marquer_une_date_contestee(magasin):
    importer_tout(magasin, noms=["contrat-vente.pdf"])
    e = magasin.evenements("A1")[0]
    magasin.corriger_evenement(e["event_id"], {"status": "disputed", "note": "Date contestée par le vendeur", "event_date": "2026-03-12"})
    corrige = next(x for x in magasin.evenements("A1") if x["event_id"] == e["event_id"])
    assert corrige["status"] == "disputed" and corrige["event_date"].isoformat() == "2026-03-12"
    assert corrige["quote"] == e["quote"]  # la citation source ne change jamais
    with pytest.raises(ValueError):
        magasin.corriger_evenement(e["event_id"], {"quote": "autre chose"})
    with pytest.raises(ValueError):
        magasin.corriger_evenement(e["event_id"], {"status": "valide"})
    with pytest.raises(KeyError):
        magasin.corriger_evenement("inconnu", {"note": "x"})


def test_un_fichier_abime_ne_bloque_pas_l_import(magasin):
    magasin.creer_affaire("A1", "x")
    bilan = asyncio.run(importer(magasin, "A1", "casse.pdf", b"%PDF-1.4 tronque", faux_modele()))
    assert bilan["status"] == "illisible"


def test_une_date_doit_s_ecrire_dans_la_page():
    page = "Signé le 2 février 2026. Livraison au plus tard le 1er avril 2026. Reçu le 28/03/2026. Date: Tue, 10 Mar 2026"
    for jour in ("2026-02-02", "2026-04-01", "2026-03-28", "2026-03-10"):
        assert citations.date_dans_page(jour, page), jour
    assert not citations.date_dans_page("2026-03-31", "Nous pourrons livrer vers fin mars 2026.")
    assert not citations.date_dans_page("2026-03-15", page)


def test_une_date_non_retrouvee_est_signalee_sans_etre_supprimee(magasin):
    magasin.creer_affaire("A1", "x")
    pieces = fixtures.generer()

    async def modele(consigne, texte):
        if consigne == CONSIGNE_CLASSEMENT:
            return {"type": "courriel", "date": None, "parties": [], "titre": ""}
        return {"events": [{"date": "2026-03-30", "title": "Livraison annoncée pour fin mars", "page": 1,
                            "quote": "nous pourrons livrer la machine vers le 31 mars"}]}

    asyncio.run(importer(magasin, "A1", "email-2026-03-10.eml", pieces["email-2026-03-10.eml"], modele))
    (e,) = magasin.evenements("A1")
    assert e["event_date"].isoformat() == "2026-03-30"
    assert "date non retrouvée" in e["note"]


def test_un_jour_et_un_mois_sans_annee_sont_acceptes_mais_signales():
    page = "La machine a été livrée le 28 mars alors que le contrat prévoyait le 15 mars."
    assert citations.date_dans_page("2026-03-28", page) == "sans_annee"
    assert citations.date_dans_page("2026-03-28", "Reçu le 28 mars 2026.") == "complete"
    assert citations.date_dans_page("2026-03-29", page) is None


def test_les_evenements_d_un_projet_proche_du_contrat_sont_avertis(magasin):
    importer_tout(magasin, noms=["contrat-vente.pdf", "contrat-vente-projet.pdf"])
    documents = {d["doc_id"]: d["filename"] for d in magasin.documents("A1")}
    du_projet = [e for e in magasin.evenements("A1") if documents[e["doc_id"]] == "contrat-vente-projet.pdf"]
    du_contrat = [e for e in magasin.evenements("A1") if documents[e["doc_id"]] == "contrat-vente.pdf"]
    assert du_projet and all("peut ne pas être la version retenue" in e["note"] for e in du_projet)
    assert du_contrat and not any("version retenue" in e["note"] for e in du_contrat)


# ---------- Questions litigieuses ----------

from dossiers.enjeux import analyser


def faux_analyste(extras=()):
    async def appeler(consigne, texte):
        assert "QUESTION :" in texte and "contrat-vente.pdf" in texte
        assert "bon-livraison-copie.pdf" not in texte and "scan-illisible.pdf" not in texte  # ni doublons ni scans
        return {"evidence": [
            {"document": "email-2026-03-11.eml", "page": 1, "stance": "supports",
             "quote": "D'accord pour fin mars, sous réserve de confirmation écrite par votre direction.", "why": "L'acheteur accepte fin mars."},
            {"document": "contrat-vente.pdf", "page": 2, "stance": "contradicts",
             "quote": "Toute modification du présent contrat doit faire l'objet d'un avenant écrit signé par les deux parties.", "why": "Article 4."},
            {"document": "contrat-vente-projet.pdf", "page": 2, "stance": "context",
             "quote": "Le Vendeur livre la machine au plus tard le 1er avril 2026", "why": "Date du projet."},
            *extras,
        ], "missing": ["Confirmation écrite de la direction du vendeur", "  "]}

    return appeler


def test_les_pieces_d_une_question_sont_verifiees_et_les_manques_gardes(magasin):
    importer_tout(magasin)
    q = magasin.ajouter_enjeu("A1", "Le retard a-t-il été accepté par l'acheteur ?")
    bilan = asyncio.run(analyser(magasin, "A1", q, faux_analyste()))
    assert bilan["retenues"] == 3 and bilan["refusees"] == [] and bilan["manques"] == 1
    (enjeu,) = magasin.enjeux("A1")
    assert enjeu["manques"] == ["Confirmation écrite de la direction du vendeur"]
    documents = {d["doc_id"]: d["filename"] for d in magasin.documents("A1")}
    par_stance = {p["stance"]: documents[p["doc_id"]] for p in enjeu["preuves"]}
    assert par_stance["supports"] == "email-2026-03-11.eml" and par_stance["contradicts"] == "contrat-vente.pdf"
    projet = next(p for p in enjeu["preuves"] if documents[p["doc_id"]] == "contrat-vente-projet.pdf")
    assert "version" in projet["warning"]  # une pièce de projet n'est pas présentée comme définitive


def test_une_citation_inventee_ou_mal_placee_est_refusee(magasin):
    importer_tout(magasin)
    q = magasin.ajouter_enjeu("A1", "Le retard a-t-il été accepté par l'acheteur ?")
    extras = [
        {"document": "email-2026-03-11.eml", "page": 1, "stance": "supports", "quote": "Nous acceptons le retard sans pénalité de la part du vendeur"},
        {"document": "contrat-vente.pdf", "page": 1, "stance": "supports", "quote": "Toute modification du présent contrat doit faire l'objet d'un avenant écrit"},
        {"document": "bon-livraison-copie.pdf", "page": 1, "stance": "supports", "quote": "Reçu le 28 mars 2026 par M. Paul Durand"},
        {"document": "email-2026-03-11.eml", "page": "x", "stance": "supports", "quote": "D'accord pour fin mars"},
    ]
    bilan = asyncio.run(analyser(magasin, "A1", q, faux_analyste(extras)))
    assert bilan["retenues"] == 3 and len(bilan["refusees"]) == 4  # l'inventée, la mauvaise page, la copie, la page illisible


def test_reanalyser_remplace_l_analyse_precedente(magasin):
    importer_tout(magasin)
    q = magasin.ajouter_enjeu("A1", "Question")
    asyncio.run(analyser(magasin, "A1", q, faux_analyste()))
    asyncio.run(analyser(magasin, "A1", q, faux_analyste()))
    assert len(magasin.enjeux("A1")[0]["preuves"]) == 3


# ---------- Questions libres ----------

from dossiers.questions import repondre


def _dossier_vente(magasin):
    magasin.creer_affaire("A1", "Test")
    for nom, octets in fixtures.generer().items():
        asyncio.run(importer(magasin, "A1", nom, octets, faux_modele()))


def _reponse(**kw):
    async def appeler(consigne, texte):
        return kw

    return appeler


def _poser(magasin, appeler, question="?"):
    return asyncio.run(repondre(magasin, "A1", question, appeler))


def test_question_repondue_avec_citation_verifiee(magasin):
    _dossier_vente(magasin)
    r = _poser(magasin, _reponse(found=True, answer="Livraison au plus tard le 15 mars 2026.", claims=[
        {"text": "Date limite", "document": "contrat-vente.pdf", "page": 2, "quote": "au plus tard le 15 mars 2026"}]))
    assert r["statut"] == "repondu" and r["sources"][0]["filename"] == "contrat-vente.pdf" and not r["refusees"]


def test_question_sans_reponse_dans_le_dossier(magasin):
    _dossier_vente(magasin)
    r = _poser(magasin, _reponse(found=False, claims=[], answer="Je pense que c'est FR12."))
    assert r["statut"] == "non_trouve" and r["reponse"] == "Le dossier ne le dit pas." and not r["sources"]


def test_affirmation_inventee_refusee_et_le_modele_ne_peut_pas_forcer_une_reponse(magasin):
    _dossier_vente(magasin)
    r = _poser(magasin, _reponse(found=True, answer="Pénalité de 5 %.", claims=[
        {"text": "Pénalité", "document": "contrat-vente.pdf", "page": 2, "quote": "pénalité de cinq pour cent par jour"}]))
    assert r["statut"] == "non_trouve" and len(r["refusees"]) == 1 and r["reponse"] == "Le dossier ne le dit pas."


def test_reponse_partielle_quand_une_citation_sur_deux_est_fausse(magasin):
    _dossier_vente(magasin)
    r = _poser(magasin, _reponse(found=True, answer="x", claims=[
        {"text": "a", "document": "contrat-vente.pdf", "page": 2, "quote": "au plus tard le 15 mars 2026"},
        {"text": "b", "document": "contrat-vente.pdf", "page": 1, "quote": "texte qui n'existe pas dans la page"}]))
    assert r["statut"] == "partiel" and len(r["sources"]) == 1 and len(r["refusees"]) == 1


def test_citation_dans_un_doublon_est_refusee(magasin):
    _dossier_vente(magasin)
    r = _poser(magasin, _reponse(found=True, answer="x", claims=[
        {"text": "a", "document": "bon-livraison-copie.pdf", "page": 1, "quote": "Reçu le 28 mars 2026"}]))
    assert r["statut"] == "non_trouve"


# ---------- Dossier fictif n° 2 (essai à l'aveugle) : le jeu de pièces lui-même ----------

from dossiers import fixtures_travail as ft


def test_dossier_travail_les_pieces_se_lisent_et_les_dates_attendues_y_sont():
    pieces = ft.generer()
    textes = {n: " ".join(p["content"] for p in lecture.lire(n, o)) for n, o in pieces.items()}
    assert set(ft.TYPES_ATTENDUS) <= set(pieces) and set(ft.PIEGES) <= set(pieces)
    assert "2 150" in textes["contrat-travail.pdf"] and "faute grave" in textes["lettre-licenciement.pdf"]
    assert not any(p["readable"] for p in lecture.lire("scan-illisible.pdf", pieces["scan-illisible.pdf"]))
    for nom, dates in ft.DATES_ATTENDUES.items():
        pages = [p["content"] for p in lecture.lire(nom, pieces[nom])]
        assert any(citations.date_dans_page(d, page) for d in dates for page in pages), nom


# ---------- Corrections issues de l'essai à l'aveugle ----------

def test_plages_de_dates_reconnues():
    page = "Semaine du 2 au 8 mars : 41 heures. Absence du 8 au 12 avril 2026."
    assert citations.date_dans_page("2026-03-02", page) == "sans_annee" and citations.date_dans_page("2026-03-08", page) == "sans_annee"
    assert citations.date_dans_page("2026-04-12", page) == "complete" and citations.date_dans_page("2026-04-08", page) == "complete"
    assert citations.date_dans_page("2026-03-14", page) is None


def test_deux_bulletins_de_paie_ne_sont_pas_des_versions(magasin):
    magasin.creer_affaire("A1", "T")
    modele = fixtures_travail_paies()
    for nom, octets in modele.items():
        b = asyncio.run(importer(magasin, "A1", nom, octets, _modele_autre))
        assert b["status"] == "ok", (nom, b["flags"])


def fixtures_travail_paies():
    from dossiers import fixtures_travail as ft

    p = ft.generer()
    return {n: p[n] for n in ("bulletin-paie-2026-01.pdf", "bulletin-paie-2026-03.pdf")}


async def _modele_autre(consigne, texte):
    if consigne == CONSIGNE_CLASSEMENT:
        return {"type": "autre", "date": None, "parties": [], "titre": "paie"}
    return {"events": []}


def test_la_reponse_affichee_ne_contient_que_les_affirmations_verifiees(magasin):
    _dossier_vente(magasin)
    r = _poser(magasin, _reponse(found=True, answer="Texte libre non sourcé.", claims=[
        {"text": "Livraison au plus tard le 15 mars 2026", "document": "contrat-vente.pdf", "page": 2, "quote": "au plus tard le 15 mars 2026"}]))
    assert r["reponse"] == "Livraison au plus tard le 15 mars 2026." and "libre" not in r["reponse"]
