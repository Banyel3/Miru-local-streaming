# Setting up the PC

Runbook for the second machine: the NVENC worker and `movies-downloader`.

**Do these in order.** Each step ends with a check, and every step assumes the
previous check passed. The ordering is deliberate: connectivity is proven before
anything is installed, so a failure at step 5 is a failure of step 5 and not a
network problem you have been carrying since step 1.

Throughout: **laptop** is the always-on Linux server running Miru; **PC** is the
Windows 11 + WSL2 machine with the RTX 5060.

---

## Step 0 — What must already be true

On the PC:

- Tailscale installed and signed into the same tailnet
- `%UserProfile%\.wslconfig` contains mirrored networking, and WSL has been
  restarted since:
  ```ini
  [wsl2]
  networkingMode=mirrored
  ```

### You are in a real Linux distro, not Docker Desktop's

If Docker Desktop is installed it registers its own WSL distributions, and
typing bare `wsl` can drop you into one. That VM is a stripped-down BusyBox
image: no `apt`, no `sudo`, no `python3`, and Docker Desktop rewrites it on
update, so anything installed there disappears.

```powershell
wsl -l -v
```

If the only entries are `docker-desktop` / `docker-desktop-data`, there is no
distro to work in yet:

```powershell
wsl --install -d Ubuntu
wsl --set-default Ubuntu
wsl --shutdown
```

If Ubuntu is listed but not marked default, just:

```powershell
wsl --set-default Ubuntu
```

Confirm before continuing:

```bash
wsl
whoami                            # your user, not root
cat /etc/os-release | head -2     # Ubuntu
ls /mnt/c                         # NOT /mnt/host/c
```

`/mnt/host/c` in the path, a `-sh:` prefix on errors, or a `#` prompt as root
all mean you are still in Docker Desktop's VM.

### Addresses: use the tailnet IPs, not MagicDNS names

```bash
# on the laptop
tailscale status
```

This document uses the addresses from that output:

| | Tailnet IP |
|---|---|
| laptop (`ban-1`) | `100.71.150.101` |
| PC (`ban-pc`) | `100.67.44.13` |

**MagicDNS names are deliberately not used.** On this laptop `/etc/resolv.conf`
contains only `1.1.1.1`, so Tailscale is not managing system DNS and neither
`ban-pc` nor `ban-pc.tail88f195.ts.net` resolves for ordinary programs. That
`tailscale ping ban-pc` succeeds is misleading: Tailscale resolves that name
internally, while curl, ffmpeg and uvicorn all go through system DNS and fail.

Tailnet IPs are bound to machine identity, so they are exactly as stable as a
MagicDNS name and have no DNS dependency at all. If you would rather use names,
enable `sudo tailscale set --accept-dns=true` on both machines first and confirm
`getent hosts ban-pc` returns an address before relying on it.

---

## Step 1 — Prove the laptop can reach a port inside WSL2

This is the assumption the entire design rests on, and it is the one most likely
to be wrong. Test it with a throwaway listener before installing anything.

> **Run it from an empty directory.** `http.server` serves the current working
> directory to anyone who can reach the port. Started from your home or from
> `/mnt/c/Users/<you>`, it publishes `.ssh/`, `.gitconfig`, `.config/` and every
> other credential file you own to the whole tailnet, unauthenticated.

```bash
# on the PC, inside WSL2
mkdir -p /tmp/miru-nettest && cd /tmp/miru-nettest
echo "reachable" > index.html
python3 -m http.server 8010
```

```bash
# on the laptop
curl -m 5 http://100.67.44.13:8010/
```

Expect the word `reachable`. Stop the server with Ctrl-C as soon as it works —
it has no authentication and no reason to keep running.

**If it hangs or refuses**, stop here — nothing
below will work. Check in this order: is Tailscale up on the PC
(`tailscale status` should list it as online, not offline); did WSL actually
restart after `.wslconfig` was edited (`wsl --shutdown` from PowerShell, then
reopen); is Windows Firewall blocking inbound 8001.

Do not continue until this returns something. Stop the throwaway server when it
does.

### If the listener says `Address already in use`

That error is good news about the thing you were testing: with mirrored
networking WSL shares the Windows network stack, so a port occupied by a Windows
process is occupied inside WSL too. Seeing the conflict proves mirroring is
active.

Find the occupant from PowerShell:

```powershell
netstat -ano | findstr :8001
tasklist /FI "PID eq <the-pid-from-above>"
```

On this setup port 8001 is held by **VS Code Server** (the remote-SSH backend),
which you do not want to kill. The worker uses **8010** throughout this document
for that reason. Any free high port works; just keep it consistent between the
worker's `--port` and `MIRU_TRANSCODE_WORKER`.

---

## Step 2 — ffmpeg with working NVENC

```bash
# on the PC, inside WSL2
sudo apt update && sudo apt install -y ffmpeg python3-venv python3-pip git
```

Listing the encoder is not the same as it working — a build can advertise
`h264_nvenc` and still fail at runtime when the driver is wrong. Test a real
encode:

```bash
ffmpeg -hide_banner -f lavfi -i testsrc=size=1280x720:rate=30 -t 3 \
       -c:v h264_nvenc -f null -
```

**Expect** a clean exit. `Cannot load libnvidia-encode.so.1` means the Windows
NVIDIA driver is too old — update it from the Windows side, then `wsl --shutdown`
and retry. Do not install a Linux NVIDIA driver inside WSL; it breaks the
passthrough.

`nvidia-smi` should also list the 5060 from inside WSL.

---

## Step 3 — The transcode worker

```bash
# on the PC, inside WSL2
git clone <your-miru-repo> ~/miru && cd ~/miru
python3 -m venv .venv && .venv/bin/pip install -e "apps/worker"
```

Configure it. The allowlist is not optional: the worker fetches whatever URL it
is handed, so without it anything that can reach your tailnet can make it fetch
arbitrary addresses.

```bash
cat > ~/miru/apps/worker/.env <<'EOF'
# Only Miru may hand this worker source URLs.
WORKER_ALLOWED_SOURCE_PREFIXES=http://100.71.150.101:8000/
# hls.js fetches manifests and segments by XHR, so the browser origin needs CORS.
WORKER_WEB_ORIGIN=http://100.71.150.101:3001
# Local disk, not tmpfs and not the NFS share: ~5 GB per two-hour film.
WORKER_CACHE_DIR=/var/tmp/miru-hls
WORKER_MAX_SESSIONS=4
EOF
```

Run it:

```bash
cd ~/miru/apps/worker && ../../.venv/bin/uvicorn miru_worker.main:app --host 0.0.0.0 --port 8010
```

**Check, from the laptop:**

```bash
curl -m 5 http://100.67.44.13:8010/health
```

**Expect** `"encoder":"h264_nvenc"`. If it says `libx264`, the worker's own NVENC
probe failed even though step 2 passed. The worker logs ffmpeg's error when that
happens — read it before guessing. To force the encoder regardless:

```bash
echo "WORKER_ENCODER=h264_nvenc" >> ~/miru/apps/worker/.env
```

Setting it skips detection entirely, which is the right move on a machine you
know has a working GPU.

---

## Step 4 — Point Miru at the worker

```bash
# on the laptop, in .env
MIRU_TRANSCODE_WORKER=http://100.67.44.13:8010
# How the WORKER reaches this API. Must not be localhost — that would point the
# PC at itself.
MIRU_PUBLIC_API_URL=http://100.71.150.101:8000
```

Restart the API, then:

```bash
curl -s localhost:8000/api/library | python3 -m json.tool | grep -A1 availability
```

**Expect** files needing an encoder to report `"gpu-ready"`. Kill the worker and
re-check: they should flip to `"unavailable"` with *"Needs the PC to transcode —
it's offline"* within about ten seconds (the health cache TTL), while `direct`
files keep playing. That flip is the whole availability model working.

---

## Step 5 — Shared storage, so downloads land on the laptop

The 932 GB drive is on the laptop; the downloader runs on the PC. Mount it so
completed downloads are written straight into the library instead of filling the
PC's disk and being copied afterwards.

```bash
# on the laptop
sudo apt install -y nfs-kernel-server
echo '/mnt/storage <PC-TAILNET-IP>(rw,sync,no_subtree_check,all_squash,anonuid=1000,anongid=1000)' \
  | sudo tee -a /etc/exports
sudo exportfs -ra
```

```bash
# on the PC, inside WSL2
sudo apt install -y nfs-common
sudo mkdir -p /mnt/storage
sudo mount -t nfs 100.71.150.101:/mnt/storage /mnt/storage
echo "100.71.150.101:/mnt/storage /mnt/storage nfs defaults,_netdev 0 0" | sudo tee -a /etc/fstab
```

**Check** the mount is writable from the PC and the file appears on the laptop:

```bash
# on the PC
touch /mnt/storage/.write-test && echo ok
# on the laptop
ls -la /mnt/storage/.write-test && rm /mnt/storage/.write-test
```

---

## Step 6 — Prowlarr (search)

**Not `movies-downloader`.** That project was tried and dropped: it scrapes HTML,
and its scrapers have rotted. The Pirate Bay's page no longer emits a `<tbody>`
element while the scraper selects `#searchResult > tbody > tr` — browsers insert
`tbody` when parsing tables, cheerio does not, so the selector matches nothing
and search returns empty with no error at all. 1337x separately 404s on its
search path. Fixing it means forking and then maintaining three HTML parsers
against sites that change markup deliberately.

Prowlarr does exactly that job as its reason for existing: 500+ indexers behind
one API, definitions maintained upstream. It also carries **nyaa.si**, which is
the source that matters for anime and which YTS (movies only) cannot cover.
Jackett solves the same problem but is no longer actively developed.

Install it **natively in Ubuntu WSL**, not via Docker. With Docker Desktop on
Windows, `docker run` from Ubuntu hands the container to Docker Desktop's own
VM — the same distro that has to be avoided in step 0 — which would put Prowlarr
in a third execution environment alongside Ubuntu WSL and Windows. Prowlarr
ships self-contained binaries, so there is no .NET runtime to install and no
reason to reach for a container here.

```bash
# on the PC, inside WSL2 — check the port first, this environment has form
python3 -c "import socket;s=socket.socket();s.bind(('0.0.0.0',9696));print('9696 free')"

# .NET needs libicu for globalization; without it Prowlarr aborts on startup
# with "Couldn't find a valid ICU package installed on the system".
sudo apt update && sudo apt install -y libicu-dev

cd /tmp
wget --content-disposition 'https://prowlarr.servarr.com/v1/update/master/updatefile?os=linux&runtime=netcore&arch=x64'
tar -xzf Prowlarr*.linux*.tar.gz
sudo mv Prowlarr /opt/ && sudo chown -R $USER:$USER /opt/Prowlarr

/opt/Prowlarr/Prowlarr -nobrowser -data=$HOME/.config/prowlarr
```

Open `http://localhost:9696` on the PC and:

1. Set authentication (Prowlarr requires it; **Forms** with a password).
2. **Indexers → Add Indexer** — add `nyaa.si` for anime, plus The Pirate Bay,
   1337x and YTS for film and TV. Test each one; a red result means that
   indexer is down or blocked from your network, not that Prowlarr is broken.
3. **Settings → General → API Key** — copy it. Miru authenticates with it.

**Check from the laptop:**

```bash
curl -s -m 15 -H "X-Api-Key: YOUR_KEY" \
  "http://100.67.44.13:9696/api/v1/indexer" | head -c 300

# a real search across every configured indexer
curl -s -m 45 -H "X-Api-Key: YOUR_KEY" \
  "http://100.67.44.13:9696/api/v1/search?query=sintel&limit=5" | head -c 400
```

That second call is the one Miru's acquisition provider will use. It returns
title, size, seeders, indexer and a magnet or `.torrent` URL per result — which
is everything aria2 needs to start a download.

### Removing movies-downloader

```bash
# stop the node process, then
rm -rf ~/movies-downloader
```

---

## Step 6b — aria2, the actual downloader

```bash
sudo apt install -y aria2
mkdir -p ~/.aria2
openssl rand -hex 24 > ~/.aria2/rpc-secret && cat ~/.aria2/rpc-secret

cat > ~/.aria2/aria2.conf <<EOF
dir=/mnt/incoming
continue=true
enable-rpc=true
rpc-listen-all=true
rpc-listen-port=6800
rpc-secret=PASTE_THE_SECRET_HERE
seed-time=0
# NFS does not handle pre-allocation well; without this large torrents stall
# on start.
file-allocation=none
EOF

aria2c --conf-path="$HOME/.aria2/aria2.conf"
```

Two settings are load-bearing. `continue=true` gives resume, so a 12 GB file
survives a reboot instead of restarting. `seed-time=0` stops seeding once the
download completes — change it if you want to keep seeding, but know that you
are choosing to.

aria2 writes a `.aria2` control file alongside each in-progress download and
deletes it on completion. The library mover already treats `.aria2` as
"unfinished no matter how still it looks", so an interrupted download can never
be promoted.

**Check from the laptop:**

```bash
curl -s -m 5 -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"aria2.getVersion","params":["token:YOUR_SECRET"]}' \
  http://100.67.44.13:6800/jsonrpc
```

---

## Step 7 — End to end

With the worker and downloader running on the PC and Miru on the laptop:

1. Open Miru, play a `direct` file — should be unchanged, served by the laptop
   alone.
2. Play a file that reports `transcode_full`. The strategy chip should read
   **Transcoding**, and the settings menu should offer **Quality** with the
   ladder capped by the source.
3. Watch the PC: `nvidia-smi` should show an ffmpeg process using the encoder.
4. Stop the worker mid-library-browse. Within ~10 s those files should show
   *"Needs the PC to transcode — it's offline"* while everything else keeps
   playing.

---

## Making it survive a reboot

Task Scheduler starts WSL2 at logon (`SETUP.md` §5). Have that startup script
mount the NFS share, then start both services:

```bash
#!/usr/bin/env bash
mountpoint -q /mnt/storage || mount -t nfs 100.71.150.101:/mnt/storage /mnt/storage
cd /home/<user>/miru/apps/worker && /home/<user>/miru/.venv/bin/uvicorn \
  miru_worker.main:app --host 0.0.0.0 --port 8010 &
cd /home/<user>/movies-downloader && HOST=127.0.0.1 PORT=5000 npm start &
```

Sleep should stay **disabled** on the PC (`SETUP.md` §6). Wake-on-LAN was
considered and dropped, so a sleeping PC means transcoded files are unavailable
until someone wakes it — everything the laptop can serve keeps working.

---

## When something breaks

| Symptom | Where to look |
|---|---|
| `apt`, `sudo` or `python3` "not found" in WSL | You are in Docker Desktop's VM, not Ubuntu. Step 0 |
| Prowlarr aborts: "Couldn't find a valid ICU package" | `sudo apt install -y libicu-dev`. Do **not** use the Invariant flag — it breaks culture-aware matching of Japanese release titles |
| `EADDRINUSE` with nothing in `ss -ltn` | VS Code's port forwarder holding a `CLOSE_WAIT` socket. Reload the remote window, or use a port it never touched |
| `curl http://100.67.44.13:8010/health` hangs | Step 1. Mirrored networking, or Tailscale down on the PC |
| Worker reports `"encoder":"libx264"` | Step 2, in the worker's own shell. NVENC probe failed |
| Player shows a CORS error | `WORKER_WEB_ORIGIN` does not match the browser's origin exactly, scheme and port included |
| Worker returns 403 | `WORKER_ALLOWED_SOURCE_PREFIXES` does not cover `MIRU_PUBLIC_API_URL` |
| Worker returns 502 on first play | ffmpeg exited; the error body carries its stderr tail |
| Downloads never appear in Miru | The downloader is writing to the PC's own disk, not `/mnt/storage` |
| Everything unavailable, worker is up | `MIRU_PUBLIC_API_URL` is `localhost` — that points the PC at itself |

---

## 9. qBittorrent — the downloader that can be watched while it downloads

Miru's default downloader is now qBittorrent rather than aria2, because it is
the only one of the two that can produce a watchable partial file. Checked
against the running instance: aria2 1.37's `stream-piece-selector` is
implemented for HTTP and FTP only and does nothing for BitTorrent, so a file
aria2 is fetching cannot be played until the last piece lands.

aria2 does not have to be uninstalled. It stays selectable with
`MIRU_DOWNLOADER=aria2`. But **only run one of them at a time** — two torrent
clients can both end up writing the same file, and that is corruption rather
than an inconvenience.

### Install (in WSL on the PC, not in a Docker Desktop VM)

```bash
sudo apt update
sudo apt install -y qbittorrent-nox
```

Run it once in the foreground to accept the licence prompt and read the
temporary password it prints:

```bash
qbittorrent-nox
# ***** legal notice *****  → type: y
# "A temporary password is provided for this session: xxxxxxxx"
```

Then stop it with Ctrl-C.

### Configure

The Web UI is on **8080** by default, which is a different port from the
BitTorrent listen port. Open it from the laptop over Tailscale:

```
http://100.67.44.13:8080
```

In **Tools → Options**:

| Setting | Value | Why |
|---|---|---|
| Downloads → Default Save Path | `/mnt/laptop-incoming` | the laptop's `incoming` over NFS, same target aria2 used |
| Downloads → Keep incomplete torrents in | *unset* | a second directory means Miru cannot find the growing file |
| WebUI → Bypass authentication for clients on localhost | **off** | it is reachable over the tailnet, not just localhost |
| WebUI → Username / Password | set them | goes into `MIRU_QBITTORRENT_*` on the laptop |
| BitTorrent → Torrent Queueing | **off** | Miru decides what runs; a queue silently defers a Watch Now |
| Connection → Listening Port | any, forwarded if you can | affects speed, not correctness |

Do **not** turn on global sequential download. Miru sets it per torrent from the
button pressed: Watch Now asks for sequential pieces, Download leaves
libtorrent's rarest-first order alone, which is better for the swarm and for
throughput.

### Tell Miru about it

On the **laptop**, in `.env`:

```bash
MIRU_DOWNLOADER=qbittorrent
MIRU_QBITTORRENT_URL=http://100.67.44.13:8080
MIRU_QBITTORRENT_USER=admin
MIRU_QBITTORRENT_PASSWORD=<what you set above>
```

Restart the API and check the wall: the strip at the top should disappear. If it
still says *"No downloader set up yet"* the URL or credentials are wrong; if it
says *"The PC is asleep"* the credentials are fine and qbittorrent-nox is not
running.

### Start it with the rest

`qbittorrent-nox -d` daemonises. Add it beside aria2 in whatever starts the PC
side, and note that **reboot survival is still an open problem on both machines**
— see `STATE.md`.

### What watch-while-downloading actually covers

| release | works? |
|---|---|
| `direct` / `remux` — H.264, most of an anime library | **yes**, playback starts once ~24 MB of the front has landed |
| `transcode_full` — HEVC, 4K | **no.** The worker runs ffmpeg over the source, and an incomplete file makes ffmpeg reach EOF and stop early |

The release picker already labels which is which before you commit to the
download, so this is visible at the point of choosing rather than discovered
afterwards.


## WSL networking: NAT + port proxies (settled 2026-08-09, after a morning of debugging)

**Do not enable `networkingMode=mirrored` in `.wslconfig`.** Mirrored mode has
a kernel-level defect that cost a full morning: userspace sockets work (curl,
python) but kernel-originated connections do not — and an NFS `mount(2)` is
exactly that, so every mount attempt timed out while every probe succeeded.
The evidence trail: `nc` to 2049 fine, a raw RPC NULL call answered in both
directions, and `mount -vvv` timing out with `clientaddr=192.168.1.x` (a LAN
address inside WSL is the mirrored-mode fingerprint).

NAT mode fixes NFS and breaks inbound instead — the laptop reaches qBittorrent
(8080), the worker (8010) and Prowlarr (9696) via the PC's tailscale IP, which
terminates on Windows, so those ports must be proxied into WSL:

- `deploy/pc-update-portproxy.ps1` (run as Administrator) points the proxies at
  the current WSL IP. **The WSL IP changes on every `wsl --shutdown`**, so run
  it after each restart — or register it as a boot task.
- Two Windows quirks it handles: portproxy rules added while IP Helper is
  running may never bind (config present, netstat shows no listener — only
  `Restart-Service iphlpsvc` materialises them), and the firewall needs an
  inbound allow for 8010,8080,9696 (`New-NetFirewallRule`, one-time).

The NFS mount itself, inside WSL (LAN IP, and v4.2 explicitly — the laptop's
firewall only opens 2049, and a version negotiation that falls back to v3
needs rpcbind/mountd, which are blocked):

```bash
sudo mount -t nfs -o vers=4.2 192.168.1.100:/mnt/storage/incoming /mnt/incoming
```

fstab line (replaces any old `100.71.150.101:/mnt/storage` entries):

```
192.168.1.100:/mnt/storage/incoming /mnt/incoming nfs vers=4.2,defaults,_netdev,soft,timeo=50 0 0
```

Laptop-side prerequisites, already applied and worth knowing about: ufw allows
2049/tcp from the PC's tailscale IP and from 192.168.1.0/24; /etc/exports
carries both; and **after the storage disk drops and remounts, restart
`nfs-kernel-server`** — its kernel threads keep serving the dead filesystem
(listener accepts, nothing answers) until restarted.
