# ADR 0001 : Hermès n'est connu que de l'adaptateur Hermès, et des profils d'agent

Date : 2026-11-11. Statut : accepté, amendé le 2026-11-12. Amendé de nouveau le 2026-11-15
(code-my-life/workflow-core#150 : toute la règle d'AD-1, exceptions bornées et API). Reprise de la décision AD-1 de
l'architecture du chantier archi-studio (2026-11-11), qui la donne pour adoptée.

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

## Amendement du 2026-11-15

Reprise de toute la règle d'AD-1 de l'architecture du chantier archi-studio, telle qu'elle est fixée le 2026-11-15
(code-my-life/workflow-core#150). Elle remplace l'« exception unique » de la décision d'origine ; le renvoi au test de
contrat cite AD-19, la décision qui le porte (AD-21 dans le texte d'origine).

**Le code.** Seul `workflow_core/adapters/hermes/` nomme une commande, une base, une table, une route, une variable
`HERMES_*` ou un format d'Hermès. Le contenu des profils d'agent d'un workflow vit dans le seul dossier
`hermes/profiles/` de son dépôt.

**Les exceptions bornées** (2026-11-12). Une vérification en CI, dans chaque dépôt de workflow et dans factory-live,
refuse toute mention d'Hermès hors de ces exceptions, et d'elles seules :
- le dossier `hermes/profiles/` ;
- les ADR de `docs/decisions/` et le journal historique du dépôt (`docs/JOURNAL.md`), qui ne se réécrivent pas ;
- un contenu tiers figé, déclaré dans la liste que lit cette vérification et nulle part ailleurs (comme
  `vitrine/factory-live/` de video-factory) ;
- le nom du dépôt `hermes-software-factory` ;
- dans hermes-software-factory seulement, l'outillage de l'organisation, déclaré dans la même liste et nulle part
  ailleurs ; les commandes des stations du workflow `factory` restent soumises à la règle stricte, et un fichier qui
  sert les deux la suit ;
- dans factory-live seulement, les dictionnaires de l'interface `apps/web/src/i18n/` et les données de démonstration
  `apps/web/src/demo/` : des textes et des données, jamais du code.

**L'API** (2026-11-15). L'API du socle n'expose aucun nom propre à Hermès dans ses identifiants ni dans ses valeurs
énumérées : clés, groupes, sortes, sources (`runtime`, pas `hermes`). Les textes qu'elle adresse au propriétaire
peuvent le nommer, et une commande qu'ils proposent de lancer nomme le vrai programme ; un client les affiche tels
quels, sans les interpréter. Les valeurs propres à factory-live qui nomment encore Hermès sont admises par une
exemption provisoire de sa vérification, retirée quand factory-live les renomme. Dans ce dépôt, les contrôles de la
machine que pose l'adaptateur et ceux que déclare `config/workflows.toml` pour le runtime sont du groupe `runtime`.

**L'écriture** : celle de l'amendement du 2026-11-12, ci-dessus. Le test de contrat tourne contre la version d'Hermès
installée, à l'install du socle et à chaque démarrage de son service (AD-19).
