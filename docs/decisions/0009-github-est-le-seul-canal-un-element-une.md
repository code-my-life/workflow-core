# ADR 0009 : GitHub est le seul canal ; un élément, une issue

Date : 2026-11-11. Statut : accepté. Reprise de la décision AD-9 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : des alertes en double par plusieurs canaux, ou un élément dont les alertes n'ont pas de place.

## Décision

le notificateur du socle est le seul à prévenir le propriétaire, et seulement sur GitHub, avec l'App, qui appartient au socle avec le renouvellement de son jeton. Chaque élément a exactement une issue, sa boîte, dans le dépôt que déclare son workflow (`factory` : l'issue d'origine ; `studio` : `code-my-life/video-content`). Une issue d'un workflow autre que `factory` ne porte jamais le label `factory`. Chaque alerte porte une marque cachée (clé de l'attente) : le notificateur ne publie jamais deux fois la même, même après une reconstruction (AD-11). Un workflow qui a besoin du jeton de l'App (les PR de la factory) le déclare, et le socle le lui fournit. À l'arrivée, aucun code n'appelle `hermes send` ni Web Push, et les notifications natives d'Hermès vers Telegram sont coupées.

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R10, R13.
