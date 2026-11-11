# workflow-core

Le socle commun des workflows de code-my-life : il observe les boards d'Hermès et les sert à factory-live.

## Démarrer

```bash
uv sync --locked
uv run pytest
```

Le service sert en lecture, sur `127.0.0.1:8447`, les cartes des workflows déclarés dans `config/workflows.toml`.
Toute route exige l'en-tête `Authorization: Bearer <secret>`, le secret étant lu dans `WORKFLOW_CORE_SECRET` :

```bash
WORKFLOW_CORE_SECRET=<secret> uv run python -m workflow_core
```

`scripts/install.sh` installe l'unité systemd utilisateur `workflow-core.service`, qui lit le secret dans
`~/.config/workflow-core/.env`, sans l'activer. Les décisions d'architecture sont dans `docs/decisions/`.

## Contribuer

Ouvrez une issue avec l'un des modèles : elle passe par la software factory de Code My Life (une carte, une pull
request, des contrôles automatiques, puis l'approbation du propriétaire).

## Licence

MIT, voir `LICENSE`.
