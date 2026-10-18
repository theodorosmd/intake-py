"""Serveur MCP du cabinet : dossiers, résumés d'appels et rendez-vous, lisibles depuis un assistant IA.

Le cabinet branche Claude, Cursor ou tout client MCP sur https://<hôte>/mcp avec un jeton
(en-tête Authorization: Bearer <MCP_TOKEN>) et demande par exemple « quels dossiers urgents
sont arrivés aujourd'hui ? » ou « réserve un rendez-vous jeudi pour M. X ».

Les données viennent du même stockage que la gateway (Postgres en production, voir stockage.py) :
main.py le lui passe à la création. Sans jeton configuré, le point d'accès refuse tout (503).
"""

import hmac
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from stockage import Stockage

LECTURE = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


class Creneau(BaseModel):
    slot: str
    slot_spoken: str


class Reservation(Creneau):
    status: str
    previous_slot_spoken: str | None = None


class RendezVous(BaseModel):
    slot: str
    caller_name: str


def creer_serveur(
    *,
    stockage: Stockage,
    est_urgent: Callable[[dict], bool],
    creneau_libre: Callable[[date, int | None], dict[str, Any]],
    reserver: Callable[[str, str, date, int | None], dict[str, Any]],
    rechercher: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
) -> MCPServer:
    serveur = MCPServer(
        name="pi-intake",
        instructions=(
            "Tools for a US personal-injury law firm's intake desk: cases captured by the phone agent, "
            "post-call summaries and consultation bookings. Times are America/New_York."
        ),
    )

    @serveur.tool(annotations=LECTURE)
    def list_intake_cases(urgent_only: bool = False) -> list[dict]:
        """List the intake cases saved by the phone agent, most recent first.

        A case is urgent when the caller was injured and the accident is recent.
        """
        cas = [
            {"case_id": case_id, **dossier, "urgent": est_urgent(dossier)}
            for case_id, dossier in stockage.dossiers()
        ]
        return [c for c in cas if c["urgent"]] if urgent_only else cas

    @serveur.tool(annotations=LECTURE)
    def get_intake_case(case_id: str) -> dict[str, Any]:
        """Return one intake case by its id."""
        dossier = stockage.dossier(case_id)
        if dossier is None:
            raise ValueError(f"No intake case with id {case_id}")
        return {"case_id": case_id, **dossier, "urgent": est_urgent(dossier)}

    @serveur.tool(annotations=LECTURE)
    def list_call_summaries() -> list[dict]:
        """List the post-call summaries (injured, accident type), most recent first."""
        return stockage.resumes()

    @serveur.tool(annotations=LECTURE)
    def list_consultations() -> list[RendezVous]:
        """List booked consultations in chronological order."""
        return [
            RendezVous(slot=creneau.isoformat(), caller_name=nom)
            for creneau, nom in stockage.rendez_vous()
        ]

    @serveur.tool(annotations=LECTURE)
    def find_next_slot(preferred_date: date, preferred_hour: int | None = None) -> Creneau:
        """Find the first free consultation slot on or after a date, without booking it."""
        return Creneau(**creneau_libre(preferred_date, preferred_hour))

    @serveur.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
    def book_consultation(
        caller_name: str, phone: str, preferred_date: date, preferred_hour: int | None = None
    ) -> Reservation:
        """Book the first free consultation slot on or after a date.

        A caller (same phone number) who already has a slot is moved to the new one.
        """
        return Reservation(**reserver(caller_name, phone, preferred_date, preferred_hour))

    if rechercher is not None:

        @serveur.tool(annotations=LECTURE)
        async def search_firm_knowledge(question: str) -> dict[str, Any]:
            """Search the firm's own information (fees, hours, languages, cases handled,
            how consultations work). Results come with their source section."""
            return await rechercher(question)

    return serveur


class PointAccesMCP:
    """Le point d'accès HTTP /mcp, protégé par jeton (`Authorization: Bearer <MCP_TOKEN>`).

    Sans état (stateless) et en réponses JSON : chaque requête se suffit à elle-même, ce qui
    passe sans souci derrière Traefik et survit à un redémarrage du pod. La protection contre le
    DNS rebinding du SDK vise les serveurs locaux sans authentification ; ici le jeton la remplace.
    Le SDK ne laisse démarrer un gestionnaire de sessions qu'une fois : on en recrée un à chaque
    démarrage de l'application (utile pour les tests, qui la démarrent plusieurs fois).
    """

    def __init__(self, serveur: MCPServer) -> None:
        self.serveur = serveur
        self.app: ASGIApp | None = None

    @asynccontextmanager
    async def demarrer(self) -> AsyncIterator[None]:
        self.app = self.serveur.streamable_http_app(
            streamable_http_path="/mcp",
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        try:
            async with self.serveur.session_manager.run():
                yield
        finally:
            self.app = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        attendu = os.environ.get("MCP_TOKEN", "")
        recu = dict(scope["headers"]).get(b"authorization", b"")
        if not attendu or self.app is None:
            await JSONResponse({"detail": "MCP désactivé"}, 503)(scope, receive, send)
            return
        if not hmac.compare_digest(recu, f"Bearer {attendu}".encode()):
            await JSONResponse({"detail": "Jeton invalide"}, 401)(scope, receive, send)
            return
        await self.app(scope, receive, send)
