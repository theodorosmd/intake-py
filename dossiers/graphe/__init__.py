"""Version « graphe » du pipeline des dossiers (optionnelle : `pip install -r requirements-graphe.txt`).

Le code simple (`dossiers.importer`, `dossiers.enjeux`) fait le travail ; ce paquet ne le réécrit pas,
il l'enchaîne en graphe pour ajouter deux choses que du Python ordinaire ne donne pas gratuitement :
une pause où l'avocat valide la chronologie, et la reprise là où la panne a eu lieu.

Rien dans la passerelle n'importe ce paquet : sans LangGraph installé, tout le reste tourne.
"""

from dossiers.graphe.pipeline import (
    attente,
    configuration,
    construire_graphe,
    entree,
    proposition_en_attente,
)

__all__ = ["attente", "configuration", "construire_graphe", "entree", "proposition_en_attente"]
