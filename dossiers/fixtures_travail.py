"""Deuxième dossier fictif, d'un autre domaine, pour tester sans réglage préalable.

Affaire : Sophie Armand, serveuse, conteste son licenciement pour faute grave (abandon de poste)
et réclame des heures supplémentaires impayées à Brasserie Le Mistral SAS. Personnes, société,
montants et dates sont inventés. Les attendus (en bas) ont été écrits AVANT de lancer le vrai
modèle sur ces pièces : ce dossier sert à vérifier que les consignes ne sont pas réglées sur le
premier.
"""

from pathlib import Path

from dossiers.fixtures import _pdf

PIEGES = {
    "contrat-travail-projet.pdf": "version",
    "lettre-licenciement-copie.pdf": "doublon",
    "scan-illisible.pdf": "illisible",
}

CONTRAT = [[
    "CONTRAT DE TRAVAIL A DUREE INDETERMINEE", "",
    "Entre Brasserie Le Mistral SAS (l'Employeur) et Mme Sophie Armand (la Salariée).",
    "Signé le 1er septembre 2025.", "",
    "Article 1. Fonction. La Salariée est engagée comme serveuse en salle.",
    "Article 2. Durée. La durée du travail est de 35 heures par semaine.",
    "Article 3. Rémunération. Le salaire brut mensuel est de 2 150 euros.",
    "Article 4. Heures supplémentaires. Les heures supplémentaires sont effectuées à la demande",
    "de l'Employeur et sont majorées de 25 %.",
]]
CONTRAT_PROJET = [[
    "PROJET DE CONTRAT DE TRAVAIL A DUREE INDETERMINEE", "",
    "Entre Brasserie Le Mistral SAS (l'Employeur) et Mme Sophie Armand (la Salariée).",
    "Projet du 20 août 2025, non signé.", "",
    "Article 1. Fonction. La Salariée est engagée comme serveuse en salle.",
    "Article 2. Durée. La durée du travail est de 39 heures par semaine.",
    "Article 3. Rémunération. Le salaire brut mensuel est de 2 000 euros.",
    "Article 4. Heures supplémentaires. Les heures supplémentaires sont comprises dans le salaire",
    "forfaitaire convenu.",
]]


def _paie(mois: str, debut: str, fin: str, supp: str, brut: str, net: str) -> list[list[str]]:
    return [[
        "BULLETIN DE PAIE", "",
        "Employeur : Brasserie Le Mistral SAS. Salariée : Sophie Armand.",
        f"Période : du {debut} au {fin} ({mois}).",
        "Salaire de base pour 151,67 heures : 2 150,00 euros.",
        f"Heures supplémentaires payées : {supp}.",
        f"Salaire brut : {brut} euros. Net à payer : {net} euros.",
    ]]


RELEVE = [[
    "RELEVE D'HEURES - MARS 2026 (établi par la salariée)", "",
    "Semaine du 2 au 8 mars : 41 heures.",
    "Semaine du 9 au 15 mars : 43 heures.",
    "Semaine du 16 au 22 mars : 42 heures.",
    "Semaine du 23 au 29 mars : 41 heures.",
    "Total des heures au-delà de 35 heures par semaine : 22 heures.",
]]
CONVOCATION = [[
    "LETTRE DE CONVOCATION A UN ENTRETIEN PREALABLE", "",
    "Brasserie Le Mistral SAS à Mme Sophie Armand, le 14/04/2026.",
    "Nous envisageons une sanction pouvant aller jusqu'au licenciement.",
    "Vous êtes convoquée à un entretien le 20/04/2026 à 10 heures.",
]]
LICENCIEMENT = [[
    "LETTRE DE LICENCIEMENT POUR FAUTE GRAVE", "",
    "Brasserie Le Mistral SAS à Mme Sophie Armand, le 27/04/2026.",
    "Suite à l'entretien du 20/04/2026, nous vous licencions pour faute grave.",
    "Motif : abandon de poste, absence injustifiée du 8 au 12 avril 2026.",
    "Aucune indemnité de licenciement ne sera versée.",
]]

EMAIL_HEURES = """From: Marc Dufour <m.dufour@mistral.example>
To: Sophie Armand <s.armand@mail.example>
Date: Thu, 12 Mar 2026 18:05:00 +0100
Subject: Service de samedi
Content-Type: text/plain; charset=utf-8

Sophie,

On est en sous-effectif ce week-end. Merci de rester jusqu'à 23 heures samedi et dimanche.

Marc
""".encode()
EMAIL_CONGES = """From: Marc Dufour <m.dufour@mistral.example>
To: Sophie Armand <s.armand@mail.example>
Date: Thu, 02 Apr 2026 09:40:00 +0200
Subject: Re: Absence du 8 au 12 avril
Content-Type: text/plain; charset=utf-8

C'est ok pour ton absence du 8 au 12 avril, c'est validé de mon côté.

Marc Dufour, responsable de salle
""".encode()


def generer() -> dict[str, bytes]:
    lettre = _pdf(LICENCIEMENT)
    return {
        "contrat-travail.pdf": _pdf(CONTRAT),
        "contrat-travail-projet.pdf": _pdf(CONTRAT_PROJET),
        "bulletin-paie-2026-01.pdf": _pdf(_paie("janvier", "01/01/2026", "31/01/2026", "8,00 heures", "2 366,00", "1 868,00")),
        "bulletin-paie-2026-03.pdf": _pdf(_paie("mars", "01/03/2026", "31/03/2026", "0,00 heure", "2 150,00", "1 696,00")),
        "releve-heures-mars.pdf": _pdf(RELEVE),
        "convocation-entretien.pdf": _pdf(CONVOCATION),
        "lettre-licenciement.pdf": lettre,
        "lettre-licenciement-copie.pdf": _pdf([["COPIE", *LICENCIEMENT[0]]]),
        "scan-illisible.pdf": _pdf([[""]], dessin_seul=True),
        "email-2026-03-12-service.eml": EMAIL_HEURES,
        "email-2026-04-02-absence.eml": EMAIL_CONGES,
    }


TITRE = "Armand c. Brasserie Le Mistral"

# ---- Attendus, écrits avant le premier essai avec le vrai modèle ----
TYPES_ATTENDUS = {
    "contrat-travail.pdf": "contrat", "contrat-travail-projet.pdf": "contrat",
    "bulletin-paie-2026-01.pdf": "autre", "bulletin-paie-2026-03.pdf": "autre", "releve-heures-mars.pdf": "autre",
    "convocation-entretien.pdf": "courrier", "lettre-licenciement.pdf": "courrier",
    "email-2026-03-12-service.eml": "courriel", "email-2026-04-02-absence.eml": "courriel",
}
DATES_ATTENDUES = {
    "contrat-travail.pdf": ["2025-09-01"],
    "convocation-entretien.pdf": ["2026-04-14", "2026-04-20"],
    "lettre-licenciement.pdf": ["2026-04-27", "2026-04-20"],
    "email-2026-03-12-service.eml": ["2026-03-12"],
    "email-2026-04-02-absence.eml": ["2026-04-02", "2026-04-08"],
}
TOUTES = {"supports", "contradicts", "context"}
ENJEUX = [
    ("Les heures supplémentaires de mars 2026 ont-elles été payées ?",
     [("bulletin-paie-2026-03.pdf", TOUTES), ("releve-heures-mars.pdf", TOUTES), ("email-2026-03-12-service.eml", TOUTES)]),
    ("L'absence du 8 au 12 avril 2026 était-elle autorisée ?",
     [("email-2026-04-02-absence.eml", {"supports"}), ("lettre-licenciement.pdf", {"contradicts"})]),
    ("La procédure de licenciement a-t-elle respecté la convocation et le délai après l'entretien ?",
     [("convocation-entretien.pdf", TOUTES), ("lettre-licenciement.pdf", TOUTES)]),
]
# (question, pièces qui doivent être citées, la réponse doit être « le dossier ne le dit pas »)
QUESTIONS = [
    ("Quel est le salaire brut mensuel prévu au contrat de travail ?", {"contrat-travail.pdf"}, False),
    ("Combien d'heures supplémentaires figurent sur le bulletin de paie de mars 2026 ?", {"bulletin-paie-2026-03.pdf"}, False),
    ("Quel motif la lettre de licenciement invoque-t-elle ?", {"lettre-licenciement.pdf"}, False),
    # Corrigé après le premier essai : l'ancienne attente « le dossier ne le dit pas » était fausse, la lettre répond.
    ("Que dit la lettre de licenciement sur l'indemnité de licenciement ?", {"lettre-licenciement.pdf"}, False),
    ("Quel est le numéro de sécurité sociale de la salariée ?", set(), True),
    ("La salariée a-t-elle saisi le conseil de prud'hommes ?", set(), True),
]


if __name__ == "__main__":
    sortie = Path(__file__).parents[1] / "tests" / "fixtures" / "armand-mistral"
    sortie.mkdir(parents=True, exist_ok=True)
    for nom, octets in generer().items():
        (sortie / nom).write_bytes(octets)
    print(f"{len(generer())} pièces écrites dans {sortie}")
