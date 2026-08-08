#!/usr/bin/env bash
# Wake the transcode PC with a Wake-on-LAN magic packet.
#
# The packet is a layer-2 broadcast, so it does NOT travel over Tailscale — the
# laptop sends it on the local network on your behalf. That is why this works
# even when the request came from a phone on mobile data: the always-on laptop
# is the one holding the LAN.
#
# Uses only the Python standard library; nothing to install.
#
# Usage:  MIRU_PC_MAC=AA:BB:CC:DD:EE:FF ./scripts/wake-pc.sh
#         ./scripts/wake-pc.sh AA:BB:CC:DD:EE:FF
set -euo pipefail
cd "$(dirname "$0")/.."

set -a; [ -f .env ] && . ./.env; set +a

MAC="${1:-${MIRU_PC_MAC:-}}"
if [ -z "$MAC" ]; then
  echo "error: no MAC address. Set MIRU_PC_MAC in .env or pass one as an argument." >&2
  echo "  On the PC:  Get-NetAdapter | Select-Object Name, MacAddress, Status" >&2
  exit 1
fi

python3 - "$MAC" "${MIRU_PC_BROADCAST:-255.255.255.255}" <<'PY'
import re, socket, sys

mac, broadcast = sys.argv[1], sys.argv[2]
clean = re.sub(r"[^0-9a-fA-F]", "", mac)
if len(clean) != 12:
    sys.exit(f"error: {mac!r} is not a MAC address")

# A magic packet is 6 bytes of 0xFF followed by the target MAC repeated 16 times.
packet = b"\xff" * 6 + bytes.fromhex(clean) * 16

with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    # Ports 9 and 7 are both conventional; some NICs listen on only one.
    for port in (9, 7):
        s.sendto(packet, (broadcast, port))

print(f"magic packet sent to {mac} via {broadcast}:9,7")
PY
