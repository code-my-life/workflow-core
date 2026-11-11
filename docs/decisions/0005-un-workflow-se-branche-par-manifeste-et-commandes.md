# ADR 0005 : un workflow se branche par manifeste et commandes

Date : 2026-11-11. Statut : accepté. Reprise de la décision AD-5 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : un workflow qui parle à Hermès, qui fait tomber le moteur commun, ou dont l'ajout touche un autre dépôt.

## Décision

un workflow est un dépôt séparé, inscrit dans la configuration du socle par sa clé et son chemin. Il publie un manifeste JSON versionné (`schema_version`) : sa clé, sa fiche, ses stations et leur nature, ses réponses reconnues, ses données affichées (AD-11), sa commande `intake` (AD-16) et ses besoins (profils, clés, tâches planifiées, dépôt des issues, routes d'envoi de fichiers, jeton de l'App). Chaque station `code` est une commande que le moteur lance dans le dépôt du workflow (`uv run --directory <dépôt> …`). Entrée JSON sur l'entrée standard : `{schema_version, item, station, attempt, answer}`. Sortie JSON sur la sortie standard : une décision (Conventions). Le moteur applique le délai déclaré par la station ; un dépassement ou une sortie illisible devient `fail`, visible sur la carte et dans l'issue. Une commande est idempotente : relancée après une panne, elle relit l'élément avant d'agir. Le workflow n'appelle jamais Hermès : le moteur applique la décision par l'adaptateur.

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R5, R15, tous les workflows.
