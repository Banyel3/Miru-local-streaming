#!/usr/bin/env python3
"""Rotate the qBittorrent WebUI password. Run from the repo root, PC awake.

The deployed password has been the literal string from the setup snippet since
day one — docs/plans/STATUS.md has carried the line for a while. This logs in
with the current credentials from .env, sets a random 24-character password via
the WebUI API, rewrites MIRU_QBITTORRENT_PASSWORD in .env, and prints the new
password ONCE for your own records (it is in .env afterwards; nowhere else).

Refuses to run if the PC is unreachable, and changes nothing until the new
password is proven to work. Restart miru-api afterwards so it picks up the env.
"""

from __future__ import annotations

import secrets
import string
import sys
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ENV = Path(__file__).resolve().parent.parent / ".env"


def read_env() -> dict[str, str]:
    conf = {}
    for line in ENV.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            conf[k.strip()] = v.strip()
    return conf


def main() -> int:
    conf = read_env()
    url = conf.get("MIRU_QBITTORRENT_URL", "").rstrip("/")
    user = conf.get("MIRU_QBITTORRENT_USER", "")
    old = conf.get("MIRU_QBITTORRENT_PASSWORD", "")
    if not url or not user:
        print("MIRU_QBITTORRENT_URL / _USER missing from .env", file=sys.stderr)
        return 1

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def post(path: str, data: dict[str, str]) -> str:
        req = urllib.request.Request(
            f"{url}/api/v2{path}",
            data=urllib.parse.urlencode(data).encode(),
            headers={"Referer": url},
        )
        with opener.open(req, timeout=6) as res:
            return res.read().decode()

    try:
        if post("/auth/login", {"username": user, "password": old}) != "Ok.":
            print("login refused — is the current password in .env right?", file=sys.stderr)
            return 1
    except OSError as exc:
        print(f"PC unreachable ({exc}); run this when it is awake.", file=sys.stderr)
        return 1

    alphabet = string.ascii_letters + string.digits
    new = "".join(secrets.choice(alphabet) for _ in range(24))
    post("/app/setPreferences", {"json": f'{{"web_ui_password": "{new}"}}'})

    # Trust nothing until the new password actually works.
    fresh = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    req = urllib.request.Request(
        f"{url}/api/v2/auth/login",
        data=urllib.parse.urlencode({"username": user, "password": new}).encode(),
        headers={"Referer": url},
    )
    with fresh.open(req, timeout=6) as res:
        if res.read().decode() != "Ok.":
            print("VERIFY FAILED — .env NOT changed; check qBittorrent manually.", file=sys.stderr)
            return 1

    ENV.write_text(
        ENV.read_text().replace(
            f"MIRU_QBITTORRENT_PASSWORD={old}", f"MIRU_QBITTORRENT_PASSWORD={new}"
        )
    )
    print(f"rotated. New password (also in .env): {new}")
    print("now: systemctl --user restart miru-api")
    return 0


if __name__ == "__main__":
    sys.exit(main())
