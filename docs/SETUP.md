# Miru — Setup (Windows 11 + WSL2)

Target machine: Ryzen 7 5700G, 32GB RAM, RTX 5060, Windows 11, WSL2 running Ubuntu.

---

## 1. WSL2

From an elevated PowerShell on Windows:

```powershell
wsl --install -d Ubuntu
wsl --set-default-version 2
wsl --update
```

Confirm you are on WSL2 (version 2, not 1 — version 1 has no NVENC passthrough):

```powershell
wsl -l -v
```

### Media drives

Windows drives appear under `/mnt/`. A library on `D:\Media` is `/mnt/d/Media` inside
WSL. No extra mounting is needed, but note that `/mnt/` is slow for metadata
operations. Miru never walks a directory in response to a request — scans are
incremental background jobs. Sequential reads (which is what streaming is) are fine.

---

## 2. System packages

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip postgresql ffmpeg curl git
```

Node 20+ for the frontend:

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

---

## 3. Postgres

Native, not Docker — see `ARCHITECTURE.md` §5 for why.

```bash
sudo service postgresql start
sudo -u postgres psql -c "CREATE USER miru WITH PASSWORD 'miru';"
sudo -u postgres psql -c "CREATE DATABASE miru OWNER miru;"
```

Verify:

```bash
psql postgresql://miru:miru@localhost/miru -c 'select version()'
```

Postgres does not start on boot inside WSL2 by default. The systemd route is cleanest —
enable systemd in `/etc/wsl.conf`:

```ini
[boot]
systemd=true
```

Then `wsl --shutdown` from PowerShell, restart the distro, and:

```bash
sudo systemctl enable --now postgresql
```

If you would rather not enable systemd, add `sudo service postgresql start` to the
startup script in §7.

---

## 4. ffmpeg with NVENC

The apt build of ffmpeg includes NVENC support; the driver comes from the Windows-side
NVIDIA driver, which WSL2 exposes. Do not install a Linux NVIDIA driver inside WSL —
it will break the passthrough.

Verify the encoder is present:

```bash
ffmpeg -hide_banner -encoders | grep nvenc
```

Expect `h264_nvenc` and `hevc_nvenc` in the output.

Verify it actually encodes (this is the test that matters — the encoder can be listed
and still fail at runtime):

```bash
ffmpeg -hide_banner -f lavfi -i testsrc=size=1280x720:rate=30 -t 3 \
       -c:v h264_nvenc -f null -
```

A clean exit means NVENC works. `Cannot load libnvidia-encode.so.1` means the Windows
driver is too old — update it from GeForce Experience, then `wsl --shutdown` and retry.

`nvidia-smi` should also report the RTX 5060 from inside WSL.

---

## 5. WSL2 does not auto-start on boot

WSL only starts when something asks for it. Create a Task Scheduler entry so the
distro comes up at logon.

Task Scheduler → Create Task:

- **General:** "Start WSL Ubuntu", check *Run whether user is logged on or not*
- **Triggers:** At log on
- **Actions:** Start a program
  - Program: `C:\Windows\System32\wsl.exe`
  - Arguments: `-d Ubuntu -u root /etc/miru/startup.sh`
- **Conditions:** uncheck *Start the task only if the computer is on AC power*

Where `/etc/miru/startup.sh` (inside WSL, `chmod +x`) is:

```bash
#!/usr/bin/env bash
service postgresql start
su - <your-user> -c 'cd /home/<your-user>/miru && ./scripts/start-api.sh &'
```

If you enabled systemd in §3, a systemd unit for the API is tidier than the script;
the Task Scheduler entry is still required, because it is what starts WSL at all.

---

## 6. The PC sleeps and kills the service

Miru is down whenever the machine is asleep. This is an accepted trade — it buys GPU
transcoding and one process to operate. Disable sleep so it is not down constantly:

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 15
```

The monitor still blanks; the machine stays awake. Confirm nothing else is sleeping it:

```powershell
powercfg /requests
```

---

## 7. Miru itself

```bash
git clone <repo> ~/miru
cd ~/miru

cp .env.example .env
$EDITOR .env          # set MIRU_LIBRARY_PATHS to your /mnt/... directories

./scripts/dev.sh      # API on :8000, web on :3000
```

`scripts/dev.sh` creates the Python venv, installs both sides, and runs them together.
To run them separately: `scripts/start-api.sh` and `cd apps/web && npm run dev`.

First scan:

```bash
curl -X POST localhost:8000/api/library/scan
curl localhost:8000/api/jobs/1
```

Then open <http://localhost:3000>.

---

## 8. Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Tailscale Serve terminates TLS and puts the app on your tailnet, which means nginx is
optional — it earns its place only if you want it serving the Next.js build directly
and routing `/api`:

```bash
sudo tailscale serve --bg --https=443 localhost:3000
sudo tailscale serve --bg --https=443 --set-path=/api localhost:8000
```

The app is then at `https://<machine>.<tailnet>.ts.net` from any device on the
tailnet. **Do not** run `tailscale funnel` — that publishes it to the internet, which
is an explicit non-goal.

Set `MIRU_TOKEN` in `.env` once the API is reachable off localhost. Without it the API
is open to anything that can route to it.

---

## 9. Troubleshooting

| Symptom | Cause |
|---|---|
| `connection refused` on :8000 after reboot | WSL did not start — check the Task Scheduler entry in §5 |
| API up, DB errors | Postgres did not start — `sudo service postgresql start` |
| Scan finds nothing | `MIRU_LIBRARY_PATHS` points at a Windows path (`D:\Media`) instead of a WSL path (`/mnt/d/Media`) |
| Scan is very slow | Expected on first run over `/mnt/`. Subsequent scans are incremental (mtime + size) |
| Video lists but will not play | Its `playback_strategy` is not `direct`. Remux lands in M3 |
| `Cannot load libnvidia-encode.so.1` | Windows NVIDIA driver too old, or a Linux driver was installed inside WSL |
| Server unreachable while away | The PC is asleep or off. See §6 |
