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
| `WORKFLOW_CORE_OWNER_LOGIN` | Le compte GitHub du propriétaire, celui au nom duquel factory-live ouvre une issue et commente : seuls ses issues et ses gestes sont pris par la relecture de GitHub. Absente, la relecture ne prend aucune issue et aucun geste ; le reste du socle tourne. |

Les deux premières sont lues dans l'environnement, sinon dans le `.env` ; les cinq autres, dans l'environnement du
service (le `.env`, chargé par l'unité systemd). Au démarrage, le journal du service dit l'état de l'interrupteur, ses
dépôts, son heure, l'absence du lien et celle du compte du propriétaire :
`journalctl --user -u workflow-core.service --since "-2 min"`. Aucune de ces variables n'est passée aux commandes
qu'il lance.

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
Pour les gestes du propriétaire (voir « Les gestes ») : lire les commentaires d'une issue créés ou modifiés depuis sa
relecture précédente, moins deux minutes (tous à la première relecture après un démarrage)
(`GET /repos/{dépôt}/issues/{n}/comments?since=…`, page par page), lire les réactions d'un commentaire qui en porte
(`GET /repos/{dépôt}/issues/comments/{id}/reactions`, page par page), et réagir à un commentaire au nom de l'App
(`POST /repos/{dépôt}/issues/comments/{id}/reactions`, `+1` ou `confused`). Toutes relèvent de la permission Issues.

### Permissions accordées à l'App

Relevées le 2026-11-12 par `uv run python -m workflow_core.adapters.github check`, sur l'installation de l'organisation
`code-my-life` (code-my-life/workflow-core#41). Aucune n'a été retirée : la factory se sert encore de l'App comme
d'un besoin, jusqu'à l'epic « La factory sur le moteur du socle ».

| Permission | Niveau | Qui s'en sert |
|---|---|---|
| Issues | écriture | le socle (alertes, issue de panne, issue du contrat cassé) et la factory (fil de l'issue, commandes `/factory`) |
| Metadata | lecture | tous (obligatoire pour toute App) |
| Contents | écriture | la factory (push des branches, fusion par la release) |
| Pull requests | écriture | la factory (ouverture, mise à jour, revue demandée, auto-merge) |
| Commit statuses | écriture | la factory (statut `factory/gate`) |
| Checks | lecture | la factory (lecture de la CI par la gate) |
| Actions | lecture | la factory (lecture des runs de CI) |
| Workflows | écriture | la factory (pousser une PR qui modifie `.github/workflows/`) |
| Deployments | écriture | la factory (déploiements de la release) |

Le socle n'a besoin que d'Issues en écriture (et de Metadata). Une permission retirée dans les réglages de l'App doit
être acceptée sur l'installation de l'organisation, puis ce tableau mis à jour.

## Le notificateur

Toutes les 30 secondes, une passe lit les cartes des workflows dont l'entrée de `config/workflows.toml` porte
`notify = true` (seulement `factory`), et celles des workflows branchés par manifeste (plus bas). Une carte bloquée
reçoit une alerte dans son issue, sauf un blocage `dependency` et une carte assignée à une voie de
`notify_skip_assignees`. Une carte prête mais retenue par la garde
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

Une carte assignée à un nom de voie d'un workflow branché par manifeste (`<clé>-<station>`, par exemple
`essai-revue`) n'est jamais une carte retenue : une voie n'est pas un agent, la carte peut y rester prête longtemps.
Elle n'a donc jamais d'alerte « Carte prête mais retenue » ; ses blocages sont alertés comme les autres (#61).

**Les workflows branchés par manifeste** (`essai`) sont toujours notifiés, sans clé `notify`, sous le même
interrupteur, la même heure d'allumage, la même liste de dépôts et le même registre que la factory. Chaque alerte va
dans l'issue dont l'adresse est écrite dans le corps de la carte :

- une carte racine en attente (`scheduled`) à la voie d'une station `owner` reçoit « ⏸ Ta réponse est attendue :
  <station> », avec l'identifiant de l'attente (`essai:owner:<carte>:<événement>`, aussi sa clé) et un geste
  `/essai <geste> [note]` par geste de sorte `comment` du manifeste (`<station>` est l'étiquette de la station dans
  la fiche, à défaut sa clé) ;
- une carte racine bloquée reçoit « ⛔ Échec à la station <station> », avec le motif écrit par le moteur et
  `/essai unblock [note]` ; une carte fille d'une station `agent` (`essai:7/brouillon/1`) bloquée reçoit la même
  alerte, avec la question de l'agent, dans l'issue de son élément : elle se débloque sur le board (opérateur). Leur
  clé est celle d'une carte bloquée de la factory (`essai:<sorte>:<carte>:<événement>`, avec la sorte du blocage :
  `needs_input` ou `capability` pour une carte racine, voir « Le moteur »).

**Les signalements du moteur.** Le moteur note dans `~/.local/state/workflow-core/engine.db` deux anomalies d'une
carte racine, chacune dite une fois au journal (ligne `<4>`) et alertée une fois dans l'issue de l'élément
(« ⚠️ ... ») ; le notificateur les lit sans jamais écrire dans `engine.db` :

- A, **carte racine terminée par un autre que le moteur** (`essai:finished_elsewhere:<carte>`). Le moteur note
  chaque carte racine qu'il termine lui-même, juste avant d'écrire sa fin (table `finishes`). Une carte racine
  `done` qu'il n'a pas notée, à n'importe quelle station, est signalée, et le moteur ne touche plus jamais à son
  élément, même après un redémarrage. Pour reprendre l'élément : comprendre qui a terminé la carte (journal
  d'Hermès, `hermes kanban --board <board> show <carte>`), puis rouvrir une nouvelle issue.
- B, **carte racine prise par un agent** (`essai:taken:<carte>:<run>`, une fois par run). Tant que le run dure,
  le moteur ne touche pas à l'élément : aucune écriture, aucune commande, les réponses du propriétaire restent en
  attente. Le run fini, l'élément reprend son cours. Seules les cartes filles se confient à un agent : regarder le
  profil qui a pris la carte racine.

L'heure d'un signalement est celle de la carte (sa fin pour A, le début du run pour B) : une carte terminée par un
autre avant l'heure d'allumage, par exemple terminée par le moteur avant qu'il note ses fins, est dite au journal et
abandonnée, mais jamais alertée.

**C, version d'Hermès changée en marche.** La veille de version (toutes les 5 minutes) passe le socle en observateur
jusqu'au redémarrage, et ouvre l'issue « contrat cassé » de code-my-life/workflow-core, ou commente celle déjà
ouverte par l'App (jamais deux). GitHub injoignable : une ligne `<3>` au journal, rien d'autre. Pour en sortir :
`systemctl --user restart workflow-core.service`, dont le test de contrat juge la nouvelle version.

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
Une version d'Hermès changée pendant que le socle tourne ouvre la même issue (ou commente celle déjà ouverte), avec la
version d'avant et celle lue (voir « Le notificateur », C).

- Une issue de contrat cassé déjà ouverte reçoit un commentaire à chaque démarrage, au lieu d'une seconde issue ; une
  issue fermée depuis moins de 24 heures est rouverte et commentée.
- Au premier démarrage où le contrat tient de nouveau, l'issue reçoit « Le contrat avec Hermès tient de nouveau » et se
  ferme.
- Son numéro est noté dans `~/.local/state/workflow-core/contract-issue.json` (`{"issue": <numéro> | null}`).
- GitHub injoignable ou App absente : le journal du service le dit (`journalctl --user -u workflow-core.service`), le
  socle reste en observateur, et le démarrage suivant réessaie (`systemctl --user restart workflow-core.service`).

## Le test de contrat et la version d'Hermès

- Le test de contrat vérifie aussi les commandes que le socle lance : pour chacune de `COMMANDS` (`contract.py`),
  `hermes kanban <commande> --help` doit sortir en code 0 et nommer chaque option déclarée. Sinon, le verdict porte
  « commande `create` : option `--idempotency-key` absente » ou « commande `schedule` absente ».
- `uv run pytest tests/contract -rs` (lancé par `scripts/install.sh`) fait en plus un cycle d'écriture complet sur un
  board jetable `contrat-<heure Unix>`, relu après chaque pas, puis supprimé, même après un pas en échec. Un board
  `contrat-*` laissé par un cycle interrompu est supprimé au cycle suivant. Aucun autre board n'est écrit. Deux
  lancements du test de contrat en même temps sur la machine (deux `uv run pytest`) se gênent : chaque cycle supprime
  au départ le board de l'autre, qui échoue ; ne lancer qu'un test de contrat à la fois.
- Le socle relit `hermes --version` toutes les 5 minutes, et à la première passe du moteur après un démarrage : son
  écrivain refuse toute écriture tant que cette relecture n'a pas confirmé la version du test de contrat. Une version
  changée ou illisible le met en observateur, avec « version d'Hermès changée : 0.22.2 → 0.22.3 » dans le verdict de
  `/api/machine`, une ligne au journal et l'issue du contrat cassé (ouverte, ou commentée si elle l'est déjà). Il y
  reste jusqu'au redémarrage : après une mise à jour d'Hermès, lancer `uv run pytest tests/contract -rs` contre la
  nouvelle version, puis, s'il est vert, `systemctl --user restart workflow-core.service`.

## Le moteur

Toutes les 30 secondes, en mode normal seulement, une passe du moteur parcourt chaque workflow branché par son
**manifeste** (`workflow-manifest.json` à la racine de son dépôt, déclaré dans la configuration par `key` et `repo`).
Le moteur crée le board du workflow si sa base n'existe pas, relit ses cartes, et reconnaît une **carte racine** (la
carte d'un élément) à sa clé d'idempotence `<clé>:<id local>`. Une carte racine prête, assignée au **nom de voie**
`<clé>-<station>` d'une station `code`, fait lancer la commande de la station dans le dépôt du workflow, puis le
moteur applique sa **décision** :
- `advance` réassigne la carte au nom de voie de la station `to`, ou de la suivante ; après la dernière station du
  manifeste, il termine la carte racine (`complete <carte> --result <raison>`), seul moment où le moteur termine une
  carte racine : il le note juste avant dans `engine.db` (table `finishes`), et une carte racine terminée sans cette
  note est signalée (voir « Le notificateur », A) ;
- `wait` laisse la carte telle quelle : la commande est relancée à la passe suivante, avec un essai de plus ;
- `fail`, un délai dépassé ou une sortie illisible bloquent la carte avec son motif, « Échec de la station
  preparer : délai de 60 s dépassé ». Une carte bloquée n'est plus lancée ; débloquée
  (`hermes kanban --board <slug> unblock <carte>`), elle redevient prête à sa station et la commande est relancée.
  Chaque blocage porte une **sorte** (`block --kind`), jamais celle du blocage précédent de la carte : `needs_input`,
  puis `capability`, puis `needs_input`… Hermès garde la sorte d'un blocage après le déblocage (seul `complete`
  l'efface) et envoie en `triage` une carte bloquée de nouveau pour la même sorte (sa garde contre les boucles de
  déblocage, événement `block_loop_detected`, `BLOCK_RECURRENCE_LIMIT = 2`) ; le moteur ne lance plus une carte en
  `triage`. En changeant de sorte à chaque blocage, la carte reste `blocked`, et le propriétaire peut toujours la
  débloquer.
  Une station `to` inconnue du manifeste bloque aussi la carte, avec « station « <to> » inconnue ».

Une carte racine prête au nom de voie d'une station `owner` (où l'on attend le propriétaire) est mise en **attente** :
`schedule <carte> "En attente du propriétaire à la station <station>"`, et la carte passe en `scheduled`. L'attente a
un identifiant, `<clé>:owner:<carte>:<événement>`, l'événement étant l'identifiant du dernier événement `scheduled`
de la carte dans Hermès ; le journal le donne (« attente essai:owner:t_ab12cd34:812 ouverte »). Les **réponses du
propriétaire** (un geste et sa note) arrivent dans la table `answers` de `engine.db`, dans le dossier d'état du socle.
À la passe suivante, une réponse pour un élément en attente est passée à la commande de la station (champ `answer`)
et marquée appliquée à l'attente, une seule fois ; une décision `wait` laisse la carte en attente, toute autre
décision la débloque (`unblock`) puis s'applique comme ci-dessus. Une réponse pour un élément sans attente ouverte est
marquée refusée et ne change rien, sauf quand la carte racine de l'élément a un run en cours : ses réponses (et ses
gestes inconnus) restent alors en attente jusqu'à la fin du run. Une carte racine terminée par un autre que le moteur
n'est plus jamais touchée, même après un redémarrage (voir « Le notificateur », A). Après un redémarrage, l'attente
est retrouvée dans Hermès et une réponse non traitée est traitée à la première passe.

Le moteur écrit dans Hermès par la seule ligne de commande `hermes kanban --board <slug>`, ne prend jamais une carte
(`claim`), et saute un élément qu'une passe précédente traite encore. En mode observateur, il ne lance rien, n'écrit
rien et ne lit pas les réponses, qui attendent ; le journal le dit une fois.

Un manifeste absent, illisible ou d'une autre clé laisse son workflow non branché : le journal du service le dit au
démarrage, avec la raison (« workflow essai non branché : manifeste absent : … »), et les autres workflows tournent.
Une écriture refusée par Hermès est écrite au journal avec la commande, son code et sa sortie d'erreur ; la passe
suivante réessaie.

## La relecture de GitHub

Un élément d'un workflow branché par manifeste naît d'une **issue** ouverte dans l'un des dépôts que déclare son
manifeste (`issues_repos`, une liste d'au moins un dépôt ; un dépôt n'est lu que par un seul workflow : le second
workflow qui le déclare reste non branché, règle `shared-issues-repo`). Le socle ne reçoit aucun webhook : il **relit**
chacun de ces dépôts au démarrage, puis toutes les 10 minutes (`GITHUB_POLL_SECONDS = 600` dans
`workflow_core/intake.py`), au nom de l'App `code-my-life-factory`. Il ne lit aucun autre dépôt. La relecture ne
tourne que si un workflow est branché par son manifeste.

Issues prises : les issues ouvertes (pas les pull requests, que l'API de GitHub range parmi les issues) ouvertes par le
compte du propriétaire (celui au nom duquel factory-live ouvre une issue), que le socle lit dans la variable
`WORKFLOW_CORE_OWNER_LOGIN` de son `.env` : l'y ajouter avant de brancher un workflow. Sans elle, aucune issue n'est
prise, et le journal le dit une fois au démarrage. Les autres issues sont ignorées sans commentaire. Une entrée de la configuration peut porter `issue_label` : seules les issues à ce label sont prises
(`config/essai-test.toml` a `issue_label = "test"`). Une entrée sans `issue_label` ignore les issues au label `test` :
le socle de production ne prend jamais une issue de la machine de test.

Pour chaque issue nouvelle, le socle lance la commande `intake` du manifeste (`uv run --directory <dépôt> …`, avec son
délai, sans les secrets du socle), avec l'issue en JSON sur son entrée :
`{"schema_version": 1, "issue": {"repo", "number", "url", "title", "body", "labels"}}`. Elle rend
`{"decision": "accept", "id": "<id>", "reason": "…"}` (`reason` facultatif ; `id` sans espace, `/` ni `:`) ou
`{"decision": "refuse", "reason": "…"}` (`reason` obligatoire).
- Acceptée : le socle crée la carte racine de l'élément `<clé>:<id>` (titre `<clé>:<id> · <titre de l'issue>`, nom de
  voie de la première station, clé d'idempotence `<clé>:<id>`, adresse de l'issue dans le corps), puis commente
  l'issue : « ✅ Cette issue devient l'élément `essai:7` du workflow `essai` ; il commence à la station `preparer`. »,
  le lien `<WORKFLOW_CORE_LIVE_URL>/workflows/<clé>` (omis sans la variable), le motif, et la marque. Le moteur prend
  l'élément à sa passe suivante.
- Acceptée, mais la clé `<clé>:<id>` est déjà celle d'une carte d'une autre issue (ou d'une carte faite à la main
  sans adresse d'issue) : l'issue est refusée, avec un commentaire de refus qui nomme l'autre issue, une ligne `<4>`
  au journal, et rien d'autre d'écrit.
- Refusée : un commentaire « 🚫 Cette issue n'est pas prise par le workflow `essai` : <motif>. », avec la marque ;
  aucune carte.
- Sortie illisible, délai dépassé ou lancement impossible : ce n'est pas une décision. Le journal le dit, rien n'est
  créé ni posté, et la relecture suivante relance la commande.

Une issue traitée porte une **marque**, un commentaire HTML invisible à l'écran :
`<!-- workflow-core:intake <dépôt>#<n> accept -->` ou `<!-- workflow-core:intake <dépôt>#<n> refuse -->`. Seule la
marque d'un commentaire de l'App compte. Le socle note aussi l'issue dans la table `intake` d'`engine.db` (dossier
d'état du socle : `repo`, `number`, `item`, `decision`, `handled_at`) pour ne pas relire ses commentaires à chaque
relecture. Registre perdu ou supprimé : la marque suffit, et l'issue est notée de nouveau. Une issue marquée n'est plus
jamais passée à `intake`. Pour qu'une issue soit relue, il faudrait retirer sa ligne de la table et son commentaire
marqué.

Si la carte est créée mais que le commentaire n'est pas posté (GitHub en échec, service arrêté), l'issue reste sans
marque : à la relecture suivante, `intake` est relancée, la création rend la carte existante (même clé
d'idempotence), et le commentaire est posté. Jamais deux cartes pour un élément.

En mode observateur, ou tant que la version d'Hermès n'est pas confirmée, la relecture lit les issues mais ne lance
pas `intake`, ne crée rien et ne poste rien. En mode observateur, le journal le dit une fois par issue (« en attente
du mode normal, intake non lancée ») ; tant que la version n'est pas confirmée, il ne dit rien.

Le journal du service préfixe ses lignes par `workflow-core: relecture` : issue acceptée (élément et carte), refusée
(motif), décision dite dans l'issue (adresse du commentaire), déjà traitée, issues ou commentaires illisibles, commentaire
non posté, intake sans décision.

## Les gestes

Le propriétaire répond à un élément par un **geste** : un commentaire sur l'issue de l'élément (l'adresse écrite dans
le corps de sa carte racine), posté sur GitHub ou depuis factory-live, qui le poste en son nom. Sa première ligne est
exactement `/<clé> <geste>`, suivie d'une note facultative (le reste de la ligne) : `/essai valider c'est bon`. La casse
compte (`/Essai valider` n'est pas un geste), et le geste doit ouvrir le commentaire.

Seuls comptent les commentaires du compte du propriétaire (la variable `WORKFLOW_CORE_OWNER_LOGIN` du `.env`, le même
que pour la relecture des issues ; sans elle, aucun geste n'est pris) ; tout autre commentaire est
ignoré sans réaction, de même qu'un geste d'un autre workflow (`/factory unblock` sur l'issue d'un élément `essai` :
la factory lit ses gestes elle-même). Gestes reconnus : ceux de sorte `comment` du manifeste (`valider` et `refaire`
pour le workflow d'essai), plus `unblock`, valable pour tout workflow. Un geste de sorte `issue` (`ouvrir`) ne se donne
pas en commentaire : `/essai ouvrir` est un geste inconnu.

Lecture : à chaque relecture de GitHub (au démarrage, puis toutes les 10 minutes), après les issues, le socle lit les
commentaires de l'issue de chaque élément dont la carte racine n'est pas terminée, et de chaque élément terminé dont
l'issue est encore ouverte. Il ne lit que les commentaires créés ou modifiés depuis sa relecture précédente de cette
issue (deux minutes plus tôt, pour les horloges ; le filtre `since` de GitHub porte sur l'heure de modification) ;
tous, à la première relecture après un démarrage. Un commentaire déjà pris ne l'est jamais une seconde fois, même
modifié. Un geste entre dans la boîte
des réponses (`answers` d'`engine.db`) avec l'identifiant `github:<identifiant du commentaire>`, la source `github`, et
l'heure de création du commentaire.

**Une seule fois** : un identifiant déjà dans la boîte n'y entre jamais une seconde fois, et un commentaire qui porte
déjà une réaction de l'App n'est jamais repris, même si `engine.db` est perdu.

Application, par le moteur à sa passe suivante (30 secondes au plus) :
- un geste du manifeste pour un élément en attente à une station `owner`, posté après l'ouverture de l'attente
  (l'événement `scheduled` de la carte ; même seconde acceptée), est passé à la commande de la station (`answer`) et
  marqué appliqué, comme une réponse de l'outil de la machine de test ;
- un geste du manifeste sans attente ouverte (élément terminé, bloqué, à une station `code`), ou posté avant
  l'ouverture de l'attente en cours (un écran périmé), est refusé ;
- `unblock` sur un élément dont la carte racine est bloquée : la carte est débloquée (`unblock`), la réponse est
  marquée appliquée au blocage (`<clé>:blocked:<carte>:<événement blocked>`), et la commande de sa station est
  relancée aussitôt avec `answer` `{"gesture": "unblock", "note": …}`, sa décision appliquée. Si elle échoue de
  nouveau, la carte est bloquée de nouveau, avec une autre sorte de blocage (voir « Le moteur ») : elle reste
  `blocked`, et un nouvel `unblock` s'y applique. Sur un élément non bloqué, `unblock` est refusé. Un déblocage
  qu'Hermès refuse d'écrire est écrit au journal (`<3>`) et la réponse est refusée (😕) : la carte reste bloquée, et le
  propriétaire peut reposer le geste ;
- un geste inconnu du workflow est refusé.

Ces règles ne valent que pour les gestes de GitHub : une réponse de `scripts/inject_answer.py` (source `local`) garde
le traitement décrit dans « Le moteur ».

**Réaction** : une fois le geste traité, l'App réagit au commentaire, 👍 (`+1`) s'il est appliqué, 😕 (`confused`)
s'il est refusé, en quelques secondes après la passe du moteur ; la colonne `reacted_at` de la boîte le note. Une
réaction que GitHub refuse est écrite au journal (`<3>`) et réessayée après la relecture suivante, pas avant ; une
réaction refusée en 404 (commentaire supprimé) est abandonnée, avec une seule ligne au journal (`<4>`). Le
propriétaire a donc sa réponse en moins de 11 minutes : 10 minutes au plus jusqu'à la relecture, 30 secondes jusqu'à
la passe, puis la réaction.

En **mode observateur**, les gestes lus entrent dans la boîte, mais le moteur n'applique rien et l'App ne réagit pas ;
au retour du mode normal, chaque geste est appliqué une fois et reçoit sa réaction.

Le journal du service préfixe ces lignes par `workflow-core: gestes` (geste reçu, réaction posée ou non posée,
commentaires illisibles) ; l'application et le refus sont écrits par le moteur (`workflow-core: moteur`).

## La machine de test

La machine de test est la machine de production, avec le même Hermès, mais un board à part, `essai-test`. Un second
socle y tourne, lancé depuis la branche en cours, sur le port 8449, avec sa propre configuration
(`config/essai-test.toml`, qui ne déclare que le workflow d'essai `essai` sur le board `essai-test`) et son propre
dossier d'état. Le socle de production (port 8447, `~/perso/workflow-core`) n'est pas touché. Le dépôt du workflow
d'essai, `code-my-life/workflow-essai`, est cloné dans `~/perso/workflow-essai`.

Lancer le socle de test, depuis le dossier de la branche (`uv sync --locked` fait) :

```sh
set -a; . ~/.config/workflow-core/.env; set +a
unset WORKFLOW_CORE_NOTIFY
export WORKFLOW_CORE_CONFIG=config/essai-test.toml
export WORKFLOW_CORE_STATE_DIR=~/.local/state/workflow-core-essai-test
mkdir -p "$WORKFLOW_CORE_STATE_DIR"
uv run python -m workflow_core --port 8449 2>&1 | tee -a "$WORKFLOW_CORE_STATE_DIR/socle.log"
```

Si le journal du socle de test dit que le contrat est cassé, le socle a déjà ouvert (ou commenté) l'issue du contrat
cassé dans `code-my-life/workflow-core` dès son démarrage : l'arrêter (Ctrl+C), puis noter dans cette issue qu'elle
vient de la machine de test, ou la fermer.

Le premier démarrage ne connaît pas encore le board `essai-test` : le contrat porte sur les autres lectures, et la
première passe du moteur crée le board (« board essai-test créé » au journal).

Créer une carte racine d'essai à la main, au nom de voie de la première station. Sa clé d'idempotence est
`essai:manuel-1`, puis `essai:manuel-2`, `essai:manuel-3` pour les cartes suivantes : jamais `essai:<numéro>`, la clé
de l'élément né d'une issue de `code-my-life/workflow-essai`, qui serait déjà prise.

```sh
hermes kanban --board essai-test create "essai:manuel-1 · Premier essai" --assignee essai-preparer \
  --idempotency-key essai:manuel-1 --body https://github.com/code-my-life/workflow-essai/issues/1
```

En moins d'une minute, la carte passe à `essai-revue` ; la vérifier, et vérifier qu'elle n'a jamais été prise :

```sh
hermes kanban --board essai-test show <carte>
```

À la passe suivante, la carte passe en `scheduled` : elle attend le propriétaire à `essai-revue`, et le journal donne
l'identifiant de l'attente. Injecter la réponse du propriétaire avec l'outil de la machine de test, qui écrit dans la
boîte des réponses du socle de test et imprime l'identifiant de la réponse :

```sh
uv run python scripts/inject_answer.py --state-dir ~/.local/state/workflow-core-essai-test --item essai:manuel-1 \
  --gesture valider
```

(`--item` est la clé d'idempotence de la carte racine ; `--note <texte>` ajoute une note au geste.) À la passe suivante, la carte passe à `essai-clore`, puis, une passe
plus loin, en `done`. Lire l'attente, puis la fin, avec `hermes kanban --board essai-test show <carte>` : le statut,
les événements `scheduled`, `unblocked` et `completed`, et le résultat. Une carte bloquée par un échec de commande se
lit de même, avec le motif de son événement `blocked`.

Lire le journal du socle de test : `tail -f ~/.local/state/workflow-core-essai-test/socle.log`. Chaque lancement de
commande y est écrit (« commande de preparer lancée ») ; une carte en attente ne fait lancer aucune commande tant
qu'aucune réponse n'est injectée. Arrêter le socle de test : Ctrl+C dans son terminal.

### Preuve de la naissance d'un élément (relecture de GitHub)

Le socle de test lancé comme ci-dessus (l'App dans le `.env`), ouvrir une issue de test dans le dépôt du workflow
d'essai, au nom du propriétaire :

```sh
gh issue create -R code-my-life/workflow-essai --label test --title "Essai de naissance" \
  --body "Issue de test de la relecture de GitHub"
```

Attendre au plus 10 minutes (la relecture suivante), puis lire le commentaire d'acceptation, signé par
`code-my-life-factory`, et la carte créée :

```sh
gh issue view <n> -R code-my-life/workflow-essai --comments
hermes kanban --board essai-test list
```

La liste montre une seule carte `essai:<n>`. Redémarrer le socle de test deux fois (Ctrl+C, puis la même commande) :
la liste n'a toujours qu'une carte. Ouvrir une seconde issue au titre `[refus] essai` (avec le label `test`) : elle
reçoit le commentaire de refus avec son motif, et aucune carte. Coller les liens des issues et la sortie de la liste
dans la pull request, puis fermer les issues de test :

```sh
gh issue close <n> -R code-my-life/workflow-essai
```

### Preuve des gestes (C9 de code-my-life/workflow-core#57)

Le socle de test lancé comme ci-dessus, et un élément d'essai né d'une issue au label `test` (voir la preuve
précédente), attendre qu'il soit en attente à `essai-revue` (`hermes kanban --board essai-test show <carte>` :
`scheduled`). Puis, au nom du propriétaire :

```sh
gh issue comment <n> -R code-my-life/workflow-essai --body "/essai refaire"
```

En moins de 11 minutes : la carte passe à `essai-preparer`, puis de nouveau en attente à `essai-revue`, et le
commentaire porte 👍 de `code-my-life-factory`. Redémarrer le socle de test : la carte reste en attente, et le même
commentaire n'est pas appliqué de nouveau. Poster ensuite `/essai valider` : la carte passe à `essai-clore` puis à
`done`, avec 👍. Poster enfin `/essai valider` sur l'issue, l'élément fini : 😕. Lire les réactions :

```sh
gh api repos/code-my-life/workflow-essai/issues/<n>/comments --jq '.[] | {html_url, body, reactions}'
```

Coller les liens des commentaires dans la pull request, puis fermer l'issue de test.
