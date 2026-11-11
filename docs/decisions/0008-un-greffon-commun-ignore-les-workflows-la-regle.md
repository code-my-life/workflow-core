# ADR 0008 : un greffon commun ignore les workflows ; la règle d'un workflow ne s'applique qu'à son board

Date : 2026-11-11. Statut : accepté. Reprise de la décision AD-8 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : une carte du studio qui déclenche une règle de la factory.

## Décision

un greffon du socle n'applique aucune règle propre à un workflow (`vram-release` en est un). Une règle propre à un workflow est une station ou une commande de ce workflow, que le moteur n'appelle que pour les cartes de son board, ou un greffon de profil rangé dans `hermes/profiles/` (AD-1). `factory-notify` et `factory-live-probe` disparaissent au profit du notificateur et de l'API du socle.

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R9.
