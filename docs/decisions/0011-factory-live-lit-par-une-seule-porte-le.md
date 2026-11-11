# ADR 0011 : factory-live lit par une seule porte, le socle

Date : 2026-11-11. Statut : accepté. Reprise de la décision AD-11 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : un écran qui lit Hermès, les fichiers d'un workflow ou une API propre à un workflow, ou qui garde un écran faux sans le savoir après une reconstruction.

## Décision

le socle sert une API HTTP en lecture (workflows, fiches, éléments avec leur état complet, attentes, diagnostics, état de la machine) et un flux SSE des changements. Chaque événement porte l'état complet de l'élément et un curseur `<époque>:<n>` ; une nouvelle époque (journal reconstruit) commence par un événement `reset`, qui oblige le client à relire l'état complet. Un changement dans Hermès atteint le flux en moins d'une seconde. factory-live ne lit que cette API et GitHub. Ce qu'un workflow montre en plus de ses cartes (jumeaux, preuves de gate) est une donnée déclarée dans son manifeste, rendue par une commande du workflow, de types génériques (texte, lien, tableau, statut) que l'écran affiche sans code propre à un workflow. L'API n'offre aucun geste. Seule écriture : une route d'envoi de fichiers commune, contrôlée par empreinte SHA-256, qui remet le fichier au workflow qui l'a déclarée. Toute route exige un secret partagé.

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R12, R14.
