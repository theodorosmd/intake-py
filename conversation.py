"""Une conversation téléphonique : tours de parole, interruptions et latence."""

import asyncio
import base64
import logging
import re
import statistics
import time

import agent_vocal

logger = logging.getLogger("uvicorn.error")

MOTS_POUR_INTERROMPRE = 2  # « No, wait » suffit ; un « uh » isolé ne coupe pas
DELAI_GRACE = (
    1.0  # pendant la 1re seconde d'un son, on ne coupe pas : c'est surtout de l'écho
)
SEUIL_ECHO = 0.75  # part des mots de l'appelant déjà présents dans ce que dit l'agent


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
        # sons envoyés dont Telnyx n'a pas encore confirmé la fin
        self.marques: set[str] = set()
        self.mesures: list[dict] = []
        self.interruptions = 0
        self.debut_son = 0.0  # heure où le son en cours a démarré
        self.texte_joue = (
            ""  # ce que l'agent est en train de dire (pour repérer l'écho)
        )

    async def saluer(self) -> None:
        try:
            audio = await agent_vocal.synthese_vocale(agent_vocal.SALUTATION)
            await self.envoyer_voix(audio, "salutation", agent_vocal.SALUTATION)
        except Exception:
            logger.exception("Salutation impossible")

    async def envoyer_voix(self, audio: bytes, nom: str, texte: str = "") -> None:
        """Envoie le mp3, puis une marque : Telnyx la renvoie quand le son est fini."""
        if not self.marques:  # rien ne jouait : un nouveau son démarre
            self.debut_son = time.monotonic()
            self.texte_joue = ""
        self.texte_joue = f"{self.texte_joue} {texte}".strip()
        payload = base64.b64encode(audio).decode()
        await self.ws.send_json({"event": "media", "media": {"payload": payload}})
        await self.ws.send_json({"event": "mark", "mark": {"name": nom}})
        self.marques.add(nom)

    @property
    def parle(self) -> bool:
        """True tant qu'un son envoyé n'est pas fini chez l'appelant."""
        return bool(self.marques)

    def fin_de_lecture(self, nom: str | None = None) -> None:
        """Telnyx a renvoyé une marque : ce son-là est fini (sans nom : tous)."""
        if nom is None:
            self.marques.clear()
        else:
            self.marques.discard(nom)

    def occupe(self) -> bool:
        return self.parle or (self.tache is not None and not self.tache.done())

    def est_echo(self, texte: str) -> bool:
        """Les mots entendus sont-ils (presque) ceux que l'agent est en train de dire ?"""
        mots = re.findall(r"\w+", texte.lower())
        agent = set(re.findall(r"\w+", self.texte_joue.lower()))
        if not mots:
            return False
        return sum(m in agent for m in mots) / len(mots) >= SEUIL_ECHO

    async def appelant_parle(self, texte: str) -> None:
        """Appelé à chaque texte, même provisoire : l'appelant a-t-il coupé l'agent ?"""
        if len(texte.split()) < MOTS_POUR_INTERROMPRE or not self.occupe():
            return
        if self.parle:  # l'agent parle : on se méfie de l'écho de son propre son
            if time.monotonic() - self.debut_son < DELAI_GRACE:
                logger.info("Interruption ignoree (debut du son) : %s", texte)
                return
            if self.est_echo(texte):
                logger.info("Interruption ignoree (echo de l'agent) : %s", texte)
                return
        await self.interrompre()

    async def interrompre(self) -> None:
        self.interruptions += 1
        if self.tache and not self.tache.done():
            self.tache.cancel()  # la réponse en préparation est abandonnée
        if self.parle:
            await self.ws.send_json({"event": "clear"})  # coupe le son tout de suite
            self.marques.clear()
        logger.info("INTERRUPTION : l'agent se tait et ecoute")

    def nouveau_tour(self, phrase: str, fin_parole: float) -> None:
        """L'appelant a fini une phrase : on (re)lance la préparation de la réponse."""
        self.non_repondu.append(phrase)
        if self.tache and not self.tache.done():
            self.tache.cancel()  # répondre à la dernière version, pas à l'ancienne
        self.tache = asyncio.create_task(self.repondre(fin_parole))

    async def repondre(self, fin_parole: float) -> None:
        """Le LLM répond en flux : chaque phrase part en voix dès qu'elle est prête."""
        consommees = len(self.non_repondu)
        texte = " ".join(self.non_repondu)
        dites: list[str] = []
        faits: list[str] = []  # outils exécutés pendant ce tour
        debut_llm = time.perf_counter()
        premier = {}
        try:
            async for phrase in agent_vocal.phrases_llm(
                [*self.historique, {"role": "user", "content": texte}], faits
            ):
                if "llm" not in premier:
                    premier["llm"] = time.perf_counter()
                audio = await agent_vocal.synthese_vocale(phrase)
                if not dites:
                    # Point de non-retour : le premier son part, on inscrit le tour.
                    premier["voix"] = time.perf_counter()
                    del self.non_repondu[:consommees]
                    self.historique.append({"role": "user", "content": texte})
                    self.historique.append({"role": "assistant", "content": ""})
                dites.append(phrase)
                self.historique[-1]["content"] = " ".join(dites)
                logger.info("AGENT : %s", phrase)
                await self.envoyer_voix(
                    audio, f"tour-{len(self.mesures) + 1}-{len(dites)}", phrase
                )
                if len(dites) == 1:
                    self.enregistrer_mesure(fin_parole, debut_llm, premier)
        except asyncio.CancelledError:
            logger.info("Reponse abandonnee (l'appelant a repris la parole)")
            raise
        except Exception:
            logger.exception("Le tour a echoue, l'agent continue")
        finally:
            self.noter_faits(texte, consommees, dites, faits)

    def noter_faits(
        self, texte: str, consommees: int, dites: list[str], faits: list[str]
    ) -> None:
        """Un outil a agi (dossier enregistré...) : la suite de l'appel doit le savoir,
        même si la réponse a été coupée avant la moindre parole."""
        if not faits:
            return
        if not dites:  # le tour n'est pas encore dans l'historique : on l'y met
            del self.non_repondu[:consommees]
            self.historique.append({"role": "user", "content": texte})
        self.historique += [{"role": "system", "content": f} for f in faits]

    def enregistrer_mesure(
        self, fin_parole: float, debut_llm: float, premier: dict
    ) -> None:
        """Latence jusqu'au premier son : c'est elle que l'appelant ressent."""
        mesure = {
            "attente": (debut_llm - fin_parole) * 1000,
            "llm": (premier["llm"] - debut_llm) * 1000,
            "voix": (premier["voix"] - premier["llm"]) * 1000,
            "total": (time.perf_counter() - fin_parole) * 1000,
        }
        self.mesures.append(mesure)
        logger.info(
            "Tour %d : premier son apres %d ms (attente fin de phrase %d, 1re phrase LLM %d, voix %d)",
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
