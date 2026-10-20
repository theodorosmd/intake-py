"""Importer une pièce : garder l'original, lire le texte, classer, repérer doublons et versions,
puis proposer des événements pour la chronologie, chacun avec sa citation vérifiée.
"""

import difflib
import hashlib
from datetime import date

from dossiers import citations, lecture
from dossiers.llm import AppelJson
from dossiers.magasin import Magasin

TYPES = ["contrat", "avenant", "bon_livraison", "facture", "courriel", "courrier", "rapport", "autre"]

CONSIGNE_CLASSEMENT = f"""Tu classes une pièce d'un dossier contentieux. Réponds en JSON :
{{"type": un de {TYPES}, "date": "AAAA-MM-JJ" ou null (date du document lui-même), "parties": [noms des personnes ou sociétés citées comme parties], "titre": titre court}}
N'invente rien : si la date ou les parties n'apparaissent pas, mets null ou []."""

CONSIGNE_EVENEMENTS = """Tu relèves les événements datés d'une pièce, pour une chronologie de dossier.
Le texte est découpé en pages repérées [page N]. Réponds en JSON :
{"events": [{"date": "AAAA-MM-JJ" ou null, "title": "ce qui s'est passé, en une phrase", "page": N, "quote": "passage EXACT de la page, recopié mot pour mot, 200 caractères maximum"}]}
Chaque événement doit être appuyé par une citation recopiée telle quelle. Si tu n'es pas certain, n'écris pas l'événement.
La date d'un événement doit être écrite dans la page. Ne la déduis pas d'un jour de la semaine (« samedi ») ni d'une formule (« fin mars ») :
mets alors la date du document si elle est écrite, sinon null."""

SEUIL_DOUBLON = 0.97  # texte quasi identique
SEUIL_VERSION = 0.6   # même pièce, modifiée
# Seuls ces types ont des versions successives (projet, signé, avenant). Deux bulletins de paie, deux factures
# ou deux bons de livraison se ressemblent par leur modèle sans être une même pièce modifiée.
TYPES_AVEC_VERSIONS = {"contrat", "avenant", "courrier", "rapport"}
LONGUEUR_COMPARAISON = 6000


def _texte(pages: list[dict]) -> str:
    return "\n".join(p["content"] for p in pages if p["readable"])


def _similarite(a: str, b: str) -> float:
    a, b = citations.normaliser(a)[:LONGUEUR_COMPARAISON], citations.normaliser(b)[:LONGUEUR_COMPARAISON]
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() if a and b else 0.0


def _date_valide(valeur) -> str | None:
    try:
        return date.fromisoformat(str(valeur)).isoformat() if valeur else None
    except ValueError:
        return None


async def classer_et_ranger(magasin: Magasin, matter_id: str, nom: str, octets: bytes, appeler: AppelJson) -> dict:
    """Étape 1 : garde l'original, lit les pages, classe, repère doublons et versions, range la pièce.

    Renvoie le bilan de la pièce, sans événements (ils viennent à l'étape 2, `relever_evenements`).
    """
    empreinte = hashlib.sha256(octets).hexdigest()
    existants = magasin.documents(matter_id)
    flags: list[str] = []
    try:
        pages = lecture.lire(nom, octets)
    except Exception as erreur:  # noqa: BLE001 — un fichier abîmé ne doit pas arrêter l'import du dossier
        pages = []
        flags.append(f"fichier illisible ({type(erreur).__name__})")

    doc = {"filename": nom, "sha256": empreinte, "doc_type": "autre", "status": "ok", "flags": flags}
    lisibles = [p for p in pages if p["readable"]]

    identique = next((d for d in existants if d["sha256"] == empreinte), None)
    if identique:
        doc.update(status="doublon", related_to=identique["doc_id"], doc_type=identique["doc_type"])
        flags.append(f"fichier identique à {identique['filename']}")
    elif not lisibles:
        doc["status"] = "illisible"
        flags.append("aucun texte lisible : scan ou image, à relire à la main")
    else:
        classe = await appeler(CONSIGNE_CLASSEMENT, _texte(pages)[:6000])
        type_doc = classe.get("type") if classe.get("type") in TYPES else "autre"
        doc.update(doc_type=type_doc, doc_date=_date_valide(classe.get("date")),
                   parties=[str(p) for p in classe.get("parties") or []][:10], title=str(classe.get("titre") or "")[:200])
        if len(lisibles) < len(pages):
            doc["status"] = "incomplet"
            flags.append("pages illisibles : " + ", ".join(str(p["page_no"]) for p in pages if not p["readable"]))
        texte = _texte(pages)
        proche = max(
            ((d, _similarite(texte, _texte(magasin.pages(d["doc_id"])))) for d in existants if d["status"] != "illisible"),
            key=lambda x: x[1], default=(None, 0.0),
        )
        if proche[0] and proche[1] >= SEUIL_DOUBLON:
            doc.update(status="doublon", related_to=proche[0]["doc_id"])
            flags.append(f"texte identique à {proche[0]['filename']} ({proche[1]:.0%})")
        elif proche[0] and proche[1] >= SEUIL_VERSION and proche[0]["doc_type"] == type_doc and type_doc in TYPES_AVEC_VERSIONS:
            doc.update(status="version", related_to=proche[0]["doc_id"])
            flags.append(f"version proche de {proche[0]['filename']} ({proche[1]:.0%}) : à comparer")

    doc_id = magasin.ajouter_document(matter_id, doc, pages, octets)
    return {"doc_id": doc_id, "filename": nom, "status": doc["status"], "doc_type": doc["doc_type"], "flags": flags,
            "evenements": 0, "refuses": []}


async def relever_evenements(magasin: Magasin, matter_id: str, doc_id: str, appeler: AppelJson) -> dict:
    """Étape 2 : propose les événements d'une pièce déjà rangée. Tout vient du magasin, rien n'est passé en mémoire.

    Une pièce doublon, illisible ou sans page lisible n'a pas d'événements : on ne sollicite pas le modèle.
    """
    documents = magasin.documents(matter_id)
    doc = next(d for d in documents if d["doc_id"] == doc_id)
    pages = magasin.pages(doc_id)
    if doc["status"] not in {"ok", "incomplet", "version"} or not any(p["readable"] for p in pages):
        return {"evenements": 0, "refuses": []}
    # Une pièce proche d'une autre peut être un projet : ses dates ne sont pas à prendre pour acquises.
    avertissement = ""
    if doc["status"] == "version":
        proche = next(d for d in documents if d["doc_id"] == doc["related_to"])
        avertissement = f"pièce proche de {proche['filename']} (version) : peut ne pas être la version retenue"
    return await _evenements(magasin, matter_id, doc_id, pages, appeler, avertissement)


async def importer(magasin: Magasin, matter_id: str, nom: str, octets: bytes, appeler: AppelJson) -> dict:
    """Importe une pièce et renvoie son bilan (statut, alertes, événements retenus ou refusés)."""
    bilan = await classer_et_ranger(magasin, matter_id, nom, octets, appeler)
    bilan.update(await relever_evenements(magasin, matter_id, bilan["doc_id"], appeler))
    return bilan


async def _evenements(magasin: Magasin, matter_id: str, doc_id: str, pages: list[dict], appeler: AppelJson, avertissement: str = "") -> dict:
    texte = "\n\n".join(f"[page {p['page_no']}]\n{p['content']}" for p in pages if p["readable"])
    par_page = {p["page_no"]: p["content"] for p in pages}
    retenus, refuses = [], []
    for e in (await appeler(CONSIGNE_EVENEMENTS, texte)).get("events", []):
        try:
            page = int(e.get("page"))
        except (TypeError, ValueError):
            page = -1
        titre, citation = str(e.get("title") or "").strip(), str(e.get("quote") or "").strip()
        if titre and page in par_page and citations.citation_presente(citation, par_page[page]):
            jour = _date_valide(e.get("date"))
            trouvee = citations.date_dans_page(jour, par_page[page]) if jour else "complete"
            notes = [avertissement] if avertissement else []
            if jour is None:
                notes.append("date absente : à renseigner")
            elif trouvee is None:
                notes.append("date non retrouvée dans la page : à vérifier")
            elif trouvee == "sans_annee":
                notes.append("année non écrite dans la page (déduite)")
            note = " ; ".join(notes)
            retenus.append({"event_date": jour, "title": titre, "page_no": page, "quote": citation, "note": note})
        else:
            refuses.append(f"citation introuvable page {e.get('page')} : {titre[:80]!r}")
    magasin.enregistrer_evenements(matter_id, doc_id, retenus)
    return {"evenements": len(retenus), "refuses": refuses}
