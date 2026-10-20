"""Un dossier entièrement fictif pour essayer et tester le paquet.

Affaire : la société Atelier Marin (acheteur) réclame à Nordik Équipements (vendeur) une
indemnité pour une machine livrée en retard. Personnes, sociétés, montants et dates sont
inventés. Le dossier contient exprès des pièges : une copie, un projet de contrat proche du
contrat final, un courriel qui semble modifier la date de livraison, un scan sans texte.
"""

import io
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

PIEGES = {
    "bon-livraison-copie.pdf": "doublon",
    "contrat-vente-projet.pdf": "version",
    "scan-illisible.pdf": "illisible",
}


def _pdf(pages: list[list[str]], *, dessin_seul: bool = False) -> bytes:
    tampon = io.BytesIO()
    c = canvas.Canvas(tampon, pagesize=A4, invariant=1)
    for lignes in pages:
        if dessin_seul:  # une page « scannée » : des formes, aucun texte
            c.rect(60, 120, 470, 600)
            c.line(80, 600, 500, 580)
        else:
            c.setFont("Helvetica", 11)
            y = 790
            for ligne in lignes:
                c.drawString(60, y, ligne)
                y -= 18
        c.showPage()
    c.save()
    return tampon.getvalue()


CONTRAT_FINAL = [
    ["CONTRAT DE VENTE DE MATERIEL", "",
     "Entre Nordik Équipements SAS (le Vendeur) et Atelier Marin SARL (l'Acheteur).",
     "Signé le 2 février 2026.", "",
     "Article 1. Objet. Le Vendeur vend à l'Acheteur une machine de découpe numérique",
     "modèle ND-400 pour un prix de 41 800 euros hors taxes."],
    ["Article 2. Livraison. Le Vendeur livre la machine au plus tard le 15 mars 2026",
     "dans les locaux de l'Acheteur.", "",
     "Article 3. Retard. Tout retard de livraison donne lieu à une pénalité de 0,5 % du prix",
     "par jour de retard, dans la limite de 10 % du prix.", "",
     "Article 4. Modification. Toute modification du présent contrat doit faire l'objet d'un",
     "avenant écrit signé par les deux parties."],
]
CONTRAT_PROJET = [
    ["PROJET DE CONTRAT DE VENTE DE MATERIEL", "",
     "Entre Nordik Équipements SAS (le Vendeur) et Atelier Marin SARL (l'Acheteur).",
     "Projet du 20 janvier 2026, non signé.", "",
     "Article 1. Objet. Le Vendeur vend à l'Acheteur une machine de découpe numérique",
     "modèle ND-400 pour un prix de 41 800 euros hors taxes."],
    ["Article 2. Livraison. Le Vendeur livre la machine au plus tard le 1er avril 2026",
     "dans les locaux de l'Acheteur.", "",
     "Article 3. Retard. Tout retard de livraison donne lieu à une pénalité de 0,2 % du prix",
     "par jour de retard, dans la limite de 5 % du prix.", "",
     "Article 4. Modification. Toute modification du présent contrat doit faire l'objet d'un",
     "avenant écrit signé par les deux parties."],
]
BON_LIVRAISON = [[
    "BON DE LIVRAISON n° BL-2026-0318", "",
    "Expéditeur : Nordik Équipements SAS. Destinataire : Atelier Marin SARL.",
    "Machine de découpe numérique ND-400, 1 unité.",
    "Reçu le 28 mars 2026 par M. Paul Durand, responsable d'atelier.",
    "Remarque à la réception : emballage endommagé, contrôle de la machine à faire.",
]]
FACTURE = [[
    "FACTURE n° F-2026-0211", "",
    "Nordik Équipements SAS à Atelier Marin SARL, émise le 28 mars 2026.",
    "Machine de découpe numérique ND-400 : 41 800,00 euros hors taxes.",
    "Paiement à 30 jours.",
]]
ANNULATION = [[
    "Lettre d'annulation de commande", "",
    "Maison Lucas SA à Atelier Marin SARL, le 2 avril 2026.",
    "Nous annulons la commande n° 7781 de 120 pièces découpées. Les causes sont",
    "le retard de livraison constaté à plusieurs reprises et des défauts de qualité sur",
    "les échantillons reçus en mars.",
]]

EMAIL_10_MARS = b"""From: Claire Vidal <c.vidal@nordik.example>
To: Julien Marin <j.marin@atelier-marin.example>
Date: Tue, 10 Mar 2026 09:12:00 +0100
Subject: Livraison de la ND-400
Content-Type: text/plain; charset=utf-8

Bonjour Julien,

Notre transporteur a pris du retard, nous pourrons livrer la machine vers le 31 mars.
Pouvez-vous nous confirmer que cela vous convient ?

Cordialement,
Claire Vidal, Nordik Equipements
"""
EMAIL_11_MARS = """From: Julien Marin <j.marin@atelier-marin.example>
To: Claire Vidal <c.vidal@nordik.example>
Date: Wed, 11 Mar 2026 14:30:00 +0100
Subject: Re: Livraison de la ND-400
Content-Type: text/plain; charset=utf-8

Bonjour Claire,

D'accord pour fin mars, sous réserve de confirmation écrite par votre direction.
Nous avons des commandes clients qui dépendent de cette machine.

Julien Marin, gérant d'Atelier Marin
""".encode()
EMAIL_RECLAMATION = """From: Julien Marin <j.marin@atelier-marin.example>
To: Claire Vidal <c.vidal@nordik.example>
Date: Sun, 05 Apr 2026 10:00:00 +0200
Subject: Réclamation - retard de livraison
Content-Type: text/plain; charset=utf-8

Madame Vidal,

La machine a été livrée le 28 mars alors que le contrat prévoyait le 15 mars. Nous vous
demandons de payer les pénalités de retard prévues à l'article 3 du contrat.

Julien Marin
""".encode()


def generer() -> dict[str, bytes]:
    bon = _pdf([BON_LIVRAISON[0]])
    return {
        "contrat-vente.pdf": _pdf(CONTRAT_FINAL),
        "contrat-vente-projet.pdf": _pdf(CONTRAT_PROJET),
        "bon-livraison.pdf": bon,
        "bon-livraison-copie.pdf": _pdf([["COPIE", *BON_LIVRAISON[0]]]),
        "facture.pdf": _pdf(FACTURE),
        "annulation-commande-client.pdf": _pdf(ANNULATION),
        "scan-illisible.pdf": _pdf([[""]], dessin_seul=True),
        "email-2026-03-10.eml": EMAIL_10_MARS,
        "email-2026-03-11.eml": EMAIL_11_MARS,
        "email-2026-04-05-reclamation.eml": EMAIL_RECLAMATION,
    }


if __name__ == "__main__":
    sortie = Path(__file__).parents[1] / "tests" / "fixtures" / "atelier-marin"
    sortie.mkdir(parents=True, exist_ok=True)
    for nom, octets in generer().items():
        (sortie / nom).write_bytes(octets)
    print(f"{len(generer())} pièces écrites dans {sortie}")
