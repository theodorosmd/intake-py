"""Questions litigieuses : pour chacune, ce qui l'appuie, ce qui la contredit, ce qui manque.

La question est posée de façon neutre (« La livraison a-t-elle été tardive ? ») : une pièce
« appuie » si elle va dans le sens du oui, « contredit » si elle va dans le sens du non. Ainsi
la liste montre aussi les pièces qui gênent le client, que l'avocat doit connaître.
Comme pour la chronologie, une pièce n'est retenue que si sa citation existe dans la page.
"""

from dossiers import citations
from dossiers.llm import AppelJson
from dossiers.magasin import Magasin

CONSIGNE = """Tu aides un avocat à préparer un dossier. On te donne une question litigieuse et les pièces du dossier,
découpées en pages repérées [page N]. Réponds en JSON :
{"evidence": [{"document": "nom du fichier exact", "page": N,
"quote": "passage EXACT de la page, recopié mot pour mot, 250 caractères maximum",
"why": "en une phrase : ce que dit la pièce et comment elle se rapporte à la question, y compris face aux autres pièces (un contrat qui impose une forme, un courriel informel...)",
"stance": "supports" | "contradicts" | "context"}],
"missing": ["ce qui manque au dossier pour trancher la question, en une phrase chacun"]}
- supports : la pièce va dans le sens du « oui » à la question telle qu'elle est posée ; contradicts : dans le sens du « non » ;
  context : utile pour comprendre, sans trancher.
- Écris « why » avant « stance » : la position doit découler de l'explication. Quand une pièce ne tranche pas seule (condition, réserve,
  exigence de forme), dis-le dans « why ».
- Une même pièce peut aller dans les deux sens (une lettre qui invoque deux causes, par exemple) : fais alors DEUX entrées,
  chacune avec sa propre citation (le passage qui appuie, le passage qui contredit).
- Pour une question de cause (« a-t-il causé... ? »), une pièce « supports » doit parler de la cause elle-même ; une pièce qui
  établit seulement un fait voisin (le retard, la date) est « context ».
- Un projet ou une version non signée ne tranche rien : « context », en le disant dans « why ».
- Cherche aussi ce qui contredit : une pièce défavorable à l'une des parties doit apparaître.
- N'invente aucune citation. Si une pièce est un projet ou une copie, ne la présente pas comme définitive.
- « missing » : documents, preuves ou faits que le dossier ne contient pas et qui permettraient de trancher."""

LIMITE_CARACTERES = 80_000  # au-delà, il faudra sélectionner les pages (recherche), pas tout envoyer


def _pieces(magasin: Magasin, matter_id: str) -> tuple[str, dict]:
    blocs, index, total = [], {}, 0
    for d in magasin.documents(matter_id):
        if d["status"] in {"doublon", "illisible"}:
            continue
        mention = " (version proche d'une autre pièce : peut être un projet)" if d["status"] == "version" else ""
        pages = [p for p in magasin.pages(d["doc_id"]) if p["readable"]]
        texte = f"=== {d['filename']} [{d['doc_type']}]{mention} ===\n" + "\n".join(f"[page {p['page_no']}]\n{p['content']}" for p in pages)
        total += len(texte)
        blocs.append(texte)
        index[d["filename"]] = (d, {p["page_no"]: p["content"] for p in pages})
    return "\n\n".join(blocs)[:LIMITE_CARACTERES], index | {"_tronque": total > LIMITE_CARACTERES}


async def analyser(magasin: Magasin, matter_id: str, issue_id: str, appeler: AppelJson) -> dict:
    """Analyse une question et enregistre les pièces vérifiées. Renvoie le bilan (retenues, refusées)."""
    question = next(e["question"] for e in magasin.enjeux(matter_id) if e["issue_id"] == issue_id)
    texte, index = _pieces(magasin, matter_id)
    reponse = await appeler(CONSIGNE, f"QUESTION : {question}\n\nPIÈCES :\n{texte}")
    retenues, refusees = [], []
    for e in reponse.get("evidence", []):
        nom, citation = str(e.get("document") or ""), str(e.get("quote") or "").strip()
        try:
            page = int(e.get("page"))
        except (TypeError, ValueError):
            page = -1
        stance = e.get("stance") if e.get("stance") in {"supports", "contradicts", "context"} else "context"
        doc, pages = index.get(nom, (None, {}))
        if doc and page in pages and citations.citation_presente(citation, pages[page]):
            avertissement = "pièce proche d'une autre (version) : peut ne pas être la version retenue" if doc["status"] == "version" else ""
            retenues.append({"doc_id": doc["doc_id"], "page_no": page, "stance": stance, "quote": citation,
                             "why": str(e.get("why") or "")[:300], "warning": avertissement})
        else:
            refusees.append(f"citation introuvable ({nom} p.{e.get('page')}) : {citation[:80]!r}")
    manques = [str(m)[:300] for m in reponse.get("missing", []) if str(m).strip()]
    magasin.enregistrer_analyse(issue_id, retenues, manques)
    return {"retenues": len(retenues), "refusees": refusees, "manques": len(manques), "tronque": index["_tronque"]}
