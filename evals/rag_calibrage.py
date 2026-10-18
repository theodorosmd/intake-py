"""Calibre le seuil de pertinence du RAG avec le vrai modèle d'embeddings.

Pose des questions dont on connaît la bonne section (ou dont on sait qu'aucune section ne
répond) et affiche les scores. Le bon seuil laisse passer toutes les bonnes réponses et bloque
les questions hors sujet.

    python -m evals.rag_calibrage        (OPENAI_API_KEY dans .env ; rien n'est écrit en base)
"""

import asyncio

from dotenv import load_dotenv

load_dotenv()

import rag  # après load_dotenv : la clé doit être chargée
from stockage import Memoire

QUESTIONS = [
    ("How much does a lawyer cost? Do I pay anything upfront?", "honoraires.md#how-the-firm-is-paid"),
    ("Is the consultation free?", "honoraires.md#price-of-the-consultation"),
    ("Does anyone there speak Spanish?", "cabinet.md#languages"),
    ("What time is your office open?", "cabinet.md#office-hours-and-location"),
    ("Can you help me with my divorce?", "cabinet.md#cases-the-firm-does-not-handle"),
    ("Do you take dog bite cases?", "cabinet.md#cases-the-firm-handles"),
    ("What should I bring to the meeting?", "consultation.md#what-to-prepare-for-the-consultation"),
    ("How long do I have to file a claim?", "questions-juridiques.md#deadlines-chances-of-winning-and-amounts"),
    ("Can I move my appointment to next week?", "consultation.md#changing-or-cancelling-a-consultation"),
    # Pas de réponse exacte dans la base, mais la section du bureau est la bonne à remonter :
    # elle permet de dire « le bureau est à Lower Manhattan » sans rien inventer.
    ("Is there free parking at your office?", "cabinet.md#office-hours-and-location"),
    ("Do you have an office in Brooklyn?", "cabinet.md#office-hours-and-location"),
    ("Quel temps fera-t-il demain à Marseille ?", None),
    ("I want to buy a used car, what do you recommend?", None),
    ("What's the weather tomorrow?", None),
    ("Can you order me a pizza?", None),
]


async def principal() -> None:
    stockage = Memoire()
    print(f"Index : {await rag.indexer(stockage)}\n")
    pertinents, hors_sujet = [], []
    for question, attendue in QUESTIONS:
        (vecteur,) = await rag.vectoriser([question])
        trouves = stockage.chercher_connaissances(vecteur, 3)
        premier = trouves[0]
        juste = attendue is None or premier["id"] == attendue
        print(f"{'OK ' if juste else 'MAUVAISE SECTION'} {premier['score']:.3f}  {question}")
        print(f"      -> {premier['id']}" + (f" (attendu {attendue})" if not juste else ""))
        (hors_sujet if attendue is None else pertinents).append(premier["score"])
    print(f"\nPlus petit score d'une bonne réponse : {min(pertinents):.3f}")
    print(f"Plus grand score d'une question hors sujet : {max(hors_sujet):.3f}")
    print(f"Seuil actuel (RAG_SEUIL) : {rag.SEUIL}")
    if max(hors_sujet) < rag.SEUIL <= min(pertinents):
        print("Le seuil sépare bien les deux groupes.")
    elif max(hors_sujet) < min(pertinents):
        print(f"À ajuster : RAG_SEUIL={(max(hors_sujet) + min(pertinents)) / 2:.2f} sépare les deux groupes.")
    else:
        print("Les deux groupes se chevauchent : aucun seuil ne les sépare, il faut revoir les sections.")


if __name__ == "__main__":
    asyncio.run(principal())
