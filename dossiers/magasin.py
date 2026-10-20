"""Où vivent les affaires, leurs pièces et leur chronologie.

Même idée que stockage.py : deux implémentations du même contrat, `MagasinMemoire` pour les
tests et `MagasinPostgres` (qui réutilise le pool de la gateway) quand DATABASE_URL est défini.
"""

import json
from datetime import date
from typing import Protocol

CHAMPS_CORRIGEABLES = {"event_date", "title", "status", "note"}
STATUTS_EVENEMENT = {"proposed", "confirmed", "disputed", "rejected"}


class Magasin(Protocol):
    def vider(self) -> None: ...
    def creer_affaire(self, matter_id: str, titre: str) -> None: ...
    def affaires(self) -> list[dict]: ...
    def ajouter_document(self, matter_id: str, doc: dict, pages: list[dict], original: bytes) -> str: ...
    def documents(self, matter_id: str) -> list[dict]: ...
    def pages(self, doc_id: str) -> list[dict]: ...
    def original(self, doc_id: str) -> bytes | None: ...
    def enregistrer_evenements(self, matter_id: str, doc_id: str, evenements: list[dict]) -> None: ...
    def evenements(self, matter_id: str) -> list[dict]: ...
    def corriger_evenement(self, event_id: str, champs: dict) -> None: ...
    def ajouter_enjeu(self, matter_id: str, question: str) -> str: ...
    def enregistrer_analyse(self, issue_id: str, preuves: list[dict], manques: list[str]) -> None: ...
    def enjeux(self, matter_id: str) -> list[dict]: ...


def _verifier_correction(champs: dict) -> dict:
    inconnus = set(champs) - CHAMPS_CORRIGEABLES
    if inconnus:
        raise ValueError(f"champs non modifiables : {sorted(inconnus)}")
    if "status" in champs and champs["status"] not in STATUTS_EVENEMENT:
        raise ValueError(f"statut inconnu : {champs['status']!r}")
    if champs.get("event_date"):
        champs = {**champs, "event_date": date.fromisoformat(str(champs["event_date"]))}
    return champs


def _tri_evenement(e: dict):
    return (e["event_date"] is None, e["event_date"] or date.max, e["event_id"])


class MagasinMemoire:
    def __init__(self) -> None:
        self.vider()

    def vider(self) -> None:
        self._affaires: dict[str, str] = {}
        self._documents: dict[str, dict] = {}
        self._pages: dict[str, list[dict]] = {}
        self._originaux: dict[str, bytes] = {}
        self._evenements: dict[str, dict] = {}
        self._enjeux: dict[str, dict] = {}

    def creer_affaire(self, matter_id: str, titre: str) -> None:
        self._affaires[matter_id] = titre

    def affaires(self) -> list[dict]:
        return [{"matter_id": i, "title": t} for i, t in self._affaires.items()]

    def ajouter_document(self, matter_id: str, doc: dict, pages: list[dict], original: bytes) -> str:
        doc_id = f"{matter_id}-{sum(d['matter_id'] == matter_id for d in self._documents.values()) + 1:03d}"
        self._documents[doc_id] = {**doc, "doc_id": doc_id, "matter_id": matter_id}
        self._pages[doc_id] = [dict(p) for p in pages]
        self._originaux[doc_id] = original
        return doc_id

    def documents(self, matter_id: str) -> list[dict]:
        return [dict(d) for d in self._documents.values() if d["matter_id"] == matter_id]

    def pages(self, doc_id: str) -> list[dict]:
        return [dict(p) for p in self._pages.get(doc_id, [])]

    def original(self, doc_id: str) -> bytes | None:
        return self._originaux.get(doc_id)

    def enregistrer_evenements(self, matter_id: str, doc_id: str, evenements: list[dict]) -> None:
        for e in evenements:
            event_id = f"{matter_id}-e{len(self._evenements) + 1:03d}"
            self._evenements[event_id] = {
                "status": "proposed", "note": "", **e, "event_id": event_id,
                "matter_id": matter_id, "doc_id": doc_id,
                "event_date": date.fromisoformat(e["event_date"]) if e.get("event_date") else None,
            }

    def evenements(self, matter_id: str) -> list[dict]:
        return sorted((dict(e) for e in self._evenements.values() if e["matter_id"] == matter_id), key=_tri_evenement)

    def corriger_evenement(self, event_id: str, champs: dict) -> None:
        if event_id not in self._evenements:
            raise KeyError(event_id)
        self._evenements[event_id].update(_verifier_correction(dict(champs)))

    def ajouter_enjeu(self, matter_id: str, question: str) -> str:
        issue_id = f"{matter_id}-q{sum(i['matter_id'] == matter_id for i in self._enjeux.values()) + 1:02d}"
        self._enjeux[issue_id] = {"issue_id": issue_id, "matter_id": matter_id, "question": question, "manques": [], "preuves": []}
        return issue_id

    def enregistrer_analyse(self, issue_id: str, preuves: list[dict], manques: list[str]) -> None:
        self._enjeux[issue_id].update(preuves=[dict(p) for p in preuves], manques=list(manques))

    def enjeux(self, matter_id: str) -> list[dict]:
        return [{**e, "preuves": [dict(p) for p in e["preuves"]]} for e in self._enjeux.values() if e["matter_id"] == matter_id]


class MagasinPostgres:
    def __init__(self, stockage) -> None:
        self._s = stockage  # une instance stockage.Postgres : on réutilise son pool

    def vider(self) -> None:
        with self._s._connexion() as c:
            c.execute("TRUNCATE matters CASCADE")

    def creer_affaire(self, matter_id: str, titre: str) -> None:
        with self._s._connexion() as c:
            c.execute(
                "INSERT INTO matters (matter_id, title) VALUES (%s, %s) "
                "ON CONFLICT (matter_id) DO UPDATE SET title = EXCLUDED.title",
                (matter_id, titre),
            )

    def affaires(self) -> list[dict]:
        with self._s._connexion() as c:
            return [{"matter_id": i, "title": t} for i, t in c.execute("SELECT matter_id, title FROM matters ORDER BY created_at")]

    def ajouter_document(self, matter_id: str, doc: dict, pages: list[dict], original: bytes) -> str:
        with self._s._connexion() as c:
            (n,) = c.execute("SELECT count(*) FROM documents WHERE matter_id = %s", (matter_id,)).fetchone()
            doc_id = f"{matter_id}-{n + 1:03d}"
            c.execute(
                "INSERT INTO documents (doc_id, matter_id, filename, sha256, doc_type, doc_date, parties, title, "
                "status, related_to, flags, original) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (doc_id, matter_id, doc["filename"], doc["sha256"], doc["doc_type"], doc.get("doc_date"),
                 json.dumps(doc.get("parties", [])), doc.get("title", ""), doc["status"], doc.get("related_to"),
                 json.dumps(doc.get("flags", [])), original),
            )
            for p in pages:
                c.execute(
                    "INSERT INTO pages (doc_id, page_no, content, readable) VALUES (%s,%s,%s,%s)",
                    (doc_id, p["page_no"], p["content"], p["readable"]),
                )
        return doc_id

    def documents(self, matter_id: str) -> list[dict]:
        with self._s._connexion() as c:
            lignes = c.execute(
                "SELECT doc_id, matter_id, filename, sha256, doc_type, doc_date, parties, title, status, related_to, flags "
                "FROM documents WHERE matter_id = %s ORDER BY doc_id", (matter_id,),
            ).fetchall()
        cles = ("doc_id", "matter_id", "filename", "sha256", "doc_type", "doc_date", "parties", "title", "status", "related_to", "flags")
        return [dict(zip(cles, ligne)) for ligne in lignes]

    def pages(self, doc_id: str) -> list[dict]:
        with self._s._connexion() as c:
            lignes = c.execute("SELECT page_no, content, readable FROM pages WHERE doc_id = %s ORDER BY page_no", (doc_id,)).fetchall()
        return [{"page_no": n, "content": t, "readable": r} for n, t, r in lignes]

    def original(self, doc_id: str) -> bytes | None:
        with self._s._connexion() as c:
            ligne = c.execute("SELECT original FROM documents WHERE doc_id = %s", (doc_id,)).fetchone()
        return bytes(ligne[0]) if ligne else None

    def enregistrer_evenements(self, matter_id: str, doc_id: str, evenements: list[dict]) -> None:
        with self._s._connexion() as c:
            for e in evenements:
                (n,) = c.execute("SELECT count(*) FROM events WHERE matter_id = %s", (matter_id,)).fetchone()
                c.execute(
                    "INSERT INTO events (event_id, matter_id, doc_id, page_no, event_date, title, quote, note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (f"{matter_id}-e{n + 1:03d}", matter_id, doc_id, e["page_no"], e.get("event_date") or None, e["title"], e["quote"], e.get("note", "")),
                )

    def evenements(self, matter_id: str) -> list[dict]:
        with self._s._connexion() as c:
            lignes = c.execute(
                "SELECT event_id, matter_id, doc_id, page_no, event_date, title, quote, status, note FROM events "
                "WHERE matter_id = %s ORDER BY event_date NULLS LAST, event_id", (matter_id,),
            ).fetchall()
        cles = ("event_id", "matter_id", "doc_id", "page_no", "event_date", "title", "quote", "status", "note")
        return [dict(zip(cles, ligne)) for ligne in lignes]

    def corriger_evenement(self, event_id: str, champs: dict) -> None:
        champs = _verifier_correction(dict(champs))
        if not champs:
            return
        colonnes = ", ".join(f"{k} = %s" for k in champs)
        with self._s._connexion() as c:
            n = c.execute(f"UPDATE events SET {colonnes} WHERE event_id = %s", (*champs.values(), event_id)).rowcount
        if not n:
            raise KeyError(event_id)

    def ajouter_enjeu(self, matter_id: str, question: str) -> str:
        with self._s._connexion() as c:
            (n,) = c.execute("SELECT count(*) FROM issues WHERE matter_id = %s", (matter_id,)).fetchone()
            issue_id = f"{matter_id}-q{n + 1:02d}"
            c.execute("INSERT INTO issues (issue_id, matter_id, question) VALUES (%s,%s,%s)", (issue_id, matter_id, question))
        return issue_id

    def enregistrer_analyse(self, issue_id: str, preuves: list[dict], manques: list[str]) -> None:
        with self._s._connexion() as c:
            c.execute("DELETE FROM evidence WHERE issue_id = %s", (issue_id,))
            for p in preuves:
                c.execute(
                    "INSERT INTO evidence (issue_id, doc_id, page_no, stance, quote, why, warning) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (issue_id, p["doc_id"], p["page_no"], p["stance"], p["quote"], p.get("why", ""), p.get("warning", "")),
                )
            c.execute("UPDATE issues SET gaps = %s, analysed_at = clock_timestamp() WHERE issue_id = %s", (json.dumps(manques), issue_id))

    def enjeux(self, matter_id: str) -> list[dict]:
        with self._s._connexion() as c:
            enjeux = c.execute("SELECT issue_id, matter_id, question, gaps FROM issues WHERE matter_id = %s ORDER BY issue_id", (matter_id,)).fetchall()
            resultat = []
            for issue_id, m, question, manques in enjeux:
                lignes = c.execute(
                    "SELECT doc_id, page_no, stance, quote, why, warning FROM evidence WHERE issue_id = %s ORDER BY evidence_id", (issue_id,),
                ).fetchall()
                preuves = [dict(zip(("doc_id", "page_no", "stance", "quote", "why", "warning"), ligne)) for ligne in lignes]
                resultat.append({"issue_id": issue_id, "matter_id": m, "question": question, "manques": manques, "preuves": preuves})
        return resultat


def ouvrir_magasin(stockage) -> Magasin:
    from stockage import Postgres

    return MagasinPostgres(stockage) if isinstance(stockage, Postgres) else MagasinMemoire()
