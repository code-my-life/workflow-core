#!/usr/bin/env bash
# Install the workflow-core systemd user units (the service, the watchdog and its timer): copy them to
# ~/.config/systemd/user/ and reload systemd. It neither enables nor starts any of them (the owner's task). Run again,
# it changes nothing.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target="$HOME/.config/systemd/user"
mkdir -p "$target"

changed=0
for unit in "$repo"/systemd/*.service "$repo"/systemd/*.timer; do
  name="$(basename "$unit")"
  if cmp -s "$unit" "$target/$name"; then
    echo "$name : déjà à jour"
  else
    cp "$unit" "$target/$name"
    echo "$name : copié dans $target"
    changed=1
  fi
done

systemctl --user daemon-reload
if [ "$changed" -eq 1 ]; then
  echo "systemd rechargé. Rien n'est activé ni démarré : systemctl --user enable --now workflow-core.service"
  echo "puis, pour le chien de garde : systemctl --user enable --now workflow-core-watchdog.timer"
else
  echo "systemd rechargé, rien n'a changé."
fi

# The contract test against the installed runtime (ADR 0001). A failure does not undo the install: the service will
# start in observer mode. It is said on the last line, and the install exits with 1.
echo "Test de contrat : uv run pytest tests/contract"
if (cd "$repo" && uv run pytest tests/contract -rs); then
  echo "Contrat tenu : le service démarrera en mode normal."
else
  echo "Contrat cassé : l'unité est installée, mais le service démarrera en mode observateur (voir ci-dessus)."
  exit 1
fi
