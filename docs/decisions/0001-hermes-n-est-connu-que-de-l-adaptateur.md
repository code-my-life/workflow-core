# ADR 0001 : Hermès n'est connu que de l'adaptateur Hermès, et des profils d'agent

Date : 2026-11-11. Statut : accepté, amendé le 2026-11-12. Reprise de la décision AD-1 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : un workflow, l'écran ou un greffon qui lit une sortie d'Hermès et casse à sa prochaine mise à jour.

## Décision

seul `workflow_core/adapters/hermes/` nomme une commande, une base, une table, une route, une variable `HERMES_*` ou un format d'Hermès. Exception unique : le contenu des profils d'agent d'un workflow vit dans le seul dossier `hermes/profiles/` de son dépôt ; une vérification en CI refuse toute mention d'Hermès ailleurs dans ce dépôt. Un test de contrat du socle tourne contre la version d'Hermès installée, à l'install du socle et à chaque démarrage de son service (AD-21).

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R1, R4, tous les dépôts.

## Amendement du 2026-11-12

Reprise de l'amendement AD-1 de l'architecture du chantier archi-studio (2026-11-12). L'adaptateur écrit dans Hermès par la seule ligne de commande `hermes kanban --board <slug>`, lancée en sous-processus avec une liste d'arguments ; il n'ouvre jamais une base d'Hermès en écriture. Le test de contrat vérifie les options de chaque commande employée et, à l'install, fait un cycle d'écriture complet sur un board jetable.
