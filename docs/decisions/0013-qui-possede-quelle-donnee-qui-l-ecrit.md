# ADR 0013 : qui possède quelle donnée, qui l'écrit

Date : 2026-11-11. Statut : accepté. Reprise de la décision AD-13 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : deux propriétaires d'une même donnée, ou un cache du socle pris pour la vérité.

## Décision

| Donnée | Propriétaire | Écrivains permis |
| --- | --- | --- |
| état d'une carte (statut, assigné, commentaires) | Hermès | adaptateur Hermès du socle ; l'agent d'une station `agent`, sur sa propre carte et ses cartes filles, par les outils d'Hermès |
| station courante d'un élément | le socle | le moteur seul (AD-4) |
| lien élément, issue et cartes | le socle | l'adaptateur, sur la carte racine ; reconstructible |
| état métier d'un élément (`state.json` d'une vidéo, PR d'une issue) | le workflow | ses commandes et ses agents |
| fiche et manifeste | le workflow | son dépôt ; le socle les juge |
| issue d'un élément | GitHub | le notificateur du socle ; le propriétaire et factory-live (au nom du compte du propriétaire) pour l'ouvrir et y répondre |
| journal des événements, curseurs SSE | le socle | le socle ; projection reconstructible depuis Hermès |
| réglages de la machine sous `~/.hermes` | le socle | install du socle |

L'état d'un élément ne se déduit jamais de l'état ouvert ou fermé de son issue : une issue fermée par une PR (`Closes`) ne termine pas l'élément.

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : tous.
