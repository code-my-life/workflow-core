# ADR 0004 : un seul moteur par board, seul à changer la station, déclenché par l'état

Date : 2026-11-11. Statut : accepté. Reprise de la décision AD-4 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : deux chemins qui font changer de station un même élément, ou un changement manqué parce qu'Hermès n'émet pas d'événement.

## Décision

le moteur du socle est le seul à faire passer un élément d'une station à l'autre, à la sortie d'une station `agent` comme d'une station `code` ou `owner`. Il relit à chaque passe l'état des cartes (il ne dépend d'aucun hook : déblocage, mise en attente et gestes du dashboard d'Hermès n'en émettent pas). Une carte à une station `code` ou `owner` est assignée à un nom de voie `<workflow>-<station>`, qui n'est jamais un profil lançable. Le moteur ne prend une carte (`claim`) que pour l'instant d'une transition, jamais pendant une commande ; il travaille sur la carte prête sous son propre verrou par élément, pour qu'aucune passe ne traite deux fois un élément. Un board n'a jamais deux moteurs actifs : l'ancien est arrêté avant que le nouveau le prenne.

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R3, R15, R19.
