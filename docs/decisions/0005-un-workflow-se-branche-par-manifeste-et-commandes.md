# ADR 0005 : un workflow se branche par manifeste et commandes

Date : 2026-11-11. Statut : accepté, amendé le 2026-11-12. Reprise de la décision AD-5 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : un workflow qui parle à Hermès, qui fait tomber le moteur commun, ou dont l'ajout touche un autre dépôt.

## Décision

un workflow est un dépôt séparé, inscrit dans la configuration du socle par sa clé et son chemin. Il publie un manifeste JSON versionné (`schema_version`) : sa clé, sa fiche, ses stations et leur nature, ses réponses reconnues, ses données affichées (AD-11), sa commande `intake` (AD-16) et ses besoins (profils, clés, tâches planifiées, dépôt des issues, routes d'envoi de fichiers, jeton de l'App). Chaque station `code` est une commande que le moteur lance dans le dépôt du workflow (`uv run --directory <dépôt> …`). Entrée JSON sur l'entrée standard : `{schema_version, item, station, attempt, answer}`. Sortie JSON sur la sortie standard : une décision (Conventions). Le moteur applique le délai déclaré par la station ; un dépassement ou une sortie illisible devient `fail`, visible sur la carte et dans l'issue. Une commande est idempotente : relancée après une panne, elle relit l'élément avant d'agir. Le workflow n'appelle jamais Hermès : le moteur applique la décision par l'adaptateur.

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R5, R15, tous les workflows.

## Amendement du 2026-11-12

Reprise de l'amendement AD-5 de l'architecture du chantier archi-studio (2026-11-12). Le manifeste est un JSON versionné (`schema_version`), à la racine du dépôt du workflow (`workflow-manifest.json`). `config/workflows.toml` ne garde, pour un workflow branché par manifeste, que sa clé et le chemin de son dépôt. Les entrées de `factory` et `studio` gardent leur forme actuelle jusqu'à leur étape de migration. Une commande qui se termine avec un code de sortie différent de 0 a échoué, quelle que soit sa sortie : pour une station, c'est `fail` avec la raison « code de sortie <n> » ; pour `intake`, ce n'est aucune décision, comme une sortie illisible (rien n'est écrit, l'issue est relue plus tard). Un délai dépassé et une sortie illisible gardent leurs raisons. Limite connue : la commande d'un workflow tourne sous le même utilisateur Unix que le socle ; elle ne reçoit ni `WORKFLOW_CORE_*`, ni `GITHUB_APP_*`, ni `VIRTUAL_ENV`, mais elle peut lire le jeton en cache de l'App et la clé privée de l'App, et écrire dans le dossier d'état du socle (dont `engine.db`). Seul un dépôt du propriétaire se branche tant que code-my-life/workflow-core#149 n'est pas faite. La commande `intake` est déterministe : la même issue rend toujours le même `id`. Si le commentaire d'acceptation n'est pas posté, elle est relancée à la relecture suivante, et un autre `id` créerait une seconde carte.
