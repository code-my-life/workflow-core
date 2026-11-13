# ADR 0016 : un élément naît d'une issue, et garde une identité unique

Date : 2026-11-11. Statut : accepté, amendé le 2026-11-12. Reprise de la décision AD-16 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : deux façons de créer un élément, deux identifiants pour un même élément, ou un lien élément-issue perdu.

## Décision

tout élément naît d'une issue ouverte dans le dépôt que déclare son workflow (par le propriétaire sur GitHub, ou par factory-live). Le socle reçoit l'événement et appelle la commande `intake` du workflow, qui accepte ou refuse en le disant dans l'issue ; acceptée, l'adaptateur crée la carte racine. L'identifiant d'un élément est `<clé du workflow>:<id local>`, où l'id local est fixé par le workflow et unique dans son workflow (`factory:claude-cockpit#42`, `studio:presenter-factory-live`). L'adaptateur écrit sur la carte racine l'identifiant de l'élément et l'adresse de son issue ; toute carte fille porte l'identifiant de l'élément.

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R5, R13, chemin de bout en bout.

## Amendement du 2026-11-12

Reprise de l'amendement AD-16 de l'architecture du chantier archi-studio (2026-11-12). L'identifiant d'un élément, `<clé du workflow>:<id local>`, est la clé d'idempotence de sa carte racine, et l'adresse de son issue est écrite dans le corps de cette carte. Toute carte fille porte l'identifiant de l'élément dans son corps et la clé d'idempotence `<élément>/<station>/<passage>`, sans aucun lien d'Hermès vers la carte racine.
