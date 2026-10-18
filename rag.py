"""Recherche dans la base de connaissances du cabinet (RAG).

Les fichiers Markdown de connaissances/ sont découpés par section (`## …`), chaque section est
transformée en vecteur (embedding) et rangée dans le stockage (pgvector en production). Pendant
un appel, l'agent pose une question avec l'outil search_firm_info : la question devient un
vecteur, et on renvoie les sections les plus proches, avec leur source.

L'indexation est incrémentale : une empreinte (sha256) par section, seules les sections
nouvelles ou modifiées sont recalculées, celles qui ont disparu sont retirées.
"""

import asyncio
import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import litellm

from stockage import Stockage

logger = logging.getLogger("uvicorn.error")

DOSSIER = Path(__file__).with_name("connaissances")
DIMENSIONS = 1536  # text-embedding-3-small ; la colonne vector(1536) du schéma en dépend
NB_RESULTATS = 3
# En dessous, la section n'a probablement rien à voir avec la question : mieux vaut que
# l'agent dise qu'il ne sait pas plutôt que de répondre à côté. Réglé avec evals/rag_calibrage.py
# (18 oct. : bonnes réponses ≥ 0,26, questions hors sujet ≤ 0,19).
SEUIL = float(os.getenv("RAG_SEUIL", "0.22"))


@dataclass(frozen=True)
class Morceau:
    id: str  # "honoraires.md#how-the-firm-is-paid"
    source: str  # "honoraires.md"
    titre: str  # "Fees and costs — How the firm is paid"
    texte: str  # titre + contenu : c'est ce qui est vectorisé et renvoyé à l'agent

    @property
    def empreinte(self) -> str:
        return hashlib.sha256(self.texte.encode()).hexdigest()


def _slug(titre: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", titre.lower()).strip("-")


def _sections(texte: str, titre_defaut: str) -> tuple[str, list[tuple[str, str]]]:
    """Le titre du fichier (`# …`) et ses sections (`## …`) avec leur contenu sur une ligne."""
    titre, sections, courante, lignes = titre_defaut, [], None, []
    for ligne in [*texte.splitlines(), "## "]:  # la section sentinelle ferme la dernière
        if ligne.startswith("# "):
            titre = ligne[2:].strip()
        elif ligne.startswith("## "):
            corps = " ".join(x.strip() for x in lignes if x.strip())
            if courante and corps:
                sections.append((courante, corps))
            courante, lignes = ligne[3:].strip(), []
        else:
            lignes.append(ligne)
    return titre, sections


def decouper(dossier: Path = DOSSIER) -> list[Morceau]:
    """Une section `## …` = un morceau. Le README du dossier n'est pas une connaissance."""
    morceaux = []
    for fichier in sorted(dossier.glob("*.md")):
        if fichier.name.lower() == "readme.md":
            continue
        titre_fichier, sections = _sections(fichier.read_text(), fichier.stem)
        for section, corps in sections:
            titre = f"{titre_fichier} — {section}"
            morceaux.append(
                Morceau(f"{fichier.name}#{_slug(section)}", fichier.name, titre, f"{titre}\n{corps}")
            )
    return morceaux


async def vectoriser(textes: list[str]) -> list[list[float]]:
    """Appelle l'API d'embeddings (OpenAI directement, comme la synthèse vocale)."""
    reponse = await litellm.aembedding(
        model=os.getenv("EMBEDDING_MODEL", "openai/text-embedding-3-small"),
        input=textes,
        timeout=10,
    )
    return [d["embedding"] if isinstance(d, dict) else d.embedding for d in reponse.data]


async def indexer(stockage: Stockage, dossier: Path = DOSSIER) -> dict:
    """Met l'index à jour. Renvoie ce qui a été fait (pour les journaux et les tests)."""
    morceaux = decouper(dossier)
    connus = await asyncio.to_thread(stockage.empreintes_connaissances)
    a_calculer = [m for m in morceaux if connus.get(m.id) != m.empreinte]
    obsoletes = set(connus) - {m.id for m in morceaux}
    if a_calculer:
        vecteurs = await vectoriser([m.texte for m in a_calculer])
        await asyncio.to_thread(stockage.enregistrer_connaissances, list(zip(a_calculer, vecteurs)))
    if obsoletes:
        await asyncio.to_thread(stockage.supprimer_connaissances, obsoletes)
    bilan = {"sections": len(morceaux), "calculees": len(a_calculer), "retirees": len(obsoletes)}
    logger.info("Base de connaissances : %s", bilan)
    return bilan


async def rechercher(stockage: Stockage, question: str, nb: int = NB_RESULTATS) -> dict:
    debut = time.perf_counter()
    (vecteur,) = await vectoriser([question])
    trouves = await asyncio.to_thread(stockage.chercher_connaissances, vecteur, nb)
    retenus = [t for t in trouves if t["score"] >= SEUIL]
    logger.info(
        "RAG : %d ms, %s",
        (time.perf_counter() - debut) * 1000,
        [(t["id"], round(t["score"], 2)) for t in trouves],
    )
    return {
        "results": [
            {"source": t["id"], "title": t["title"], "text": t["text"], "score": round(t["score"], 3)}
            for t in retenus
        ],
        "note": None
        if retenus
        else "Nothing in the firm's information answers this. Say you do not have that "
        "information and that the attorney can cover it at the consultation.",
    }
