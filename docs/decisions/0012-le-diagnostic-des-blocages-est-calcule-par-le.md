# ADR 0012 : le diagnostic des blocages est calculé par le socle

Date : 2026-11-11. Statut : accepté. Reprise de la décision AD-12 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : un écran qui lit les journaux et les formats d'Hermès pour expliquer un blocage.

## Décision

l'adaptateur Hermès lit les journaux des agents, les erreurs des runs et le code des greffons ; `workflow_core/diagnosis/` en tire un diagnostic par règles pures, sans modèle d'IA, pour toute carte de tout workflow. Le socle le publie par l'API ; factory-live l'affiche sans règle.

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R1, R4, R14.
