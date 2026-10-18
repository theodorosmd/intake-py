"""Masque les données personnelles (noms, numéros, e-mails) dans tous les journaux.

Le masquage se fait à la création de chaque ligne de journal (setLogRecordFactory) :
aucune ligne n'y échappe, quel que soit le module qui l'écrit, y compris les lignes
ajoutées plus tard. Pour déboguer en local, LOGS_EN_CLAIR=1 désactive le masquage.

Les noms sont appris pendant l'appel : quand l'appelant se présente (« I'm Tilda »)
ou quand un outil reçoit caller_name, le nom est retenu et masqué partout ensuite.
"""

import logging
import os
import re

NUMERO = re.compile(r"(?<![\w/-])\+?\d[\d\s().-]{6,}\d(?![\w-])")
CHIFFRES_DITS = re.compile(
    r"\b(?:(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
    r"|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty"
    r"|forty|fifty|sixty|seventy|eighty|ninety|hundred|double|triple)[\s,-]+){4,}"
    r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
    r"|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty"
    r"|forty|fifty|sixty|seventy|eighty|ninety)\b",
    re.IGNORECASE,
)
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
EPELE = re.compile(
    r"\b(?:[A-Za-z][-.]\s?){2,}[A-Za-z]\b|\b(?:[A-Za-z] ){2,}[A-Za-z]\b"
)
REMERCIEMENT = re.compile(r"\b(?i:thank you),\s+([A-Z][\w'’-]+)")
PRESENTATION = re.compile(
    r"\b(?i:my name is|my name's|name is|i am|i'm|this is|call me)\s+"
    r"([A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+)?)"
)
CHAMP_NOM = re.compile(r'"caller_name":\s*"([^"]+)"')
PAS_UN_NOM = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "sorry", "fine", "okay", "not",
    "here", "calling", "the", "a", "an", "an ai", "ai", "new", "york", "still",
}
NOMS_CONNUS: set[str] = set()
MAX_NOMS = 500


def retenir_nom(nom: str | None) -> None:
    """Retient un nom (et chacune de ses parties) pour le masquer dans la suite."""
    if not nom:
        return
    parties = [p.strip(" .,'’") for p in nom.split()]
    if all(p.lower() in PAS_UN_NOM for p in parties):
        return  # « This is New York », « I'm Sorry »
    if len(NOMS_CONNUS) > MAX_NOMS:
        NOMS_CONNUS.clear()
    for partie in [nom, *parties]:
        partie = partie.strip(" .,'’")
        if len(partie) >= 3 and partie.lower() not in PAS_UN_NOM:
            NOMS_CONNUS.add(partie)


def _numero(trouve: re.Match) -> str:
    texte = trouve.group(0)
    if sum(c.isdigit() for c in texte) < 10 and not texte.startswith("+"):
        return texte  # durées, octets, dates : pas des numéros de téléphone
    return "[TEL]"


def masquer(texte: str) -> str:
    """Remplace noms, numéros, e-mails et noms épelés par des marqueurs."""
    for trouve in PRESENTATION.finditer(texte):
        retenir_nom(trouve.group(1))
    for trouve in CHAMP_NOM.finditer(texte):
        retenir_nom(trouve.group(1))
    for trouve in REMERCIEMENT.finditer(texte):
        retenir_nom(trouve.group(1))  # « Thank you, Theo. » : l'agent répète le nom
    for trouve in EPELE.finditer(texte):
        retenir_nom(re.sub(r"[^A-Za-z]", "", trouve.group(0)))  # nom épelé lettre par lettre
    texte = EMAIL.sub("[EMAIL]", texte)
    texte = NUMERO.sub(_numero, texte)
    texte = CHIFFRES_DITS.sub("[TEL]", texte)
    texte = EPELE.sub("[EPELE]", texte)
    for nom in sorted(NOMS_CONNUS, key=len, reverse=True):
        texte = re.sub(rf"\b{re.escape(nom)}\b", "[NOM]", texte, flags=re.IGNORECASE)
    return texte


def installer() -> None:
    """Branche le masquage sur la création de toutes les lignes de journal."""
    if os.getenv("LOGS_EN_CLAIR") == "1":
        return
    fabrique = logging.getLogRecordFactory()
    if getattr(fabrique, "masque", False):
        return  # déjà installé (rechargement de fastapi dev)

    def fabrique_masquee(*args, **kwargs):
        record = fabrique(*args, **kwargs)
        try:
            if record.name == "uvicorn.access":
                # Le format d'accès d'uvicorn dépouille record.args (5 valeurs) : on les garde
                # et on masque chaque valeur texte, sans toucher à la structure.
                record.args = tuple(masquer(a) if isinstance(a, str) else a for a in record.args)
            else:
                record.msg = masquer(record.getMessage())
                record.args = ()
        except Exception:  # noqa: BLE001, S110 — un journal ne doit jamais faire planter l'appel
            pass
        return record

    fabrique_masquee.masque = True
    logging.setLogRecordFactory(fabrique_masquee)
