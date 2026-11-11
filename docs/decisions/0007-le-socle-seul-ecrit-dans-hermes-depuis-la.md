# ADR 0007 : le socle seul écrit dans `~/.hermes`, depuis la somme des besoins déclarés

Date : 2026-11-11. Statut : accepté. Reprise de la décision AD-7 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : deux installs qui écrasent les mêmes clés, ou une installation neuve à laquelle il manque une tâche créée à la main.

## Décision

trois cas. (1) Ce qui vaut pour toute la machine (réglages `kanban.*`, modèles auxiliaires, plateforme webhook, langue, profils lançables, hooks sortants, greffons communs, gateway, `.env` racine) est écrit par l'install du socle seule ; ces réglages sont ceux du socle, aucun workflow ne les déclare. (2) Ce qui ne sert qu'un workflow (ses profils, ses tâches planifiées) figure dans son manifeste et le socle l'installe ; deux workflows qui déclarent le même nom font refuser l'install. (3) L'outillage de l'organisation (règles et hooks de Claude Code, wrapper `gh`, OpenCode, graphify, hooks git, `checks.json`, types d'issues) reste dans hermes-software-factory. Aucun workflow n'écrit dans `~/.hermes`. La liste des profils lançables n'est jamais vide (Hermès ne lancerait plus rien). Toute carte est créée avec un assigné explicite : aucun workflow ne compte sur `default_assignee` ni `orchestrator_profile`. L'install est idempotente et recrée tout ce que les manifestes déclarent.

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R7, R8, R18.
