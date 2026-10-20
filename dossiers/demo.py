"""Essai complet sur le dossier fictif Atelier Marin, avec le vrai modèle, puis notation.

    python -m dossiers.demo            dossier Atelier Marin (celui sur lequel les consignes ont été réglées)
    python -m dossiers.demo travail    dossier Armand c. Brasserie Le Mistral (essai à l'aveugle, autre domaine)
    (clé du modèle dans .env, comme pour le rejeu ; rien n'est écrit en base)

Les « attendus » ci-dessous ont été écrits à la main d'après les pièces de fixtures.py. Ils servent
à mesurer le vrai modèle : combien d'événements importants il retrouve, combien de ses propositions
la vérification des citations écarte, et si les pièges (copie, projet, scan) sont bien repérés.
"""

import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from dossiers import fixtures
from dossiers.enjeux import analyser
from dossiers.importer import importer
from dossiers.llm import appeler_json
from dossiers.magasin import MagasinMemoire
from dossiers.questions import repondre

TYPES_ATTENDUS = {
    "contrat-vente.pdf": "contrat", "contrat-vente-projet.pdf": "contrat", "bon-livraison.pdf": "bon_livraison",
    "facture.pdf": "facture", "annulation-commande-client.pdf": "courrier",
    "email-2026-03-10.eml": "courriel", "email-2026-03-11.eml": "courriel", "email-2026-04-05-reclamation.eml": "courriel",
}
# Pour chaque pièce, les dates qu'un lecteur attentif retiendrait (au moins une doit être trouvée).
DATES_ATTENDUES = {
    "contrat-vente.pdf": ["2026-02-02", "2026-03-15"],
    "bon-livraison.pdf": ["2026-03-28"],
    "facture.pdf": ["2026-03-28"],
    "annulation-commande-client.pdf": ["2026-04-02"],
    "email-2026-03-10.eml": ["2026-03-10", "2026-03-31"],
    "email-2026-03-11.eml": ["2026-03-11"],
    "email-2026-04-05-reclamation.eml": ["2026-04-05", "2026-03-28", "2026-03-15"],
}

# Questions litigieuses et ce qu'un lecteur attentif y trouverait : (fichier, positions acceptées).
# Les pièces clés doivent figurer, quelle que soit la position choisie, quand un juriste pourrait trancher
# dans les deux sens (le courriel « d'accord sous réserve » appuie ou contredit l'acceptation selon le point de vue).
# Seul cas sans ambiguïté : la lettre d'annulation invoque deux causes, elle doit figurer dans les deux sens.
TOUTES = {"supports", "contradicts", "context"}
ENJEUX = [
    ("La machine a-t-elle été livrée en retard par rapport au contrat ?",
     [("contrat-vente.pdf", TOUTES), ("bon-livraison.pdf", {"supports"})]),
    ("L'acheteur a-t-il accepté un report de la date de livraison ?",
     [("email-2026-03-11.eml", TOUTES), ("contrat-vente.pdf", TOUTES)]),
    ("Le retard de livraison a-t-il causé l'annulation de la commande de Maison Lucas ?",
     [("annulation-commande-client.pdf", {"supports"}), ("annulation-commande-client.pdf", {"contradicts"})]),
]
# Questions libres : (question, pièces à citer, la réponse doit être « le dossier ne le dit pas »)
QUESTIONS = [
    ("Quelle est la date de livraison prévue par le contrat signé ?", {"contrat-vente.pdf"}, False),
    ("Qui a reçu la machine, et quand ?", {"bon-livraison.pdf"}, False),
    ("Quel montant la facture réclame-t-elle ?", {"facture.pdf"}, False),
    # Corrigé après un essai : l'ancienne attente « le dossier ne le dit pas » était fausse, l'article 3 du contrat répond.
    ("Quel est le taux des pénalités de retard prévu au contrat signé ?", {"contrat-vente.pdf"}, False),
    ("Quel est le numéro de TVA de l'acheteur ?", set(), True),
    ("Le vendeur a-t-il été mis en demeure par huissier ?", set(), True),
]

CAS = {
    "vente": {"generer": fixtures.generer, "titre": "Atelier Marin c. Nordik Équipements", "pieges": fixtures.PIEGES,
                  "types": TYPES_ATTENDUS, "dates": DATES_ATTENDUES, "enjeux": ENJEUX, "questions": QUESTIONS},
}


async def principal(cas: dict) -> None:
    magasin = MagasinMemoire()
    magasin.creer_affaire("A1", cas["titre"])
    bilans = {}
    for nom, octets in cas["generer"]().items():
        bilans[nom] = await importer(magasin, "A1", nom, octets, appeler_json)

    print("\nPIÈCES")
    for nom, b in bilans.items():
        print(f"  {b['status']:<9} {b['doc_type']:<14} {nom}" + "".join(f"\n            ! {f}" for f in b["flags"]))
    noms = {d["doc_id"]: d["filename"] for d in magasin.documents("A1")}
    print("\nCHRONOLOGIE")
    for e in magasin.evenements("A1"):
        date = e["event_date"].isoformat() if e["event_date"] else "date ?   "
        alerte = f"  [{e['note']}]" if e["note"] else ""
        print(f"  {date}  {e['title'][:70]:<70}  {noms[e['doc_id']]} p.{e['page_no']}{alerte}")

    trouves = {(noms[e["doc_id"]], e["event_date"].isoformat()) for e in magasin.evenements("A1") if e["event_date"]}
    retrouves = sum(any((n, d) in trouves for d in dates) for n, dates in cas["dates"].items())
    bon_type = sum(bilans[n]["doc_type"] == t for n, t in cas["types"].items())
    refuses = [r for b in bilans.values() for r in b["refuses"]]
    retenus = len(magasin.evenements("A1"))
    non_retrouvees = sum(1 for e in magasin.evenements("A1") if "date non retrouvée" in e["note"])
    detectes = {n: b["status"] for n, b in bilans.items() if b["status"] != "ok"}

    print("\nQUESTIONS LITIGIEUSES")
    trouves_enjeux, attendus_enjeux, refusees_enjeux = 0, 0, []
    for question, attendu in cas["enjeux"]:
        issue_id = magasin.ajouter_enjeu("A1", question)
        bilan_q = await analyser(magasin, "A1", issue_id, appeler_json)
        refusees_enjeux += bilan_q["refusees"]
        enjeu = next(e for e in magasin.enjeux("A1") if e["issue_id"] == issue_id)
        print(f"\n  ? {question}")
        for p in enjeu["preuves"]:
            alerte = f"  [{p['warning']}]" if p["warning"] else ""
            print(f"    {p['stance']:<11} {noms[p['doc_id']]} p.{p['page_no']} : {p['quote'][:90]}{alerte}")
            print(f"                -> {p['why']}")
        for m in enjeu["manques"]:
            print(f"    manque      {m}")
        for nom, positions in attendu:
            attendus_enjeux += 1
            trouves_enjeux += any(noms[p["doc_id"]] == nom and p["stance"] in positions for p in enjeu["preuves"])

    print("\nQUESTIONS LIBRES")
    bonnes, refusees_libres = 0, []
    for question, a_citer, sans_reponse in cas["questions"]:
        r = await repondre(magasin, "A1", question, appeler_json)
        cites = {s["filename"] for s in r["sources"]}
        ok = r["statut"] == "non_trouve" if sans_reponse else (r["statut"] != "non_trouve" and a_citer <= cites)
        bonnes += ok
        refusees_libres += r["refusees"]
        print(f"\n  ? {question}\n    {'OK ' if ok else 'ÉCHEC'} [{r['statut']}] {r['reponse']}")
        for src in r["sources"]:
            print(f"    - {src['filename']} p.{src['page_no']} : {src['quote'][:90]}")
        if r["limites"]:
            print(f"    limites : {r['limites']}")

    print("\nRÉSULTATS")
    print(f"  Pièces bien classées : {bon_type}/{len(cas["types"])}")
    print(f"  Pièges repérés       : {'oui' if detectes == cas["pieges"] else 'NON'} {detectes}")
    print(f"  Pièces dont une date attendue est retrouvée : {retrouves}/{len(cas["dates"])}")
    print(f"  Événements retenus : {retenus}, dont {non_retrouvees} avec une date non retrouvée dans la page")
    print(f"  Propositions écartées (citation introuvable) : {len(refuses)}")
    for r in refuses:
        print(f"    - {r}")
    print(f"  Pièces attendues retrouvées dans les questions : {trouves_enjeux}/{attendus_enjeux}")
    print(f"  Citations refusées dans les questions : {len(refusees_enjeux)}")
    for r in refusees_enjeux:
        print(f"    - {r}")
    print(f"  Questions libres bien traitées : {bonnes}/{len(cas['questions'])}")
    print(f"  Citations refusées dans les questions libres : {len(refusees_libres)}")
    for r in refusees_libres:
        print(f"    - {r}")


if __name__ == "__main__":
    nom_cas = sys.argv[1] if len(sys.argv) > 1 else "vente"
    if nom_cas == "travail":
        from dossiers import fixtures_travail as ft

        CAS["travail"] = {"generer": ft.generer, "titre": ft.TITRE, "pieges": ft.PIEGES, "types": ft.TYPES_ATTENDUS,
                              "dates": ft.DATES_ATTENDUES, "enjeux": ft.ENJEUX, "questions": ft.QUESTIONS}
    asyncio.run(principal(CAS[nom_cas]))
