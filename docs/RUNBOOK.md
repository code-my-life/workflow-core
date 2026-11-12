# RUNBOOK du socle

Ce qu'il faut savoir pour faire tourner le socle sur la machine. Le service et le chien de garde s'installent par
`scripts/install.sh` (voir `AGENTS.md`, « Commandes ») ; leur `.env` est `~/.config/workflow-core/.env`, en mode 600,
jamais dans le dépôt (ADR 0002).

## L'App GitHub

Le socle poste ses alertes sous l'identité de l'App GitHub `code-my-life-factory` (ADR 0009). Il fabrique lui-même
ses jetons d'installation sur l'organisation `code-my-life` : il ne lit jamais le cache de jeton de la factory.

### Variables du `.env` du socle

| Variable | Rôle |
|---|---|
| `GITHUB_APP_ID` | L'identifiant de l'App. |
| `GITHUB_APP_PRIVATE_KEY_PATH` | Le chemin de sa clé privée (PEM). La clé reste où elle est jusqu'à l'epic « Le socle administre Hermès ». |
| `WORKFLOW_CORE_NOTIFY` | L'interrupteur du notificateur : `on` l'allume ; absent ou toute autre valeur, il est éteint et ne lit rien. |
| `WORKFLOW_CORE_NOTIFY_SINCE` | L'heure d'allumage, ISO 8601 avec son décalage (`2026-11-20T14:05:00+01:00`), obligatoire avec `on` : seules les attentes ouvertes après elle sont alertées. Absente ou illisible, le notificateur reste éteint. |
| `WORKFLOW_CORE_NOTIFY_REPOS` | Facultative : les dépôts (`code-my-life/workflow-core,…`) dont les issues peuvent recevoir une alerte ; vide, tous. Sert à l'essai sur une issue de test. |
| `WORKFLOW_CORE_LIVE_URL` | L'adresse de factory-live, pour le lien vers l'élément ; sans elle, l'alerte n'a pas de lien. |

Les deux premières sont lues dans l'environnement, sinon dans le `.env` ; les quatre autres, dans l'environnement du
service (le `.env`, chargé par l'unité systemd). Au démarrage, le journal du service dit l'état de l'interrupteur, ses
dépôts, son heure, et l'absence du lien : `journalctl --user -u workflow-core.service --since "-2 min"`.

### Le cache du jeton

`~/.local/state/workflow-core/app-token.json` (dossier d'état du socle), en mode 600, écrit sous verrou
(`app-token.lock`). Le jeton est renouvelé quand il lui reste moins de 10 minutes. Le supprimer force un jeton neuf.

### Les deux commandes, sans le service

Dans `~/perso/workflow-core` :

- `uv run python -m workflow_core.adapters.github token` imprime un jeton (celui du cache s'il est encore bon) ;
- `uv run python -m workflow_core.adapters.github check` imprime, sans le jeton, son heure d'expiration, les
  permissions de l'installation, et si `code-my-life/workflow-core` lui est accessible.

Sans identifiant ni clé, ou avec une clé introuvable, les deux sortent en code 1 avec un message qui nomme la variable
en cause.

### Ce que le socle appelle sur GitHub

Dans l'epic du notificateur : lire et écrire des commentaires d'issue (`POST /repos/{dépôt}/issues/{n}/comments`).
Pour fabriquer le jeton : `GET /orgs/code-my-life/installation` et `POST /app/installations/{id}/access_tokens`.

### Permissions accordées à l'App

À compléter par la tâche du propriétaire qui suit l'issue de l'alerte d'une carte bloquée
(code-my-life/workflow-core#41) : chaque permission, son niveau, et ce qui s'en sert (socle ou factory).

## Le notificateur

Toutes les 30 secondes, une passe lit les cartes des workflows dont l'entrée de `config/workflows.toml` porte
`notify = true` (seulement `factory`). Une carte bloquée reçoit une alerte dans son issue, sauf un blocage
`dependency` et une carte assignée à une voie de `notify_skip_assignees`. Une carte prête mais retenue par la garde
`active_pr` d'Hermès depuis `held_after_minutes` minutes (30 par défaut) reçoit l'alerte « ⚠️ Carte prête mais
retenue », avec la commande de relance par le premier agent de `notify_relay_agents` qui n'est pas son assigné ; une
carte relancée puis retenue de nouveau reçoit une nouvelle alerte. Le test de contrat vérifie que le `payload` de
`respawn_guarded` reste un objet JSON avec un texte `reason`. Chaque alerte finit par la marque cachée
`<!-- workflow-core:attente <clé> -->`, et sa clé est notée dans `~/.local/state/workflow-core/alerts.db` : une clé
notée n'est jamais repostée.

Avant de poster une clé que le registre ne connaît pas, le notificateur lit tous les commentaires de l'issue (pages de
100) : si un commentaire de `code-my-life-factory[bot]` porte déjà sa marque, il ne poste rien et note la clé. Une
marque écrite par un autre compte ne compte pas. Une issue qui ne se lit pas en entier ne reçoit rien à cette passe.
Ainsi, registre perdu ou journal des événements reconstruit, aucune alerte n'est republiée. Un registre illisible est
mis de côté (`alerts.db.illisible-<horodatage>`) et recréé vide ; le journal du service le dit.

Rien n'est posté interrupteur éteint, ni en mode observateur (test de contrat échoué, ADR 0019). Un commentaire
refusé par GitHub est écrit au journal et réessayé à la passe suivante. Une carte sans issue est dite une fois au
journal.

Éteindre le notificateur : retirer `WORKFLOW_CORE_NOTIFY` du `.env`, puis
`systemctl --user restart workflow-core.service`.

## Le chien de garde et l'issue de panne

`workflow-core-watchdog.timer` lance chaque minute `workflow-core-watchdog.service`, qui exécute dans l'ordre :

1. `scripts/healthz_watchdog.py`, avec le Python du système : il interroge `/healthz` et, après 5 minutes d'échec,
   écrit une ligne d'erreur dans journald ; son état est `~/.local/state/workflow-core/watchdog.json` ;
2. `python -m workflow_core.notify.outage`, avec le Python de l'environnement du socle, sans le service : il lit
   `watchdog.json` sans le modifier et garde son propre état, `outage-issue.json` (le numéro de l'issue de la panne en
   cours et l'heure de son début).

Quand le chien de garde a écrit une panne, la commande commente l'issue de panne ouverte de l'App, sinon rouvre et
commente celle fermée depuis moins de 24 heures, sinon ouvre « Le socle workflow-core ne répond plus » dans
`code-my-life/workflow-core`. Chaque issue de panne porte la marque cachée `<!-- workflow-core:panne -->` ; une issue
ouverte par un autre compte n'est jamais reprise. Au retour de `/healthz`, elle commente « Le socle répond de
nouveau, après <n> min de panne » et ferme l'issue. Une panne ne donne qu'une issue et au plus un commentaire.

Elle n'utilise que l'App (permission Issues en écriture), le `.env` et GitHub. Un échec de GitHub est écrit dans
journald (`journalctl --user -u workflow-core-watchdog.service`), rien n'est noté, et le passage suivant réessaie ;
elle sort toujours en code 0.

Réessayer à la main, dans `~/perso/workflow-core` : `set -a; . ~/.config/workflow-core/.env; set +a`, puis
`.venv/bin/python -m workflow_core.notify.outage`.

## L'issue du contrat cassé

Au démarrage, quand le test de contrat échoue, le socle passe en observateur et ouvre dans
`code-my-life/workflow-core` l'issue « Le contrat du socle avec Hermès est cassé », signée par l'App. Elle porte la
marque `<!-- workflow-core:contrat -->`, la version d'Hermès lue, l'heure du test et chaque problème trouvé. C'est la
seule écriture du socle en observateur, et elle part même notificateur éteint.

- Une issue de contrat cassé déjà ouverte reçoit un commentaire à chaque démarrage, au lieu d'une seconde issue ; une
  issue fermée depuis moins de 24 heures est rouverte et commentée.
- Au premier démarrage où le contrat tient de nouveau, l'issue reçoit « Le contrat avec Hermès tient de nouveau » et se
  ferme.
- Son numéro est noté dans `~/.local/state/workflow-core/contract-issue.json` (`{"issue": <numéro> | null}`).
- GitHub injoignable ou App absente : le journal du service le dit (`journalctl --user -u workflow-core.service`), le
  socle reste en observateur, et le démarrage suivant réessaie (`systemctl --user restart workflow-core.service`).
