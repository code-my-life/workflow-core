# ADR 0016 : un élément naît d'une issue, et garde une identité unique

Date : 2026-11-11. Statut : accepté. Reprise de la décision AD-16 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : deux façons de créer un élément, deux identifiants pour un même élément, ou un lien élément-issue perdu.

## Décision

tout élément naît d'une issue ouverte dans le dépôt que déclare son workflow (par le propriétaire sur GitHub, ou par factory-live). Le socle reçoit l'événement et appelle la commande `intake` du workflow, qui accepte ou refuse en le disant dans l'issue ; acceptée, l'adaptateur crée la carte racine. L'identifiant d'un élément est `<clé du workflow>:<id local>`, où l'id local est fixé par le workflow et unique dans son workflow (`factory:claude-cockpit#42`, `studio:presenter-factory-live`). L'adaptateur écrit sur la carte racine l'identifiant de l'élément et l'adresse de son issue ; toute carte fille porte l'identifiant de l'élément.

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R5, R13, chemin de bout en bout.
