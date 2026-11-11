# ADR 0018 : les contrats sont versionnés, et n'évoluent que par ajout

Date : 2026-11-11. Statut : accepté. Reprise de la décision AD-18 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : un workflow ou un écran sur une ancienne forme qui casse en silence, ou des cartes orphelines.

## Décision

le manifeste, la fiche, l'entrée et la sortie des commandes et l'API portent une version de contrat. Le socle déclare la plage qu'il accepte et refuse au branchement un manifeste hors plage. À l'intérieur d'une version, un contrat ne change que par ajout de champs facultatifs ; les clients ignorent un champ inconnu. Retirer ou renommer une station qui porte des éléments est refusé au branchement.

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : AD-5, AD-6, AD-11, tous les clients.
