# ADR 0006 : un seul juge de la fiche, en Python dans le socle

Date : 2026-11-11. Statut : accepté. Reprise de la décision AD-6 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : des copies des règles de la fiche qui divergent.

## Décision

les règles de la fiche (forme et règles croisées : clés uniques, cases distinctes, groupes d'une seule nature, liens vers des stations connues, clé `models` réservée) vivent dans `workflow_core/model/`, en Pydantic. Une fiche ou un manifeste invalide est refusé quand le workflow se branche, avec la règle cassée nommée. Le socle publie dans `schema/` le JSON Schema de la seule forme ; factory-live en tire ses types et ne porte aucune règle. Aucune fiche n'est embarquée dans un client : sans réponse du socle, l'écran garde la dernière fiche reçue.

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R6, factory-live, factory, studio.
