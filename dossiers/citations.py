"""Vérification des citations : une citation proposée par un modèle doit exister dans la page."""

import re
import unicodedata


def normaliser(texte: str) -> str:
    """Compare sans tenir compte des espaces, de la casse, des apostrophes ni des guillemets."""
    texte = unicodedata.normalize("NFKC", texte).replace("’", "'").replace("«", '"').replace("»", '"')
    texte = re.sub(r"-\s*\n\s*", "", texte)  # mot coupé en fin de ligne
    return re.sub(r"\s+", " ", texte).strip(' ."\'').lower()


def citation_presente(citation: str | None, page: str) -> bool:
    return bool(citation) and len(normaliser(citation)) >= 8 and normaliser(citation) in normaliser(page)


MOIS = [
    ("janvier", "january", "jan"), ("février", "february", "feb"), ("mars", "march", "mar"),
    ("avril", "april", "apr"), ("mai", "may"), ("juin", "june", "jun"), ("juillet", "july", "jul"),
    ("août", "august", "aug"), ("septembre", "september", "sep"), ("octobre", "october", "oct"),
    ("novembre", "november", "nov"), ("décembre", "december", "dec"),
]


def date_dans_page(date_iso: str, page: str) -> str | None:
    """La date proposée (AAAA-MM-JJ) s'écrit-elle quelque part dans la page ?

    Reconnaît « 15 mars 2026 », « 1er avril 2026 », « 10 Mar 2026 » et « 15/03/2026 ». Une date
    reconstituée par le modèle (« fin mars » devenu le 31) n'est pas retrouvée : elle est signalée.
    Renvoie "complete" (année écrite), "sans_annee" (« le 28 mars » : l'année est déduite) ou None.
    """
    annee, mois, jour = (int(x) for x in date_iso.split("-"))
    texte = normaliser(page)
    if re.search(rf"\b0?{jour}[/.-]0?{mois}[/.-]{annee}\b", texte):
        return "complete"
    noms = "|".join(MOIS[mois - 1])
    # « 15 mars », mais aussi les plages « du 2 au 8 mars » et « du 8 au 12 avril » (chaque borne est une date écrite)
    autre = r"\d{1,2}(?:er)?"
    lien = r"(?:au|à|-)"
    jour_mois = (rf"\b(?:0?{jour}(?:er)?\s+(?:{noms})|0?{jour}(?:er)?\s+{lien}\s+{autre}\s+(?:{noms})"
                 rf"|{autre}\s+{lien}\s+0?{jour}(?:er)?\s+(?:{noms}))\b\.?")
    if re.search(rf"{jour_mois},?\s+{annee}\b", texte):
        return "complete"
    return "sans_annee" if re.search(jour_mois, texte) else None
