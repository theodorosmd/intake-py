"""Lire une pièce : PDF, courriel (.eml) ou texte. Renvoie le texte page par page.

Une page est « lisible » si elle contient assez de texte pour qu'on puisse s'y fier. Un scan
sans texte reste illisible ici : on le signale au lieu d'inventer son contenu (la lecture
des scans par reconnaissance de caractères viendra comme une étape à part).
"""

import io
from email import policy
from email.parser import BytesParser

SEUIL_PAGE_LISIBLE = 40  # caractères


def _page(numero: int, texte: str) -> dict:
    texte = texte.strip()
    return {"page_no": numero, "content": texte, "readable": len(texte) >= SEUIL_PAGE_LISIBLE}


def lire_pdf(octets: bytes) -> list[dict]:
    from pypdf import PdfReader

    lecteur = PdfReader(io.BytesIO(octets))
    return [_page(i, page.extract_text() or "") for i, page in enumerate(lecteur.pages, 1)]


def lire_eml(octets: bytes) -> list[dict]:
    message = BytesParser(policy=policy.default).parsebytes(octets)
    corps = message.get_body(preferencelist=("plain", "html"))
    entetes = "\n".join(f"{k}: {message[k]}" for k in ("From", "To", "Date", "Subject") if message[k])
    return [_page(1, f"{entetes}\n\n{corps.get_content() if corps else ''}")]


def lire(nom: str, octets: bytes) -> list[dict]:
    nom = nom.lower()
    if nom.endswith(".pdf"):
        return lire_pdf(octets)
    if nom.endswith(".eml"):
        return lire_eml(octets)
    if nom.endswith((".txt", ".md")):
        return [_page(1, octets.decode("utf-8", errors="replace"))]
    raise ValueError(f"format non pris en charge : {nom}")
