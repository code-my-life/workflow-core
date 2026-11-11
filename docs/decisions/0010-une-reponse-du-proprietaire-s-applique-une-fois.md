# ADR 0010 : une réponse du propriétaire s'applique une fois, à l'attente qu'elle nomme

Date : 2026-11-11. Statut : accepté. Reprise de la décision AD-10 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : le même geste qui n'a pas le même effet par GitHub et par factory-live, un geste appliqué deux fois, ou un geste d'un écran périmé qui valide la mauvaise attente.

## Décision

trois sortes de réponses, toutes reçues par le socle seul : un commentaire de geste sur l'issue de l'élément, une revue de PR (l'approbation de la factory, ADR 0007), un envoi de fichier (AD-11). Seules comptent celles du compte du propriétaire. Chaque attente a un identifiant, inscrit dans son alerte ; une réponse s'applique à l'attente ouverte de l'élément, une seule fois par identifiant de commentaire ou de revue ; sans attente ouverte, le bot répond 😕, sinon 👍. Le geste `unblock` vaut pour toute carte bloquée, y compris d'une station `agent`. Le moteur transmet la réponse à la commande du workflow dans le champ `answer`. factory-live poste les gestes et les issues au nom du compte du propriétaire, avec le jeton utilisateur que l'App lui délivre et qu'il garde dans sa base (réalité de l'ADR 0003 de factory-live). Un déblocage fait directement dans Hermès est hors contrat ; le moteur le voit comme un changement d'état (AD-4).

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R11, R12, contrainte du compte du propriétaire (ADR 0007 de la factory).
