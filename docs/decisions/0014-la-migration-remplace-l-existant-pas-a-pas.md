# ADR 0014 : la migration remplace l'existant pas à pas, et chaque pas se défait

Date : 2026-11-11. Statut : accepté. Reprise de la décision AD-14 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : un arrêt de la factory ou du studio, ou deux lecteurs d'un même événement.

## Décision

chaque étape ne commence qu'une fois écrite l'ADR qui l'autorise et une fois son retour arrière essayé. Pendant toute la migration, chaque événement GitHub et chaque réponse du propriétaire a un seul lecteur : celui qui possède le moteur du board de l'élément. La lecture des réponses d'un board passe au socle à la même étape que son moteur. Les étapes se font dans l'ordre de la table (4b avant 4). Jusqu'à l'étape 5, l'install de la factory reste seule à écrire `~/.hermes` : un profil qu'un workflow passé au socle doit rendre lançable avant (`studio-writer` à l'étape 4) y est ajouté par une déclaration transitoire, retirée à l'étape 5 quand l'install du socle reprend la liste.

| Étape | Ce qui change | Retour arrière |
| --- | --- | --- |
| 0 | R15, R16, R18 corrigés dans leur dépôt actuel ; R9 : `factory-notify` borné au board `factory` (retiré à l'étape 6) | revert du commit |
| 1 | socle observateur : juge, adaptateur en lecture, API et SSE, test de contrat ; il n'écrit rien | arrêter son service |
| 2 | factory-live lit par le socle, derrière un interrupteur ; fiches embarquées retirées après bascule | rebasculer l'interrupteur |
| 3 | notificateur GitHub pour les alertes seules, en double de Telegram et Web Push | arrêter le notificateur |
| 4b | workflow d'essai, dépôt `code-my-life/workflow-essai`, branché sur le moteur du socle et visible dans factory-live, sans toucher la factory ni le studio ; fixe la première forme du manifeste ; gardé ensuite comme test vivant du branchement | retirer son entrée de la configuration du socle |
| 4 | studio sur le moteur du socle : issues dans `video-content`, intake, gestes `/studio` (lus par la relecture de 10 minutes jusqu'à l'étape 6), brouillon en station `agent` ; service de passe et page du studio arrêtés | relancer le service, gardé désactivé |
| 5 | install du socle, d'abord sans aucune différence avec celle de la factory, puis la factory cesse d'écrire `~/.hermes` | relancer l'install de la factory |
| 6 | factory sur le moteur du socle : gate, release et intake en stations, Funnel vers le socle, gestes `/factory` et revues de PR lus par le socle | réactiver ses tâches planifiées et ses abonnements |
| 7 | coupure de Telegram, Web Push et des notifications natives d'Hermès, après confirmation du propriétaire ; suppression de l'ancien code | aucun : seule exception à R19, acceptée par le propriétaire le 2026-11-11 |

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R19, Constraints (ADR).
