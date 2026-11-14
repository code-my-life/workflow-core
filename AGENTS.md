# workflow-core

Le socle commun des workflows de code-my-life (la ligne du code, `factory`, et la ligne vidéo, `studio`). Il lit
les boards du kanban d'Hermès, juge la fiche de chaque workflow, fait avancer par son moteur les workflows branchés par
manifeste (il écrit dans Hermès par la seule ligne de commande `hermes kanban`), relit GitHub (issues et gestes du
propriétaire) et y écrit au nom de son App (décisions d'intake, alertes, réactions, issues de panne et de contrat
cassé), et sert le tout à factory-live par une API locale en lecture seule.
Il tourne sur la machine de la factory, en service utilisateur systemd lié à 127.0.0.1. Ses décisions d'architecture
sont dans `docs/decisions/`, une par fichier (ADR 0001 à 0019).

## Commandes

| Quoi | Commande |
|---|---|
| Installer | `uv sync --locked` |
| Tous les tests | `uv run pytest` |
| Un seul fichier de test | `uv run pytest tests/test_runtime_confined.py` |
| Types | aucun pour l'instant |
| Build | `uv build` |
| Démarrer | `WORKFLOW_CORE_SECRET=<secret> uv run python -m workflow_core` (127.0.0.1:8447) ; service et chien de garde : `scripts/install.sh`, puis `systemctl --user enable --now workflow-core.service workflow-core-watchdog.timer` |
| Bout en bout | aucun |
| Réponse du propriétaire, machine de test | `uv run python scripts/inject_answer.py --state-dir <dossier d'état> --item <clé>:<id> --gesture <geste>` (écrit dans la boîte `answers` d'`engine.db` ; voir `docs/RUNBOOK.md`, « La machine de test ») |
| Lint et format | `uv run ruff check` et `uv run ruff format --check` |
| Hermès confiné | `python3 scripts/check_runtime_confined.py` |
| Jeton de l'App GitHub (sans le service) | `uv run python -m workflow_core.adapters.github token` ou `check` (voir `docs/RUNBOOK.md`) |
| Issue de panne, à la main (sans le service) | `.venv/bin/python -m workflow_core.notify.outage` (lancée par le chien de garde chaque minute) |
| Test de contrat (Hermès installé) | `uv run pytest tests/contract -rs` (aussi lancé par `uv run pytest`) : la lecture des boards est sautée sans base de board ; le cycle d'écriture sur un board jetable `contrat-<heure Unix>` tourne dès que `hermes` est sur le PATH, sauté seulement sans lui, comme en CI |
| JSON Schema de la fiche | `uv run python scripts/write_sheet_schema.py` (après un changement de la forme) |
| JSON Schema du manifeste | `uv run python scripts/write_manifest_schema.py` (après un changement de la forme du manifeste : réécrit `schema/workflow-manifest.schema.json`) |
| JSON Schema des réponses de l'API | `uv run python scripts/write_api_schemas.py` (après un changement d'une réponse : réécrit `workflows`, `sheet-response`, `items`, `machine`, `event`, `error`, `live`, `activity`, `usage`, `declared-data`, `interventions` et `agents` `.schema.json` dans `schema/`) |
| JSON Schema du diagnostic | `uv run python scripts/write_diagnosis_schema.py` (après un changement du contrat `Diagnosis` : réécrit `schema/diagnosis.schema.json` et `schema/run-verdicts.schema.json`) |

## Conventions

- Issues, branches et pull requests suivent les types de la software factory : un modèle d'issue de l'organisation par
  type, une branche `<préfixe>/<n° de l'issue>-<sujet>`, et le workflow « Conventions », qui juge chaque pull request.
- Hermès n'est nommé que dans `workflow_core/adapters/hermes/` (ADR 0001) : la CI refuse toute mention ailleurs dans
  le paquet. Les docs, les ADR, les tests de contrat et les profils d'agent peuvent le nommer.
- Python 3.12, uv 0.12.3, versions figées dans `pyproject.toml` et `uv.lock` (Pydantic 2.14.1, FastAPI, uvicorn).
- Aucun secret dans le dépôt, qui est public (ADR 0002) : jetons et clés restent dans le `.env` de la machine.
- Les commentaires du code sont en anglais. Aucun nom de personne dans un commentaire ou un titre de test.
- Les décisions vivent dans `docs/decisions/`, une par fichier.

## Échecs connus

- Deux `uv run pytest` lancés en même temps sur une machine où `hermes` est installé : le cycle d'écriture de
  `tests/contract` de l'un supprime au départ le board `contrat-*` de l'autre, qui échoue. Un seul lancement à la fois.
- `tests/test_events.py::test_an_intervention_opening_and_closing_go_on_the_stream_once` lit le flux par fenêtres
  fixes de 1,5 s : sous forte charge, une sonde en retard peut le faire échouer une fois. Le relancer.

## Interdits

- Affaiblir ou retirer une assertion, allonger un timeout.
- Pousser sur main, forcer un push, contourner un hook git.
- Nommer Hermès hors de `workflow_core/adapters/hermes/`, écrire dans une base d'Hermès hors de l'adaptateur.

## Chemins sensibles

- `.github/workflows/ci.yml` et `scripts/check_runtime_confined.py` : les contrôles du dépôt.
- `pyproject.toml` et `uv.lock` : les versions figées de la Stack.
- `docs/decisions/` : les décisions d'architecture.
- `docs/RUNBOOK.md` : l'exploitation du socle sur la machine (l'App GitHub et ses permissions, le notificateur, les
  issues de panne et de contrat cassé, le moteur, la relecture de GitHub, les gestes, la machine de test).
- `config/workflows.toml` (production) et `config/essai-test.toml` (machine de test, choisie par
  `WORKFLOW_CORE_CONFIG`, avec `WORKFLOW_CORE_STATE_DIR` pour son dossier d'état) : les workflows branchés et leurs
  boards ; une entrée sans `issue_label` (production) écarte les issues au label `test`, et `issue_label = "test"`
  limite la machine de test à ces issues.
- `WORKFLOW_CORE_OWNER_LOGIN`, dans le `.env` de la machine : le seul compte dont les issues et les gestes sont pris.
- `scripts/inject_answer.py` : écrit une réponse du propriétaire dans `engine.db`, pour la machine de test seulement.
- `systemd/` : les unités du service et du chien de garde, recopiées par `scripts/install.sh`.
