# Plain Python ou graphe LangGraph pour le pipeline des dossiers

**En bref.** Le graphe donne le même résultat, avec exactement les mêmes appels au modèle, et ajoute une vraie pause pour l'avocat et une reprise après panne sans rien refaire. Il coûte 17 paquets de plus, un conflit de versions à contourner, et plusieurs pièges de reprise que le code simple n'a pas. Pour un pipeline de six étapes avec une seule attente humaine, il se justifie par la pause et la reprise, pas par le reste. Pour l'agent vocal, non.

Mesures du 19 octobre 2026, Python 3.14.6, LangGraph 1.2.0, Postgres 16, faux modèle (aucun réseau).

## Ce qui a été construit

```
ouvrir -> classer -> relever -> (pièce suivante) -> ... -> valider -> ouvrir_enjeu -> analyser_enjeu -> ... -> conclure
                                                              |
                                                  refus de l'avocat -> fin
```

- Chaque nœud appelle le code existant : `classer_et_ranger` et `relever_evenements` (`importer.py`, coupé en deux sans changer son comportement : `importer()` les enchaîne toujours), et `analyser` (`enjeux.py`, inchangé).
- `valider` est la pause : le graphe expose la chronologie, l'avocat répond (valider ou refuser, avec des corrections de date ou de statut), puis l'analyse des questions démarre. Valider confirme ce qui n'a aucune alerte ; ce qui est signalé reste « proposé ».
- Le point de reprise s'écrit après chaque nœud, en mémoire (tests) ou dans Postgres. Il garde l'avancement et de petits bilans, jamais le texte des pièces : celui-ci vit dans le magasin.

## Ce que chacun donne

| | Python simple | Graphe |
| --- | --- | --- |
| Résultat | référence | identique (test sur 10 pièces et 2 questions, magasin mémoire et Postgres) |
| Appels au modèle | N | N, comptés un par un dans les tests |
| Pause pour l'avocat | à construire : deux fonctions, un état en base, deux routes | `interrupt()`, un appel |
| Reprise après panne | à construire : marquer chaque pièce faite | à chaque nœud, mais l'idempotence reste à écrire (voir plus bas) |
| Code | 219 lignes (`importer.py`, `enjeux.py`) | + 242 lignes (`graphe/`, hors démonstration), 21 lignes de plus dans `importer.py` coupé en deux, et 314 lignes de tests |
| Dépendances | aucune de plus | 17 paquets, environ 34 Mo (+10 % de l'environnement) |
| Base de données | les résultats | + environ 31 points de reprise par affaire, 105 ko |
| Temps de traitement hors modèle | 168 ms | 233 ms avec point de reprise en mémoire, 329 ms avec Postgres |

Le surcoût de temps est d'environ 5 ms par nœud avec Postgres, face à plusieurs secondes par appel au modèle : sans importance ici. Il compterait dans une boucle de conversation vocale, où l'on ne met pas de graphe.

## Les pièges rencontrés

1. **Conflit de versions.** La dernière version (1.2.16) ne s'installe pas avec `websockets==17.2`, épinglé pour les flux audio Telnyx : `langgraph-sdk` 0.4 exige `websockets<17`. D'où l'épinglage à 1.2.0 dans `requirements-graphe.txt`, fichier à part que l'image de la passerelle n'installe pas.
2. **Alerte de sécurité acceptée.** `pip-audit` signale `langgraph-sdk` 0.3.15 (PYSEC-2026-4240) : les décorateurs d'autorisation du serveur LangGraph appliquent mal leur argument `actions=`. Nous n'utilisons ni ce serveur ni ces décorateurs. Le correctif (0.4.4) exige `websockets<17`, donc l'alerte est ignorée explicitement dans la CI, par son identifiant. À revoir quand la contrainte disparaît.
3. **Un nœud interrompu est rejoué depuis son début.** Ce qui précède `interrupt()` doit être sans effet de bord ; les écritures viennent après.
4. **Une erreur levée après la reprise fige le fil.** Si le nœud refuse la réponse de l'avocat en levant une exception, les essais suivants rejouent la même réponse et échouent de la même façon, même avec une nouvelle réponse. La parade : vérifier la réponse avant d'écrire, et reposer la question (nouvelle interruption avec le motif). Une vraie panne d'infrastructure, elle, se rejoue telle quelle avec `ainvoke(None, config)`, ce qui est le bon comportement.
5. **`aget_state().next` n'est pas fiable pour savoir si le graphe attend.** Après une seconde interruption dans le même nœud, il était vide alors qu'une question était posée (constaté en 1.2.0). On lit les interruptions des tâches (`proposition_en_attente`).
6. **L'idempotence reste à écrire.** Les événements s'ajoutent sans remplacer : un nœud rejoué après l'écriture les doublerait. `relever` regarde donc d'abord s'il en existe déjà pour la pièce. À l'inverse, `analyser` remplace l'analyse d'une question, donc se rejoue sans risque.
7. **Une fenêtre non couverte.** Une panne entre l'enregistrement de la pièce et la fin du nœud `classer` (quelques millisecondes) la ferait ranger deux fois ; la seconde serait détectée comme doublon. Pour la fermer, il faudrait une clé d'idempotence dans le magasin.

## Quand choisir quoi

- **Le graphe**, quand une étape attend un humain pendant des heures ou des jours, que le processus peut s'arrêter entre-temps, et que l'on veut voir où en est chaque affaire. C'est le cas de la validation d'une chronologie par un avocat.
- **Le code simple**, quand les étapes s'enchaînent d'un trait. C'est le cas de l'import seul, et de tout ce qui est en direct, comme l'agent vocal.
- Ce qu'il faut d'abord regarder dans les deux cas : un état en base qui dit où l'on en est. Le graphe le fournit, mais on peut aussi l'écrire à la main.

Essai avec le vrai modèle (gpt-4.1-mini, mode mémoire, cas « vente », 20 octobre 2026) : 10 pièces importées, 10 événements proposés dont 3 signalés, pause avant l'analyse, 7 événements confirmés après validation, 3 questions analysées. Ce qui n'a pas été fait : mesurer la durée et le coût de cet essai, le comparer au code simple avec le vrai modèle, et reprendre sur Postgres avec le vrai modèle (la reprise n'est testée qu'avec le faux). Les chiffres de temps ci-dessus viennent du faux modèle.
