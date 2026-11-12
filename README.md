# workflow-core

Le socle commun des workflows de code-my-life : il observe les boards d'Hermès et les sert à factory-live.

## Démarrer

```bash
uv sync --locked
uv run pytest
```

Le service observe, sans rien écrire, les boards des workflows déclarés dans `config/workflows.toml` (`factory` et
`studio`) et les sert sur `127.0.0.1:8447`. Toute route exige l'en-tête `Authorization: Bearer <secret>`, le secret
étant lu dans `WORKFLOW_CORE_SECRET` :

```bash
WORKFLOW_CORE_SECRET=<secret> uv run python -m workflow_core
```

| Route | Ce qu'elle sert |
|---|---|
| `GET /healthz` | `{"status": "ok"}` quand le service répond |
| `GET /api/workflows` | les workflows déclarés, chacun avec son board, la validité de sa fiche et ses gestes déclarés (`gestures` : `comment`, un commentaire `/<clé> <geste>` sur l'issue d'un élément, ou `issue`, tables `[[workflow.gestures]]` de `config/workflows.toml` ; le socle les publie, il n'en poste aucun) (`schema/workflows.schema.json`) |
| `GET /api/workflows/<clé>/sheet` | la fiche du workflow, jugée à chaque requête (`schema/sheet-response.schema.json`, la fiche seule : `schema/workflow-sheet.schema.json`) |
| `GET /api/workflows/<clé>/items` | les éléments du workflow, chacun avec l'état complet de sa carte (clé d'idempotence, dossier de travail, run courant et dernier battement, surcharges, skills), ses exécutions et leur `metadata`, ses liens, ses commentaires et ses événements, et sa `station` : la clé de la station de la fiche où il se trouve, ou `null` (une carte finie est à la station de fin ; sinon la station dont le nom de voie `<clé du workflow>-<clé de station>` ou la liste `agents` nomme son agent ; règle dans `workflow_core/stations.py`) (`schema/items.schema.json`) |
| `GET /api/workflows/<clé>/items/<carte>/diagnosis` | le diagnostic complet de la carte et son déclencheur (`trigger`), calculés à la demande (`schema/diagnosis.schema.json`) ; `null` et `null` pour une carte qui n'attend pas le propriétaire |
| `GET /api/workflows/<clé>/diagnoses` | les cartes du board qui attendent le propriétaire (bloquées, abandonnées, en triage, retenues), chacune avec son déclencheur et son diagnostic, rangées par heure |
| `GET /api/workflows/<clé>/items/<carte>/run-verdicts` | le verdict de chaque run en échec de la carte, terminée ou non, du plus récent au plus ancien (`schema/run-verdicts.schema.json`) |
| `GET /api/workflows/<clé>/live` | l'état direct de chaque agent qui tient une carte en cours : état (`thinking`, `tool`, `cli`, `blocked`), action en cours, modèle (servi, lu dans le journal de l'agent, source `live` ; sinon celui de sa session, source `session`), modèle de la lane, dernier battement (`schema/live.schema.json`) |
| `GET /api/workflows/<clé>/activity?limit=<n>` | les derniers faits du board, du plus ancien au plus récent, 200 au plus : appels d'outils et leur issue, appels au modèle, événements des cartes, refus d'une garde (`schema/activity.schema.json`) |
| `GET /api/workflows/<clé>/usage?days=<n>` | l'usage et les coûts sur `n` jours, 90 au plus : par modèle (appels, jetons, coût réel sinon estimé, appels refusés pour quota et en erreur), par agent et par jour, et en détail une ligne par jour, modèle et agent (`rows`, dont les sommes redonnent les trois agrégats) (`schema/usage.schema.json`) |
| `GET /api/workflows/<clé>/data?subject=<sujet>&id=<id>` | les données que le workflow déclare pour un sujet (`pr` : `<dépôt>#<n>`, `card` : la carte, ou `<carte>@<commit>` pour ce qu'elle a par commit, comme les preuves de la gate, `run`, `agent`), lues à la demande aux chemins déclarés dans ses tables `[[workflow.data]]` de `config/workflows.toml`, en valeurs génériques (texte, lien, tableau, statut) ; une donnée absente n'a aucune valeur, une donnée illisible un statut `error` ; une colonne de tableau peut lire une clé imbriquée (`base.sha`) ; aucune valeur ne porte de session Neko ni de mot de passe (`schema/declared-data.schema.json`) |
| `GET /api/workflows/<clé>/agents?days=<n>` | les agents du workflow (ceux de son manifeste, sinon ceux des runs de son board) et leur configuration en service, sans secret : modèle et repli, effort, outils, plugins avec leur plus longue commande et leurs dépassements du délai de hook sur `n` jours (30 par défaut, 90 au plus), noms des serveurs MCP, limites avec les valeurs par défaut, les réglages du kanban, et les gardes à risque (`guard_risks` : un plugin branché sur un hook dont la plus longue commande atteint le délai effectif de ce hook, par plugin et par délai, avec ses agents) (`schema/agents.schema.json`) |
| `GET /api/workflows/<clé>/interventions` | les interventions ouvertes du board (une carte qui attend le propriétaire, des six sortes du diagnostic), rangées par heure d'ouverture, chacune avec sa gravité, sa notification (`push`), son titre et sa question (un code du catalogue de factory-live ou la citation de la raison de l'agent) et ses actions (`gesture`, un geste déclaré ; `copy`, une commande à copier) ; les liens vers l'issue et la PR restent à factory-live (`schema/interventions.schema.json`) |
| `GET /api/machine` | la version d'Hermès, le verdict du test de contrat, le mode (`normal` ou `observer`), l'heure de la dernière lecture de chaque board, et `checks`, l'état de la machine : une tuile par service (gateway, pause, tâches planifiées, llama.cpp, disque, et les contrôles déclarés par des tables `[[check]]` de `config/workflows.toml`), avec son statut `ok`, `degraded`, `down` ou `unknown` (`schema/machine.schema.json`) |
| `GET /api/events` | le flux SSE des changements, repris avec `Last-Event-ID` ou `?cursor=` (`schema/event.schema.json`, un événement : `changed` porte l'élément complet, `reset` ouvre une époque, `live` l'état direct d'un agent, `activity` un fait nouveau, `machine` la liste des contrôles quand un statut change, `intervention_opened` une intervention ouverte, `intervention_closed` l'identifiant d'une intervention fermée) |

Les réponses portent `schema_version` et, quand le flux tourne, le `cursor` courant, d'où reprendre le flux. Une erreur
a la forme `{"error", "detail"}` (`schema/error.schema.json`).

Au démarrage, le service vérifie que la version d'Hermès installée a encore la forme qu'il lit (`uv run pytest
tests/contract`) ; sinon il reste en mode observateur et l'écrit dans journald. `scripts/install.sh` installe les
unités systemd utilisateur (le service, relancé s'il tombe, et le chien de garde, qui écrit dans journald une panne de
`/healthz` de 5 minutes ou plus), sans les activer. Le secret est lu dans `~/.config/workflow-core/.env`. Les
décisions d'architecture sont dans `docs/decisions/`.

## Contribuer

Ouvrez une issue avec l'un des modèles : elle passe par la software factory de Code My Life (une carte, une pull
request, des contrôles automatiques, puis l'approbation du propriétaire).

## Licence

MIT, voir `LICENSE`.
