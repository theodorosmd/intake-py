"""Poser une question libre sur tout le dossier : réponse sourcée, ou « le dossier ne le dit pas ».

Le modèle propose une réponse faite d'affirmations, chacune avec une citation. Le code ne garde
une affirmation que si sa citation existe mot pour mot dans la page indiquée. S'il ne reste aucune
affirmation vérifiée, la réponse est « non_trouve », quoi que le modèle ait écrit.
Le texte de chaque affirmation reste une formulation du modèle : seule la citation est vérifiée.
"""

from dossiers import citations
from dossiers.enjeux import _pieces
from dossiers.llm import AppelJson
from dossiers.magasin import Magasin

CONSIGNE = """Tu réponds à la question d'un avocat en t'appuyant UNIQUEMENT sur les pièces du dossier, découpées en pages repérées [page N].
Réponds en JSON :
{"found": true | false,
 "claims": [{"text": "une affirmation, en une phrase", "document": "nom du fichier exact", "page": N,
             "quote": "passage EXACT de la page qui la soutient, recopié mot pour mot, 250 caractères maximum"}],
 "answer": "la réponse en une ou deux phrases, qui ne dit que ce que les affirmations établissent",
 "limits": "ce que le dossier ne permet pas de dire, ou vide"}
- found = false si le dossier ne contient pas l'information demandée. Alors claims est vide et answer dit que le dossier ne le dit pas.
  N'utilise jamais tes connaissances générales pour combler un manque : une information absente des pièces est absente.
- Une affirmation = un fait précis, avec sa propre citation. Pas de citation, pas d'affirmation.
- Respecte ce que dit la pièce : une intention ou une annonce (« ne sera versée ») n'est pas un fait accompli (« n'a pas été versée ») ;
  une réserve ou une condition doit figurer dans l'affirmation.
- Ne présente pas comme définitif un projet non signé ou une copie ; dis-le dans limits.
- Si les pièces se contredisent, donne les deux affirmations et dis-le dans limits."""


async def repondre(magasin: Magasin, matter_id: str, question: str, appeler: AppelJson) -> dict:
    """Renvoie {"statut": "repondu"|"partiel"|"non_trouve", "reponse", "limites", "sources", "refusees", "tronque"}."""
    texte, index = _pieces(magasin, matter_id)
    reponse = await appeler(CONSIGNE, f"QUESTION : {question}\n\nPIÈCES :\n{texte}")
    sources, refusees = [], []
    for c in reponse.get("claims") or []:
        nom, citation = str(c.get("document") or ""), str(c.get("quote") or "").strip()
        try:
            page = int(c.get("page"))
        except (TypeError, ValueError):
            page = -1
        doc, pages = index.get(nom, (None, {}))
        if doc and page in pages and citations.citation_presente(citation, pages[page]):
            sources.append({"doc_id": doc["doc_id"], "filename": nom, "page_no": page, "quote": citation,
                            "text": str(c.get("text") or "")[:300],
                            "warning": "pièce proche d'une autre (version) : peut ne pas être la version retenue" if doc["status"] == "version" else ""})
        else:
            refusees.append(f"citation introuvable ({nom} p.{c.get('page')}) : {citation[:80]!r}")
    if not sources:
        statut = "non_trouve"
        texte_reponse = "Le dossier ne le dit pas."
    else:
        statut = "partiel" if refusees else "repondu"
        # Le texte montré est fait des seules affirmations vérifiées : chaque phrase a sa citation.
        texte_reponse = " ".join(s["text"].rstrip(". ") + "." for s in sources if s["text"].strip())[:900]
    return {"statut": statut, "reponse": texte_reponse, "limites": str(reponse.get("limits") or "")[:400] if sources else "",
            "sources": sources, "refusees": refusees, "tronque": index["_tronque"]}
