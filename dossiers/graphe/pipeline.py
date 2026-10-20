"""Le pipeline des dossiers comme graphe LangGraph : importer, relever, PAUSE pour l'avocat, analyser.

    ouvrir -> classer -> relever -> (pièce suivante : classer) ... -> valider -> ouvrir_enjeu -> analyser_enjeu -> ... -> conclure
                                                                          |
                                                              refus -> fin

Chaque nœud appelle le code existant (`classer_et_ranger`, `relever_evenements`, `analyser`) et ne fait
qu'une chose à la fois : le point de reprise est écrit après chaque nœud. Une panne pendant le relevé
des événements de la 5e pièce reprend donc à ce relevé, sans refaire les quatre premières pièces.

Ce que le point de reprise garde : l'avancement et les petits bilans (noms, identifiants, compteurs).
Ce qu'il ne garde pas : les fichiers, le magasin, le modèle. Ils sont donnés à `construire_graphe`,
et c'est à l'appelant de les redonner à la reprise (le texte des pièces vit dans le magasin, pas dans l'état).

Deux règles à retenir quand on écrit un nœud :
- un nœud interrompu est rejoué depuis son début à la reprise : ce qui précède `interrupt()` doit pouvoir
  se refaire sans effet de bord, les effets viennent après ;
- un nœud qui écrit dans le magasin doit pouvoir être rejoué sans doublon (voir `relever`).
"""

from collections.abc import Mapping
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from dossiers.enjeux import analyser
from dossiers.importer import classer_et_ranger, relever_evenements
from dossiers.llm import AppelJson
from dossiers.magasin import Magasin, _verifier_correction

DECISIONS = {"valider", "refuser"}


# _verifier_correction : la vérification même que fait magasin.corriger_evenement, pour refuser une correction avant la reprise.
def probleme_dans_la_reponse(reponse: object, evenements_connus: set[str]) -> str | None:
    """Ce qui empêche de prendre en compte la réponse de l'avocat, ou None si elle est valable.

    Une réponse refusée *après* la reprise laisserait le fil bloqué sur cette réponse (LangGraph la rejoue à
    chaque nouvel essai) : on la vérifie donc avant d'écrire quoi que ce soit, et on repose la question.
    """
    if not isinstance(reponse, dict):
        return "la réponse doit être un objet : {\"decision\": \"valider\" ou \"refuser\", \"corrections\": {...}}"
    if reponse.get("decision") not in DECISIONS:
        return f"decision attendue : {sorted(DECISIONS)}, reçu {reponse.get('decision')!r}"
    corrections = reponse.get("corrections") or {}
    if not isinstance(corrections, dict):
        return "corrections doit être un objet {identifiant de l'événement: champs corrigés}"
    for event_id, champs in corrections.items():
        if event_id not in evenements_connus:
            return f"événement inconnu : {event_id!r}"
        try:
            _verifier_correction(dict(champs))
        except (TypeError, ValueError) as erreur:
            return f"correction refusée pour {event_id} : {erreur}"
    return None


class Etat(TypedDict, total=False):
    matter_id: str
    titre: str
    pieces: list[str]           # noms des fichiers à importer, dans l'ordre
    questions: list[str]        # questions litigieuses à analyser après la validation
    a_importer: list[str]       # file d'attente : ce qui reste à importer
    piece_en_cours: str         # pièce rangée dont les événements restent à relever
    doc_en_cours: str
    bilans: dict[str, dict]     # par nom de pièce : statut, alertes, événements retenus ou refusés
    validation: dict            # ce que l'avocat a décidé
    a_poser: list[str]          # file d'attente des questions
    enjeu_en_cours: str         # question ouverte dont l'analyse reste à faire
    enjeux: dict[str, str]      # identifiant de la question -> texte
    analyses: dict[str, dict]   # identifiant de la question -> bilan de l'analyse
    statut: str                 # import | validé | refusé | terminé


def entree(matter_id: str, titre: str, pieces: list[str], questions: list[str]) -> dict:
    """L'état de départ d'une affaire."""
    return {"matter_id": matter_id, "titre": titre, "pieces": list(pieces), "questions": list(questions)}


def configuration(thread_id: str) -> dict:
    """Un fil = une affaire en cours : c'est lui qui retrouve le point de reprise."""
    return {"configurable": {"thread_id": thread_id}}


def attente(resultat: dict) -> dict | None:
    """La proposition faite à l'avocat si le graphe est en pause, sinon None."""
    interruptions = resultat.get("__interrupt__")
    return interruptions[0].value if interruptions else None


async def proposition_en_attente(graphe, thread_id: str) -> dict | None:
    """Pareil, mais en relisant le point de reprise : pour un processus qui n'a pas lancé le graphe lui-même."""
    instantane = await graphe.aget_state(configuration(thread_id))
    for tache in instantane.tasks:
        if tache.interrupts:
            return tache.interrupts[0].value
    return None


def construire_graphe(magasin: Magasin, appeler: AppelJson, fichiers: Mapping[str, bytes], sauvegarde: Any = None):
    """Assemble le graphe. `sauvegarde` : un point de reprise LangGraph (mémoire ou Postgres) ; sans lui, pas de pause possible."""

    async def ouvrir(etat: Etat) -> dict:
        manquants = [n for n in etat["pieces"] if n not in fichiers]
        if manquants:
            raise ValueError(f"pièces introuvables : {manquants}")
        magasin.creer_affaire(etat["matter_id"], etat.get("titre") or etat["matter_id"])
        return {"a_importer": list(etat["pieces"]), "a_poser": list(etat.get("questions", [])),
                "bilans": {}, "enjeux": {}, "analyses": {}, "statut": "import"}

    async def classer(etat: Etat) -> dict:
        nom, *reste = etat["a_importer"]
        bilan = await classer_et_ranger(magasin, etat["matter_id"], nom, fichiers[nom], appeler)
        return {"a_importer": reste, "piece_en_cours": nom, "doc_en_cours": bilan["doc_id"],
                "bilans": {**etat["bilans"], nom: bilan}}

    async def relever(etat: Etat) -> dict:
        matter_id, doc_id, nom = etat["matter_id"], etat["doc_en_cours"], etat["piece_en_cours"]
        deja = [e for e in magasin.evenements(matter_id) if e["doc_id"] == doc_id]
        if deja:
            # Panne après l'écriture des événements mais avant le point de reprise : on ne rappelle pas le modèle
            # et on n'écrit pas deux fois. (Les propositions refusées de cette pièce ne sont plus connues.)
            suite = {"evenements": len(deja), "refuses": []}
        else:
            suite = await relever_evenements(magasin, matter_id, doc_id, appeler)
        return {"bilans": {**etat["bilans"], nom: {**etat["bilans"][nom], **suite}}, "piece_en_cours": "", "doc_en_cours": ""}

    async def valider(etat: Etat) -> dict:
        matter_id = etat["matter_id"]
        noms = {d["doc_id"]: d["filename"] for d in magasin.documents(matter_id)}
        proposition = {
            "affaire": matter_id,
            "pieces": [{"piece": n, "statut": b["status"], "alertes": b["flags"], "evenements": b["evenements"],
                        "refuses": b["refuses"]} for n, b in etat["bilans"].items()],
            "chronologie": [{"event_id": e["event_id"], "date": e["event_date"].isoformat() if e["event_date"] else None,
                             "titre": e["title"], "piece": noms[e["doc_id"]], "page": e["page_no"], "note": e["note"]}
                            for e in magasin.evenements(matter_id)],
            "questions": etat.get("questions", []),
        }
        # La pause. Tout ce qui est au-dessus est rejoué à la reprise (lecture seule) ; tout ce qui est en dessous ne l'est qu'une fois.
        # Une réponse inutilisable repose la question (une 2e pause, avec le motif) au lieu de faire échouer le nœud.
        connus = {e["event_id"] for e in magasin.evenements(matter_id)}
        reponse = interrupt(proposition)
        while (motif := probleme_dans_la_reponse(reponse, connus)) is not None:
            reponse = interrupt({**proposition, "erreur": motif})

        decision = reponse["decision"]
        corrections = reponse.get("corrections") or {}
        for event_id, champs in corrections.items():
            magasin.corriger_evenement(event_id, champs)
        confirmes = 0
        if decision == "valider":
            # Valider confirme ce que le code a proposé sans réserve. Un événement signalé (« date non retrouvée »,
            # pièce proche d'un projet) reste « proposé » tant que l'avocat ne l'a pas traité lui-même.
            for e in magasin.evenements(matter_id):
                if e["status"] == "proposed" and not e["note"] and e["event_id"] not in corrections:
                    magasin.corriger_evenement(e["event_id"], {"status": "confirmed"})
                    confirmes += 1
        return {"validation": {"decision": decision, "corriges": len(corrections), "confirmes": confirmes},
                "statut": "validé" if decision == "valider" else "refusé"}

    async def ouvrir_enjeu(etat: Etat) -> dict:
        question, *reste = etat["a_poser"]
        issue_id = magasin.ajouter_enjeu(etat["matter_id"], question)
        return {"a_poser": reste, "enjeu_en_cours": issue_id, "enjeux": {**etat["enjeux"], issue_id: question}}

    async def analyser_enjeu(etat: Etat) -> dict:
        # `analyser` remplace l'analyse précédente de la question : rejouer ce nœud ne crée aucun doublon.
        issue_id = etat["enjeu_en_cours"]
        bilan = await analyser(magasin, etat["matter_id"], issue_id, appeler)
        return {"enjeu_en_cours": "", "analyses": {**etat["analyses"], issue_id: bilan}}

    async def conclure(etat: Etat) -> dict:
        return {"statut": "terminé"}

    def suite_import(etat: Etat) -> str:
        return "classer" if etat["a_importer"] else "valider"

    def suite_validation(etat: Etat) -> str:
        if etat["validation"]["decision"] != "valider":
            return END
        return "ouvrir_enjeu" if etat["a_poser"] else "conclure"

    def suite_analyse(etat: Etat) -> str:
        return "ouvrir_enjeu" if etat["a_poser"] else "conclure"

    g = StateGraph(Etat)
    for nom, noeud in [("ouvrir", ouvrir), ("classer", classer), ("relever", relever), ("valider", valider),
                       ("ouvrir_enjeu", ouvrir_enjeu), ("analyser_enjeu", analyser_enjeu), ("conclure", conclure)]:
        g.add_node(nom, noeud)
    g.add_edge(START, "ouvrir")
    g.add_conditional_edges("ouvrir", suite_import, ["classer", "valider"])
    g.add_edge("classer", "relever")
    g.add_conditional_edges("relever", suite_import, ["classer", "valider"])
    g.add_conditional_edges("valider", suite_validation, ["ouvrir_enjeu", "conclure", END])
    g.add_edge("ouvrir_enjeu", "analyser_enjeu")
    g.add_conditional_edges("analyser_enjeu", suite_analyse, ["ouvrir_enjeu", "conclure"])
    g.add_edge("conclure", END)
    return g.compile(checkpointer=sauvegarde)
