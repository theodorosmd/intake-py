"""Essai du graphe sur le dossier fictif Atelier Marin, avec le vrai modèle : import, pause, validation, analyse.

    python -m dossiers.graphe.demo                    tout dans ce processus : la chronologie s'affiche, tu valides au clavier
    python -m dossiers.graphe.demo --postgres         s'arrête à la pause ; l'affaire et le point de reprise sont dans la base (DATABASE_URL)
    python -m dossiers.graphe.demo --postgres --reprendre DG-20261019-201500 --decision valider
                                                      un autre processus relit la pause dans la base et continue

(clé du modèle dans .env, comme pour dossiers.demo.) Sans --postgres, rien n'est écrit en base.
Avec --postgres, l'affaire DG-... et les tables du point de reprise (checkpoints, ...) y restent : à toi de les supprimer.
"""

import argparse
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from langgraph.types import Command

from dossiers import demo as cas_demo
from dossiers import fixtures
from dossiers.graphe import (
    attente,
    configuration,
    construire_graphe,
    entree,
    proposition_en_attente,
)
from dossiers.graphe.sauvegarde import sauvegarde_memoire, sauvegarde_postgres
from dossiers.llm import appeler_json
from dossiers.magasin import MagasinMemoire, ouvrir_magasin
from stockage import STOCKAGE


def afficher_proposition(p: dict) -> None:
    print(f"\nPIÈCES ({p['affaire']})")
    for piece in p["pieces"]:
        print(f"  {piece['statut']:<9} {piece['evenements']} événement(s)  {piece['piece']}" + "".join(f"\n            ! {a}" for a in piece["alertes"]))
        for refus in piece["refuses"]:
            print(f"            - écarté : {refus}")
    print("\nCHRONOLOGIE PROPOSÉE (à valider)")
    for e in p["chronologie"]:
        alerte = f"  [{e['note']}]" if e["note"] else ""
        print(f"  {e['event_id']}  {e['date'] or 'date ?   '}  {e['titre'][:60]:<60}  {e['piece']} p.{e['page']}{alerte}")
    if p.get("erreur"):
        print(f"\n  Réponse refusée : {p['erreur']}")


def demander_decision(deja: str | None) -> dict:
    """Valider ou refuser, au clavier ou par --decision. Les corrections se font ici en une ligne : « ID=AAAA-MM-JJ ». """
    if deja:
        return {"decision": deja}
    saisie = input("\nValider cette chronologie ? [valider / refuser], ou corriger : ID=AAAA-MM-JJ (puis valider) : ").strip()
    corrections = {}
    while "=" in saisie:
        event_id, jour = (x.strip() for x in saisie.split("=", 1))
        corrections[event_id] = {"event_date": jour, "status": "confirmed"}
        saisie = input("  autre correction, ou valider / refuser : ").strip()
    return {"decision": saisie or "valider", "corrections": corrections}


def afficher_fin(magasin, matter_id: str, fin: dict) -> None:
    print(f"\nSTATUT : {fin['statut']}  {fin.get('validation', {})}")
    noms = {d["doc_id"]: d["filename"] for d in magasin.documents(matter_id)}
    for enjeu in magasin.enjeux(matter_id):
        print(f"\n  ? {enjeu['question']}")
        for p in enjeu["preuves"]:
            print(f"    {p['stance']:<11} {noms[p['doc_id']]} p.{p['page_no']} : {p['quote'][:90]}")
        for manque in enjeu["manques"]:
            print(f"    manque      {manque}")


@asynccontextmanager
async def _sauvegarde(postgres: bool):
    if postgres:
        async with sauvegarde_postgres(os.environ["DATABASE_URL"]) as s:
            yield s
    else:
        yield sauvegarde_memoire()


async def principal(appeler, magasin, postgres: bool, reprendre: str | None, decision: str | None) -> None:
    pieces = fixtures.generer()
    async with _sauvegarde(postgres) as sauvegarde:
        graphe = construire_graphe(magasin, appeler, pieces, sauvegarde)
        if reprendre:
            matter_id = reprendre
            proposition = await proposition_en_attente(graphe, matter_id)
            if proposition is None:
                print(f"Rien en attente pour {matter_id}.")
                return
        else:
            matter_id = f"DG-{datetime.now():%Y%m%d-%H%M%S}"
            questions = [q for q, _ in cas_demo.ENJEUX]
            resultat = await graphe.ainvoke(entree(matter_id, cas_demo.CAS["vente"]["titre"], list(pieces), questions), configuration(matter_id))
            proposition = attente(resultat)
            if postgres:
                afficher_proposition(proposition)
                print(f"\nPause. Pour continuer, depuis ce processus ou un autre :\n  python -m dossiers.graphe.demo --postgres --reprendre {matter_id} --decision valider")
                return
        while True:
            afficher_proposition(proposition)
            resultat = await graphe.ainvoke(Command(resume=demander_decision(decision)), configuration(matter_id))
            proposition = attente(resultat)
            if proposition is None:
                break
            decision = None  # réponse refusée : on repose la question au clavier
        afficher_fin(magasin, matter_id, resultat)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--postgres", action="store_true", help="écrire l'affaire et le point de reprise dans DATABASE_URL")
    parser.add_argument("--reprendre", metavar="AFFAIRE", help="reprendre une affaire en pause (avec --postgres)")
    parser.add_argument("--decision", choices=["valider", "refuser"], help="répondre sans clavier")
    args = parser.parse_args()
    if args.reprendre and not args.postgres:
        parser.error("--reprendre n'a de sens qu'avec --postgres : la mémoire disparaît avec le processus")
    if args.postgres:
        if not os.environ.get("DATABASE_URL"):
            parser.error("--postgres demande DATABASE_URL")
        STOCKAGE.preparer()
        magasin = ouvrir_magasin(STOCKAGE)
    else:
        magasin = MagasinMemoire()
    try:
        asyncio.run(principal(appeler_json, magasin, args.postgres, args.reprendre, args.decision))
    finally:
        if args.postgres:
            STOCKAGE.fermer()


if __name__ == "__main__":
    main()
