# ADR 0001 : Hermès n'est connu que de l'adaptateur Hermès, et des profils d'agent

Date : 2026-11-11. Statut : accepté. Reprise de la décision AD-1 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : un workflow, l'écran ou un greffon qui lit une sortie d'Hermès et casse à sa prochaine mise à jour.

## Décision

seul `workflow_core/adapters/hermes/` nomme une commande, une base, une table, une route, une variable `HERMES_*` ou un format d'Hermès. Exception unique : le contenu des profils d'agent d'un workflow vit dans le seul dossier `hermes/profiles/` de son dépôt ; une vérification en CI refuse toute mention d'Hermès ailleurs dans ce dépôt. Un test de contrat du socle tourne contre la version d'Hermès installée, à l'install du socle et à chaque démarrage de son service (AD-21).

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R1, R4, tous les dépôts.
