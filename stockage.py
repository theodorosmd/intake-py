"""Où vivent les dossiers, les rendez-vous et les résumés d'appels.

Deux implémentations du même contrat :
- `Memoire` : des dictionnaires, pour les tests et le rejeu (rien ne survit au processus) ;
- `Postgres` : une vraie base, utilisée dès que DATABASE_URL est défini (en production).

Le module expose une instance partagée, `STOCKAGE`, choisie au démarrage. La gateway, la fin
d'appel et le serveur MCP passent tous par elle.
"""

import json
import math
import os
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Protocol

SCHEMA = Path(__file__).with_name("schema.sql")


class CreneauPris(Exception):
    """Le créneau a été pris entre le calcul et l'écriture (deux réservations simultanées)."""


class Stockage(Protocol):
    def preparer(self) -> None: ...
    def fermer(self) -> None: ...
    def vider(self) -> None: ...
    def enregistrer_dossier(self, case_id: str, dossier: dict) -> None: ...
    def dossiers(self) -> list[tuple[str, dict]]: ...
    def dossier(self, case_id: str) -> dict | None: ...
    def creneaux_pris(self) -> set[datetime]: ...
    def reserver(self, cle_tel: str, nom: str, creneau: datetime) -> datetime | None: ...
    def rendez_vous(self) -> list[tuple[datetime, str]]: ...
    def enregistrer_resume(self, call_id: str, resume: dict) -> None: ...
    def resumes(self) -> list[dict]: ...
    # Base de connaissances (RAG). `vider` n'y touche pas : ce n'est pas une donnée d'appel.
    def empreintes_connaissances(self) -> dict[str, str]: ...
    def enregistrer_connaissances(self, morceaux_vecteurs: list) -> None: ...
    def supprimer_connaissances(self, ids: Iterable[str]) -> None: ...
    def chercher_connaissances(self, vecteur: list[float], nb: int) -> list[dict]: ...


class Memoire:
    """Tout en mémoire. `reserver` renvoie l'ancien créneau de ce numéro, s'il y en avait un."""

    def __init__(self) -> None:
        self._dossiers: dict[str, dict] = {}
        self._reservations: dict[datetime, str] = {}
        self._par_tel: dict[str, datetime] = {}
        self._resumes: dict[str, dict] = {}
        self._connaissances: dict[str, tuple] = {}  # id -> (morceau, vecteur)

    def preparer(self) -> None:
        pass

    def fermer(self) -> None:
        pass

    def vider(self) -> None:
        for table in (self._dossiers, self._reservations, self._par_tel, self._resumes):
            table.clear()

    def enregistrer_dossier(self, case_id: str, dossier: dict) -> None:
        self._dossiers[case_id] = dossier

    def dossiers(self) -> list[tuple[str, dict]]:
        return list(reversed(self._dossiers.items()))  # le plus récent d'abord

    def dossier(self, case_id: str) -> dict | None:
        return self._dossiers.get(case_id)

    def creneaux_pris(self) -> set[datetime]:
        return set(self._reservations)

    def reserver(self, cle_tel: str, nom: str, creneau: datetime) -> datetime | None:
        if creneau in self._reservations:
            raise CreneauPris(creneau.isoformat())
        ancien = self._par_tel.pop(cle_tel, None)
        if ancien is not None:
            self._reservations.pop(ancien, None)
        self._reservations[creneau] = nom
        self._par_tel[cle_tel] = creneau
        return ancien

    def rendez_vous(self) -> list[tuple[datetime, str]]:
        return sorted(self._reservations.items())

    def enregistrer_resume(self, call_id: str, resume: dict) -> None:
        self._resumes[call_id] = resume

    def resumes(self) -> list[dict]:
        return list(reversed(self._resumes.values()))

    def empreintes_connaissances(self) -> dict[str, str]:
        return {i: m.empreinte for i, (m, _) in self._connaissances.items()}

    def enregistrer_connaissances(self, morceaux_vecteurs: list) -> None:
        for morceau, vecteur in morceaux_vecteurs:
            self._connaissances[morceau.id] = (morceau, vecteur)

    def supprimer_connaissances(self, ids: Iterable[str]) -> None:
        for i in ids:
            self._connaissances.pop(i, None)

    def chercher_connaissances(self, vecteur: list[float], nb: int) -> list[dict]:
        scores = [
            {"id": m.id, "title": m.titre, "text": m.texte, "score": _cosinus(vecteur, v)}
            for m, v in self._connaissances.values()
        ]
        return sorted(scores, key=lambda r: r["score"], reverse=True)[:nb]


def _cosinus(a: list[float], b: list[float]) -> float:
    norme = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / norme if norme else 0.0


def _litteral_vecteur(vecteur: list[float]) -> str:
    """Le format texte de pgvector, '[0.1,0.2,…]' : pas besoin d'un adaptateur Python."""
    return "[" + ",".join(f"{x:.7g}" for x in vecteur) + "]"


class Postgres:
    """Une base Postgres. Le créneau est la clé primaire des rendez-vous : c'est la base, et
    non le code, qui empêche deux réservations au même moment, même avec plusieurs copies
    de la gateway. Les dates reviennent dans le fuseau du cabinet."""

    def __init__(self, url: str, fuseau: str = "America/New_York") -> None:
        self._url = url
        self._fuseau = fuseau
        self._pool = self._nouveau_pool()

    def _nouveau_pool(self):
        from psycopg_pool import ConnectionPool

        def configurer(connexion) -> None:
            connexion.execute(f"SET TIME ZONE '{self._fuseau}'")
            connexion.commit()

        return ConnectionPool(self._url, min_size=1, max_size=5, open=False, configure=configurer)

    def _connexion(self):
        if self._pool.closed:  # un pool fermé ne se rouvre pas : on en crée un neuf
            self._pool = self._nouveau_pool()
            self._pool.open(wait=True, timeout=30)
        return self._pool.connection()

    def preparer(self) -> None:
        with self._connexion() as connexion:
            connexion.execute(SCHEMA.read_text())

    def fermer(self) -> None:
        self._pool.close()

    def vider(self) -> None:
        with self._connexion() as connexion:
            connexion.execute("TRUNCATE intake_cases, consultations, call_summaries")

    def enregistrer_dossier(self, case_id: str, dossier: dict) -> None:
        with self._connexion() as connexion:
            connexion.execute(
                "INSERT INTO intake_cases (case_id, data) VALUES (%s, %s)", (case_id, json.dumps(dossier))
            )

    def dossiers(self) -> list[tuple[str, dict]]:
        with self._connexion() as connexion:
            lignes = connexion.execute(
                "SELECT case_id, data FROM intake_cases ORDER BY created_at DESC, case_id"
            ).fetchall()
        return [(case_id, data) for case_id, data in lignes]

    def dossier(self, case_id: str) -> dict | None:
        with self._connexion() as connexion:
            ligne = connexion.execute(
                "SELECT data FROM intake_cases WHERE case_id = %s", (case_id,)
            ).fetchone()
        return ligne[0] if ligne else None

    def creneaux_pris(self) -> set[datetime]:
        with self._connexion() as connexion:
            return {slot for (slot,) in connexion.execute("SELECT slot FROM consultations")}

    def reserver(self, cle_tel: str, nom: str, creneau: datetime) -> datetime | None:
        import psycopg

        with self._connexion() as connexion:
            try:
                with connexion.transaction():
                    ancien = connexion.execute(
                        "DELETE FROM consultations WHERE phone_key = %s RETURNING slot", (cle_tel,)
                    ).fetchone()
                    connexion.execute(
                        "INSERT INTO consultations (slot, caller_name, phone_key) VALUES (%s, %s, %s)",
                        (creneau, nom, cle_tel),
                    )
            except psycopg.errors.UniqueViolation as erreur:
                raise CreneauPris(creneau.isoformat()) from erreur
        return ancien[0] if ancien else None

    def rendez_vous(self) -> list[tuple[datetime, str]]:
        with self._connexion() as connexion:
            return [
                (slot, nom)
                for slot, nom in connexion.execute(
                    "SELECT slot, caller_name FROM consultations ORDER BY slot"
                )
            ]

    def enregistrer_resume(self, call_id: str, resume: dict) -> None:
        with self._connexion() as connexion:
            connexion.execute(
                """INSERT INTO call_summaries (call_id, data) VALUES (%s, %s)
                   ON CONFLICT (call_id) DO UPDATE SET data = EXCLUDED.data, received_at = now()""",
                (call_id, json.dumps(resume)),
            )

    def resumes(self) -> list[dict]:
        with self._connexion() as connexion:
            return [
                data
                for (data,) in connexion.execute(
                    "SELECT data FROM call_summaries ORDER BY received_at DESC"
                )
            ]


    def empreintes_connaissances(self) -> dict[str, str]:
        with self._connexion() as connexion:
            return dict(connexion.execute("SELECT id, content_hash FROM knowledge_chunks").fetchall())

    def enregistrer_connaissances(self, morceaux_vecteurs: list) -> None:
        with self._connexion() as connexion, connexion.transaction():
            for m, vecteur in morceaux_vecteurs:
                connexion.execute(
                    """INSERT INTO knowledge_chunks (id, source, title, content, content_hash, embedding)
                       VALUES (%s, %s, %s, %s, %s, %s::vector)
                       ON CONFLICT (id) DO UPDATE SET source = EXCLUDED.source, title = EXCLUDED.title,
                         content = EXCLUDED.content, content_hash = EXCLUDED.content_hash,
                         embedding = EXCLUDED.embedding, indexed_at = clock_timestamp()""",
                    (m.id, m.source, m.titre, m.texte, m.empreinte, _litteral_vecteur(vecteur)),
                )

    def supprimer_connaissances(self, ids: Iterable[str]) -> None:
        with self._connexion() as connexion:
            connexion.execute("DELETE FROM knowledge_chunks WHERE id = ANY(%s)", (list(ids),))

    def chercher_connaissances(self, vecteur: list[float], nb: int) -> list[dict]:
        # <=> : distance cosinus de pgvector (0 = identique) ; l'index HNSW accélère ce tri.
        with self._connexion() as connexion:
            lignes = connexion.execute(
                """SELECT id, title, content, 1 - (embedding <=> %s::vector) AS score
                   FROM knowledge_chunks ORDER BY embedding <=> %s::vector LIMIT %s""",
                (_litteral_vecteur(vecteur), _litteral_vecteur(vecteur), nb),
            ).fetchall()
        return [{"id": i, "title": t, "text": c, "score": float(sc)} for i, t, c, sc in lignes]

def ouvrir(url: str | None) -> Stockage:
    return Postgres(url) if url else Memoire()


STOCKAGE: Stockage = ouvrir(os.environ.get("DATABASE_URL"))
