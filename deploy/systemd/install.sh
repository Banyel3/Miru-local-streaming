#!/usr/bin/env bash
# Make Miru survive a reboot on the laptop.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$HOME/.config/systemd/user"
cp "$HERE"/miru-api.service "$HERE"/miru-web.service "$HOME/.config/systemd/user/"

# Without lingering, user units only run while someone is logged in — which is
# exactly not the case for a server that is meant to be reachable from a phone.
loginctl enable-linger "$USER"

systemctl --user daemon-reload
systemctl --user enable --now miru-api.service miru-web.service

echo
systemctl --user --no-pager status miru-api.service miru-web.service | head -20
echo
echo "Logs:    journalctl --user -u miru-api -f"
echo "Restart: systemctl --user restart miru-api"
