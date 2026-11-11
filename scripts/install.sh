#!/usr/bin/env bash
# Install the workflow-core systemd user unit: copy it to ~/.config/systemd/user/ and reload systemd.
# It neither enables nor starts the service (the owner's task). Run again, it changes nothing.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target="$HOME/.config/systemd/user"
mkdir -p "$target"

changed=0
for unit in "$repo"/systemd/*.service; do
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
  echo "systemd rechargé. Le service n'est ni activé ni démarré : systemctl --user enable --now workflow-core.service"
else
  echo "systemd rechargé, rien n'a changé."
fi
