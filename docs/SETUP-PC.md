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

```bash
# on the PC, inside WSL2
python3 -m http.server 8001
```

```bash
# on the laptop
curl -m 5 http://100.67.44.13:8001/
```

**Expect** a directory listing. **If it hangs or refuses**, stop here — nothing
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

Then either free the port, or pick a different one for the worker and use it
consistently in `MIRU_TRANSCODE_WORKER` and the test above. Any free high port
works — there is nothing special about 8001.

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
cd ~/miru/apps/worker && ../../.venv/bin/uvicorn miru_worker.main:app --host 0.0.0.0 --port 8001
```

**Check, from the laptop:**

```bash
curl -m 5 http://100.67.44.13:8001/health
```

**Expect** `"encoder":"h264_nvenc"`. If it says `libx264`, the worker's own NVENC
probe failed even though step 2 passed — recheck step 2 in the same shell the
worker runs in.

---

## Step 4 — Point Miru at the worker

```bash
# on the laptop, in .env
MIRU_TRANSCODE_WORKER=http://100.67.44.13:8001
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

## Step 6 — movies-downloader

Run it **upstream and unmodified**. It already streams over HTTP with Range
support, which is all Miru needs; forking it would mean maintaining a divergent
copy of someone else's scraper for no gain.

```bash
# on the PC, inside WSL2
git clone https://github.com/Atuldubey98/movies-downloader ~/movies-downloader
cd ~/movies-downloader && npm run build
```

**Bind it to loopback only.** It ships with no authentication whatsoever, so
anything that can reach it can queue downloads onto your disk. Miru talks to it
from the same machine, so it never needs to listen on the tailnet:

```bash
HOST=127.0.0.1 PORT=5000 npm start
```

**Check, from the PC:**

```bash
curl -m 10 "http://127.0.0.1:5000/api/v1/health"
```

**Check it is NOT reachable from the laptop** — this one matters:

```bash
# on the laptop; expect connection refused
curl -m 5 http://100.67.44.13:5000/api/v1/health
```

If that succeeds, it is listening on all interfaces. Fix the bind before going
further.

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
  miru_worker.main:app --host 0.0.0.0 --port 8001 &
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
| `curl http://100.67.44.13:8001/health` hangs | Step 1. Mirrored networking, or Tailscale down on the PC |
| Worker reports `"encoder":"libx264"` | Step 2, in the worker's own shell. NVENC probe failed |
| Player shows a CORS error | `WORKER_WEB_ORIGIN` does not match the browser's origin exactly, scheme and port included |
| Worker returns 403 | `WORKER_ALLOWED_SOURCE_PREFIXES` does not cover `MIRU_PUBLIC_API_URL` |
| Worker returns 502 on first play | ffmpeg exited; the error body carries its stderr tail |
| Downloads never appear in Miru | The downloader is writing to the PC's own disk, not `/mnt/storage` |
| Everything unavailable, worker is up | `MIRU_PUBLIC_API_URL` is `localhost` — that points the PC at itself |
