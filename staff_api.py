"""API du tableau de bord du cabinet : avocats et secrétariat suivent les dossiers pris au téléphone.

L'application web (dépôt ai-labs, dossier intake-desk) l'appelle depuis son serveur, jamais depuis
le navigateur : le jeton reste côté serveur. Chaque requête porte :

- `Authorization: Bearer <STAFF_API_TOKEN>` ; sans jeton configuré, tout est refusé (503) ;
- `X-Staff-User` et `X-Staff-Role` (attorney | staff) pour les écritures : l'application web a
  authentifié la personne, la passerelle applique les droits du rôle et l'inscrit au journal.

Règles métier :
- seul un avocat accepte (`retained`) ou refuse (`declined`) un dossier ;
- chaque changement d'étape, d'avocat ou de rendez-vous laisse une ligne dans le journal du dossier ;
- les rendez-vous restent ceux de l'agent vocal (même table, même règle « un numéro, un créneau »).
"""

import hmac
import os
from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from stockage import Stockage

Statut = Literal["new", "contacted", "consult_booked", "retained", "declined"]
Role = Literal["attorney", "staff"]
STATUTS_AVOCAT = {"retained", "declined"}
NEW_YORK = ZoneInfo("America/New_York")
LIBELLES = {
    "new": "New",
    "contacted": "Contacted",
    "consult_booked": "Consultation booked",
    "retained": "Retained",
    "declined": "Declined",
}


def cle_tel(phone: str) -> str:
    """Numéro réduit à ses chiffres, sans l'indicatif américain : « +1 (555) 123-4567 » et
    « 555 123 4567 » désignent la même personne (l'agent note le numéro tel qu'il est dicté)."""
    chiffres = "".join(c for c in phone if c.isdigit())
    return chiffres[1:] if len(chiffres) == 11 and chiffres.startswith("1") else chiffres


def a_voix_haute(creneau: datetime) -> str:
    """« Monday, November 3 at 2 PM », heure du cabinet (même tournure que main.a_voix_haute)."""
    return creneau.astimezone(NEW_YORK).strftime("%A, %B %d at %I %p").replace(" 0", " ")


def verifier_jeton(authorization: Annotated[str, Header()] = "") -> None:
    attendu = os.environ.get("STAFF_API_TOKEN", "")
    if not attendu:
        raise HTTPException(status_code=503, detail="Staff API disabled")
    if not hmac.compare_digest(authorization.encode(), f"Bearer {attendu}".encode()):
        raise HTTPException(status_code=401, detail="Invalid token")


class Auteur(BaseModel):
    nom: str
    role: Role


def auteur(
    x_staff_user: Annotated[str, Header(min_length=1, max_length=120)],
    x_staff_role: Annotated[Role, Header()],
) -> Auteur:
    return Auteur(nom=x_staff_user, role=x_staff_role)


class MiseAJour(BaseModel):
    status: Statut | None = None
    # Chaîne vide = retirer l'avocat ; absent = ne pas toucher.
    assignee: str | None = Field(default=None, max_length=120)


class Note(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class DemandeRendezVous(BaseModel):
    preferred_date: date
    preferred_hour: int | None = Field(default=None, ge=0, le=23)


def creer_routeur(
    *,
    stockage: Stockage,
    est_urgent: Callable[[dict], bool],
    reserver: Callable[[str, str, date, int | None], dict[str, Any]],
) -> APIRouter:
    routeur = APIRouter(prefix="/staff", dependencies=[Depends(verifier_jeton)], tags=["staff"])

    def rendez_vous_par_tel() -> dict[str, dict]:
        return {cle_tel(r["phone_key"]): r for r in stockage.rendez_vous_detailles()}

    def resumes_par_appel() -> dict[str, dict]:
        return {r["call_id"]: r for r in stockage.resumes() if r.get("call_id")}

    def jours_depuis(accident: str | None) -> int | None:
        try:
            return (date.today() - date.fromisoformat(accident)).days if accident else None
        except ValueError:
            return None

    def presenter(ligne: dict, rdv: dict[str, dict], resumes: dict[str, dict]) -> dict:
        dossier = ligne["data"]
        consultation = rdv.get(cle_tel(dossier.get("phone", "")))
        return {
            "case_id": ligne["case_id"],
            **dossier,
            "urgent": est_urgent(dossier),
            "days_since_accident": jours_depuis(dossier.get("accident_date")),
            "status": ligne["status"],
            "assignee": ligne["assignee"],
            "created_at": ligne["created_at"],
            "updated_at": ligne["updated_at"],
            "consultation": (
                {"slot": consultation["slot"], "booked_at": consultation["booked_at"]}
                if consultation
                else None
            ),
            "has_call_summary": dossier.get("call_id") in resumes,
        }

    def ligne_du_dossier(case_id: str) -> dict:
        for ligne in stockage.dossiers_suivis():
            if ligne["case_id"] == case_id:
                return ligne
        raise HTTPException(status_code=404, detail="Unknown case")

    def detail(case_id: str) -> dict:
        resumes = resumes_par_appel()
        cas = presenter(ligne_du_dossier(case_id), rendez_vous_par_tel(), resumes)
        cas["call_summary"] = resumes.get(cas.get("call_id") or "")
        cas["notes"] = stockage.notes(case_id)
        return cas

    @routeur.get("/cases")
    def lister_dossiers() -> list[dict]:
        rdv, resumes = rendez_vous_par_tel(), resumes_par_appel()
        return [presenter(ligne, rdv, resumes) for ligne in stockage.dossiers_suivis()]

    @routeur.get("/cases/{case_id}")
    def lire_dossier(case_id: str) -> dict:
        return detail(case_id)

    @routeur.patch("/cases/{case_id}")
    def modifier_dossier(case_id: str, maj: MiseAJour, qui: Annotated[Auteur, Depends(auteur)]) -> dict:
        actuel = ligne_du_dossier(case_id)
        changements: dict[str, Any] = {}
        journal: list[str] = []
        if maj.status is not None and maj.status != actuel["status"]:
            if maj.status in STATUTS_AVOCAT and qui.role != "attorney":
                raise HTTPException(status_code=403, detail="Only an attorney can retain or decline a case")
            changements["status"] = maj.status
            journal.append(f"Status: {LIBELLES[actuel['status']]} → {LIBELLES[maj.status]}")
        if maj.assignee is not None:
            nouvel_avocat = maj.assignee.strip() or None
            if nouvel_avocat != actuel["assignee"]:
                changements["assignee"] = nouvel_avocat
                journal.append(f"Assigned to {nouvel_avocat}" if nouvel_avocat else "Unassigned")
        if changements:
            stockage.mettre_a_jour_suivi(case_id, changements)
            for ligne in journal:
                stockage.ajouter_note(case_id, "event", qui.nom, qui.role, ligne)
        return detail(case_id)

    @routeur.post("/cases/{case_id}/notes", status_code=201)
    def noter(case_id: str, note: Note, qui: Annotated[Auteur, Depends(auteur)]) -> dict:
        ligne_du_dossier(case_id)
        return stockage.ajouter_note(case_id, "note", qui.nom, qui.role, note.body.strip())

    @routeur.post("/cases/{case_id}/consultation")
    def reserver_pour_le_dossier(
        case_id: str, demande: DemandeRendezVous, qui: Annotated[Auteur, Depends(auteur)]
    ) -> dict:
        ligne = ligne_du_dossier(case_id)
        dossier = ligne["data"]
        try:
            resultat = reserver(
                dossier["caller_name"], dossier["phone"], demande.preferred_date, demande.preferred_hour
            )
        except ValueError as erreur:
            raise HTTPException(status_code=409, detail=str(erreur)) from erreur
        quand = resultat["slot_spoken"]
        texte = (
            f"Consultation moved to {quand} (was {resultat['previous_slot_spoken']})"
            if resultat.get("previous_slot_spoken")
            else f"Consultation booked for {quand}"
        )
        stockage.ajouter_note(case_id, "event", qui.nom, qui.role, texte)
        if ligne["status"] in ("new", "contacted"):
            stockage.mettre_a_jour_suivi(case_id, {"status": "consult_booked"})
        return {"booking": resultat, "case": detail(case_id)}

    @routeur.delete("/cases/{case_id}/consultation")
    def annuler_rendez_vous(case_id: str, qui: Annotated[Auteur, Depends(auteur)]) -> dict:
        ligne = ligne_du_dossier(case_id)
        rdv = rendez_vous_par_tel().get(cle_tel(ligne["data"].get("phone", "")))
        ancien = stockage.annuler(rdv["phone_key"]) if rdv else None
        if ancien is None:
            raise HTTPException(status_code=404, detail="No consultation for this case")
        stockage.ajouter_note(
            case_id, "event", qui.nom, qui.role, f"Consultation of {a_voix_haute(ancien)} cancelled"
        )
        if ligne["status"] == "consult_booked":
            stockage.mettre_a_jour_suivi(case_id, {"status": "contacted"})
        return detail(case_id)

    @routeur.get("/consultations")
    def lister_rendez_vous() -> list[dict]:
        par_tel = {cle_tel(l["data"].get("phone", "")): l["case_id"] for l in stockage.dossiers_suivis()}
        return [
            {
                "slot": r["slot"],
                "caller_name": r["caller_name"],
                "booked_at": r["booked_at"],
                "case_id": par_tel.get(cle_tel(r["phone_key"])),
            }
            for r in stockage.rendez_vous_detailles()
        ]

    @routeur.get("/calls")
    def lister_appels() -> list[dict]:
        par_appel = {
            l["data"]["call_id"]: l["case_id"]
            for l in stockage.dossiers_suivis()
            if l["data"].get("call_id")
        }
        return [{**r, "case_id": par_appel.get(r.get("call_id"))} for r in stockage.resumes()]

    @routeur.get("/overview")
    def vue_d_ensemble() -> dict:
        lignes = stockage.dossiers_suivis()
        maintenant = datetime.now().astimezone()
        par_statut = {s: 0 for s in LIBELLES}
        for l in lignes:
            par_statut[l["status"]] = par_statut.get(l["status"], 0) + 1
        ouverts = [l for l in lignes if l["status"] in ("new", "contacted")]
        return {
            "total_cases": len(lignes),
            "by_status": par_statut,
            "urgent_open": sum(1 for l in ouverts if est_urgent(l["data"])),
            "unassigned_open": sum(1 for l in ouverts if not l["assignee"]),
            "upcoming_consultations": sum(
                1 for r in stockage.rendez_vous_detailles() if r["slot"] >= maintenant
            ),
            "cases_by_day": _par_jour(lignes),
        }

    return routeur


def _par_jour(lignes: list[dict]) -> dict[str, int]:
    """Dossiers créés par jour, dans le fuseau du cabinet."""
    compte: dict[str, int] = {}
    for l in lignes:
        jour = l["created_at"].astimezone(NEW_YORK).date().isoformat()
        compte[jour] = compte.get(jour, 0) + 1
    return dict(sorted(compte.items()))
