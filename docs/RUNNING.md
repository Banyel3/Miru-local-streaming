# Running Miru

One command per machine.

```bash
miru            # start everything for this machine
miru status     # what is up, what is not
miru stop       # stop what miru started
miru logs api   # tail a service (api, web, worker, prowlarr, aria2)
```

The role is **detected, not configured**: the PC has `/opt/Prowlarr`, the laptop
has the built Next.js app. Override with `MIRU_ROLE=pc` or `MIRU_ROLE=laptop` if
that guess is ever wrong.

| Machine | Starts |
|---|---|
| Laptop | Postgres (Docker container `docker_db_1`), FastAPI :8000, Next.js :3001 |
| PC | Transcode worker :8010, Prowlarr :9696, aria2 :6800 |

Readiness is judged by **the port answering**, not by a pidfile — a pidfile
survives a crash and would report a dead service as running. Each service gets
20 seconds to start listening; if it does not, `miru` prints the tail of its log
rather than leaving you to find it.

Logs are in `~/.miru-run/<service>.log`.

---

## The alias

```bash
echo "alias miru='$HOME/dev/projects/Miru-local-streaming/scripts/miru'" >> ~/.bashrc
source ~/.bashrc
```

On the **PC**, where the repo lives at `~/miru`:

```bash
echo "alias miru='$HOME/miru/scripts/miru'" >> ~/.bashrc
source ~/.bashrc
```

---

## `wsl` still opens the wrong distro

If typing `wsl` drops you into Docker Desktop's VM — no `apt`, no `sudo`, prompt
showing `/mnt/host/c` — the default distro is still `docker-desktop`. From
PowerShell:

```powershell
wsl -l -v                    # the * marks the default
wsl --set-default Ubuntu     # use the exact name from that list
```

`wsl --shutdown` afterwards if it still misbehaves. Confirm with:

```bash
wsl
whoami                             # your user, not root
cat /etc/os-release | head -2      # Ubuntu
ls /mnt/c                          # NOT /mnt/host/c
```

Docker Desktop's distro cannot be removed while Docker Desktop is installed —
it is how Docker runs. It only needs to stop being the *default*.

---

## Startup order

Nothing here is load-bearing on order, but it is worth knowing what degrades:

- **Laptop first.** The PC's worker pulls sources from the laptop's API; with the
  laptop down, transcoding has nothing to read.
- **The PC is optional.** Without it, `direct` and `remux` files play normally
  and anything needing an encoder reports *"Needs the PC to transcode — it's
  offline"*. That is the design working, not a failure.
- **Postgres before the API**, which `miru` handles.

## Surviving a reboot

It does not, yet. `miru` starts things in the foreground under `nohup`; nothing
is registered with systemd or Task Scheduler. Making that permanent is the last
step of `SETUP-PC.md`.
