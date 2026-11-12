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
| `GET /api/workflows` | les workflows déclarés, chacun avec son board et la validité de sa fiche |
| `GET /api/workflows/<clé>/sheet` | la fiche du workflow, jugée à chaque requête (`schema/workflow-sheet.schema.json`) |
| `GET /api/workflows/<clé>/items` | les éléments du workflow, chacun avec ses exécutions, ses liens et ses commentaires |
| `GET /api/workflows/<clé>/items/<carte>/diagnosis` | le diagnostic complet de la carte et son déclencheur (`trigger`), calculés à la demande (`schema/diagnosis.schema.json`) ; `null` et `null` pour une carte qui n'attend pas le propriétaire |
| `GET /api/workflows/<clé>/diagnoses` | les cartes du board qui attendent le propriétaire (bloquées, abandonnées, en triage, retenues), chacune avec son déclencheur et son diagnostic, rangées par heure |
| `GET /api/machine` | la version d'Hermès, le verdict du test de contrat, le mode (`normal` ou `observer`), l'heure de la dernière lecture de chaque board |
| `GET /api/events` | le flux SSE des changements, repris avec `Last-Event-ID` ou `?cursor=` |

Les réponses portent `schema_version` et, quand le flux tourne, le `cursor` courant, d'où reprendre le flux. Une erreur
a la forme `{"error", "detail"}`.

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
