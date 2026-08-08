#!/usr/bin/env bash
# Set a known qBittorrent WebUI password, and configure it for Miru.
#
# Run this ON THE PC, in the Ubuntu WSL where qbittorrent-nox is installed.
#
# Why this exists: qbittorrent-nox generates a NEW temporary password every time
# it starts and prints it to stdout, so a password copied from an earlier run is
# already stale — and five failed attempts ban your IP for an hour. This writes a
# real password into the config instead, so there is nothing to race.
set -euo pipefail

PASS="${1:-}"
[ -n "$PASS" ] || { echo "usage: $0 <password-you-choose>" >&2; exit 1; }

CONF="$HOME/.config/qBittorrent/qBittorrent.conf"
SAVE_PATH="${2:-/mnt/incoming}"

echo "Stopping qbittorrent-nox (this also clears the IP ban, which is in memory)…"
pkill -x qbittorrent-nox 2>/dev/null || true
sleep 2

mkdir -p "$(dirname "$CONF")"
touch "$CONF"
cp "$CONF" "$CONF.bak.$(date +%s)"

HASH="$(python3 - "$PASS" <<'PY'
import base64, hashlib, os, sys
# qBittorrent stores: @ByteArray(<b64 salt>:<b64 PBKDF2-HMAC-SHA512>)
# 16-byte salt, 100000 iterations, 64-byte key.
salt = os.urandom(16)
key = hashlib.pbkdf2_hmac("sha512", sys.argv[1].encode(), salt, 100000, 64)
print(f"@ByteArray({base64.b64encode(salt).decode()}:{base64.b64encode(key).decode()})")
PY
)"

python3 - "$CONF" "$HASH" "$SAVE_PATH" <<'PY'
import configparser, sys
conf, hash_, save = sys.argv[1], sys.argv[2], sys.argv[3]

cp = configparser.RawConfigParser()
cp.optionxform = str          # qBittorrent keys are case-sensitive
cp.read(conf)
if not cp.has_section("Preferences"):
    cp.add_section("Preferences")
if not cp.has_section("BitTorrent"):
    cp.add_section("BitTorrent")

P = cp["Preferences"]
P["WebUI\\Username"] = "admin"
P["WebUI\\Password_PBKDF2"] = f'"{hash_}"'
P["WebUI\\Port"] = "8080"
# Listen on every interface: the laptop reaches this over Tailscale, not on
# localhost, so the default localhost-only bind is unreachable from Miru.
P["WebUI\\Address"] = "*"
# Five failures banning you for an hour is the thing that just happened.
P["WebUI\\MaxAuthenticationFailCount"] = "100"
P["WebUI\\BanDuration"] = "60"
# The API is a different origin from the WebUI's own host; without these two
# qBittorrent rejects Miru's requests as cross-site.
P["WebUI\\CSRFProtection"] = "false"
P["WebUI\\HostHeaderValidation"] = "false"

P["Downloads\\SavePath"] = save
# One directory only. A separate "incomplete" path means Miru cannot find the
# file while it is still growing, which is exactly what watch-while-downloading
# needs to read.
P["Downloads\\TempPathEnabled"] = "false"

B = cp["BitTorrent"]
# Miru decides what runs and in what order; a queue silently defers a Watch Now.
B["Session\\QueueingSystemEnabled"] = "false"
B["Session\\DefaultSavePath"] = save
B["Session\\TempPathEnabled"] = "false"

with open(conf, "w") as fh:
    cp.write(fh, space_around_delimiters=False)
print(f"wrote {conf}")
PY

mkdir -p "$SAVE_PATH" 2>/dev/null || true

echo "Starting qbittorrent-nox…"
nohup qbittorrent-nox -d >/dev/null 2>&1 &
sleep 4

if curl -s -m 5 -o /dev/null "http://127.0.0.1:8080"; then
  echo
  echo "Up. Log in as  admin  with the password you just set."
  echo "From the laptop:  http://100.67.44.13:8080"
else
  echo "qbittorrent-nox did not answer on 8080 — check: qbittorrent-nox (foreground) for the error"
fi
