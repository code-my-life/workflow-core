# ADR 0003 : l'adaptateur nomme toujours le board, et ne croit jamais le board d'un hook

Date : 2026-11-11. Statut : accepté. Reprise de la décision AD-3 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : une action appliquée au mauvais board (Hermès transmet aux greffons le board courant du processus).

## Décision

chaque appel de l'adaptateur passe le board explicitement. Un greffon du socle qui reçoit un hook retrouve le board de la carte depuis son identifiant, par l'adaptateur, et ignore le champ `board` du hook. La clé d'un workflow est le slug de son board.

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R3, R16.
