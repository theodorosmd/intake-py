# Base de connaissances du cabinet (démo)

Contenu **fictif**, écrit pour la démonstration : ce cabinet n'existe pas. Chaque section `## …` devient
un morceau indexé (embeddings + pgvector) que l'agent peut retrouver pendant un appel avec l'outil
`search_firm_info`. Modifier un fichier suffit : au démarrage, la gateway ne recalcule que les
sections qui ont changé et retire celles qui ont disparu.

Règle d'écriture : des faits sur le cabinet (horaires, honoraires, déroulé), jamais de conseil
juridique. Les questions juridiques ont leur propre section, qui renvoie vers l'avocat.
