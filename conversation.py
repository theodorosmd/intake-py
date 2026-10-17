"""Une conversation téléphonique : tours de parole, interruptions et latence."""

import asyncio
import base64
import logging
import statistics
import time

import agent_vocal

logger = logging.getLogger("uvicorn.error")

MOTS_POUR_INTERROMPRE = 2  # « No, wait » suffit ; un « uh » isolé ne coupe pas


class Conversation:
    """Suit l'état d'un appel : ce qui a été dit, ce que l'agent fait, les temps.

    - `tache` : le tour en cours de préparation (LLM puis voix).
    - `parle` : True tant que la voix de l'agent est jouée chez l'appelant.
    - `non_repondu` : phrases de l'appelant pas encore traitées. Si l'agent est
      interrompu, elles restent là et partent avec la phrase suivante.
    """

    def __init__(self, ws):
        self.ws = ws
        self.historique = [{"role": "assistant", "content": agent_vocal.SALUTATION}]
        self.non_repondu: list[str] = []
        self.tache: asyncio.Task | None = None
        self.parle = False
        self.mesures: list[dict] = []
        self.interruptions = 0

    async def saluer(self) -> None:
        try:
            audio = await agent_vocal.synthese_vocale(agent_vocal.SALUTATION)
            await self.envoyer_voix(audio, "salutation")
        except Exception:
            logger.exception("Salutation impossible")

    async def envoyer_voix(self, audio: bytes, nom: str) -> None:
        """Envoie le mp3, puis une marque : Telnyx la renvoie quand le son est fini."""
        payload = base64.b64encode(audio).decode()
        await self.ws.send_json({"event": "media", "media": {"payload": payload}})
        await self.ws.send_json({"event": "mark", "mark": {"name": nom}})
        self.parle = True

    def fin_de_lecture(self) -> None:
        """Telnyx a renvoyé la marque : l'appelant a entendu toute la réponse."""
        self.parle = False

    def occupe(self) -> bool:
        return self.parle or (self.tache is not None and not self.tache.done())

    async def appelant_parle(self, texte: str) -> None:
        """Appelé à chaque texte, même provisoire : l'appelant a-t-il coupé l'agent ?"""
        if len(texte.split()) >= MOTS_POUR_INTERROMPRE and self.occupe():
            await self.interrompre()

    async def interrompre(self) -> None:
        self.interruptions += 1
        if self.tache and not self.tache.done():
            self.tache.cancel()  # la réponse en préparation est abandonnée
        if self.parle:
            await self.ws.send_json({"event": "clear"})  # coupe le son tout de suite
            self.parle = False
        logger.info("INTERRUPTION : l'agent se tait et ecoute")

    def nouveau_tour(self, phrase: str, fin_parole: float) -> None:
        """L'appelant a fini une phrase : on (re)lance la préparation de la réponse."""
        self.non_repondu.append(phrase)
        if self.tache and not self.tache.done():
            self.tache.cancel()  # répondre à la dernière version, pas à l'ancienne
        self.tache = asyncio.create_task(self.repondre(fin_parole))

    async def repondre(self, fin_parole: float) -> None:
        texte = " ".join(self.non_repondu)
        try:
            debut_llm = time.perf_counter()
            reponse = await agent_vocal.reponse_llm(
                [*self.historique, {"role": "user", "content": texte}]
            )
            debut_voix = time.perf_counter()
            audio = await agent_vocal.synthese_vocale(reponse)
            fin = time.perf_counter()
        except asyncio.CancelledError:
            logger.info("Reponse abandonnee (l'appelant a repris la parole)")
            raise
        except Exception:
            logger.exception("Le tour a echoue, l'agent continue")
            return
        # Point de non-retour : la réponse part, on l'inscrit dans l'historique.
        self.non_repondu.clear()
        self.historique += [
            {"role": "user", "content": texte},
            {"role": "assistant", "content": reponse},
        ]
        logger.info("AGENT : %s", reponse)
        await self.envoyer_voix(audio, f"tour-{len(self.mesures) + 1}")
        mesure = {
            "attente": (debut_llm - fin_parole) * 1000,
            "llm": (debut_voix - debut_llm) * 1000,
            "voix": (fin - debut_voix) * 1000,
            "total": (time.perf_counter() - fin_parole) * 1000,
        }
        self.mesures.append(mesure)
        logger.info(
            "Tour %d : %d ms au total (attente fin de phrase %d, LLM %d, voix %d)",
            len(self.mesures),
            mesure["total"],
            mesure["attente"],
            mesure["llm"],
            mesure["voix"],
        )

    def bilan(self) -> dict | None:
        """Médianes de latence sur l'appel, pour comparer avec l'AI Assistant."""
        if not self.mesures:
            return None
        return {
            "tours": len(self.mesures),
            "interruptions": self.interruptions,
            **{
                cle: round(statistics.median(m[cle] for m in self.mesures))
                for cle in ("attente", "llm", "voix", "total")
            },
        }

    async def terminer(self) -> None:
        if self.tache and not self.tache.done():
            self.tache.cancel()
        bilan = self.bilan()
        if bilan:
            logger.info("BILAN LATENCE (medianes, ms) : %s", bilan)
