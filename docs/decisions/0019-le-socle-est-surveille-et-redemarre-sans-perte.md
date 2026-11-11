# ADR 0019 : le socle est surveillé, et redémarre sans perte

Date : 2026-11-11. Statut : accepté. Reprise de la décision AD-19 de l'architecture du chantier archi-studio
(2026-11-11), qui la donne pour adoptée.

## Contexte

Ce que la décision empêche : une panne du socle qui arrête moteur et notificateur sans que personne le sache, ou une mise à jour d'Hermès qui fait écrire le socle sur la foi d'un contrat cassé.

## Décision

le socle tourne en service utilisateur systemd, redémarré par systemd s'il tombe, et expose `/healthz`. Un chien de garde indépendant du socle et d'Hermès (minuteur systemd) ouvre ou commente une issue dans `code-my-life/workflow-core` après 5 minutes de `/healthz` en échec ; factory-live affiche le socle injoignable. Au démarrage, le socle relit tout l'état (AD-4) et relance les commandes interrompues, qui sont idempotentes (AD-5). Si le test de contrat échoue (AD-1), le socle passe en observateur : il n'écrit plus ni dans Hermès ni sur GitHub, hors l'alerte de cette panne dans `workflow-core`. Hermès n'est mis à jour qu'après un test de contrat réussi contre sa nouvelle version. Les journaux vont dans journald.

## Écartés

L'architecture du chantier ne détaille pas d'option écartée pour cette décision.

## Conséquences

Exigences et dépôts tenus par cette décision : R3, R4, R10, R19.
