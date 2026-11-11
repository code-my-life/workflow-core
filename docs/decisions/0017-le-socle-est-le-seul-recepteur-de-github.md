# ADR 0017 : le socle est le seul récepteur de GitHub

Date : 2026-11-11. Statut : accepté. Reprise de la décision AD-17 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : plusieurs lecteurs des événements GitHub, ou Hermès sur le chemin de GitHub au socle.

## Décision

les webhooks GitHub (issues, commentaires, revues de PR) arrivent sur une route du socle, par le seul port exposé publiquement (Tailscale Funnel). Le socle vérifie la signature HMAC et l'auteur (`author_association`, le compte du propriétaire) ; une relecture toutes les 10 minutes rattrape un envoi perdu. Jusqu'à l'étape 6 d'AD-14, cette relecture est la seule entrée des workflows déjà passés au socle (un geste peut mettre 10 minutes à agir) ; le Funnel bascule vers le socle à l'étape 6. Les abonnements webhook appartiennent à l'install du socle ; aucun manifeste n'en déclare.

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R10, R11, R13.
