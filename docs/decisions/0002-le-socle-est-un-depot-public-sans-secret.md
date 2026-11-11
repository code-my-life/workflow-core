# ADR 0002 : le socle est un dépôt public, sans secret

Date : 2026-11-11. Statut : accepté. Reprise de la décision AD-2 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : un client public qui ne peut pas lire le contrat dans sa CI, ou un secret qui fuit.

## Décision

le socle est `code-my-life/workflow-core`, public. Jetons, clés et secrets restent dans les `.env` de la machine, lus à l'exécution. factory et video-factory dépendent de lui, jamais l'un de l'autre.

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R2, tous les dépôts.
