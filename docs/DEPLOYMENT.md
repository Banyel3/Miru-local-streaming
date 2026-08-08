# Miru — Deployment

Two machines, one of which is optional most of the time.

This document supersedes parts of `ARCHITECTURE.md` §5. Where they disagree, this
file is current. The reversals are recorded in §8.

---

## 1. Topology

```
┌─ LAPTOP ─ always on ────────────────────┐        ┌─ PC ─ on demand ─────────────┐
│  i5-1155G7 · 932 GB HDD · wifi          │        │  Ryzen 5700G · RTX 5060      │
│                                          │        │  Windows 11 + WSL2           │
│  Next.js          :3001                  │        │                              │
│  FastAPI          :8000   ── HTTP ─────────────>  │  NVENC worker      :8010     │
│  Postgres         :5432   <───── HLS ───────────  │  movies-downloader :5000     │
│  media  /mnt/storage                     │        │                              │
│                                          │  <──── mounts /mnt/storage (NFS)      │
│  direct · remux                          │        │  anything needing an encoder │
└──────────────────────────────────────────┘        └──────────────────────────────┘
```

**The laptop is the server.** It holds the library, the database, the UI, and the
API. It is up whenever you are.

**The PC is an accelerator.** It re-encodes video and it downloads torrents.
Nothing else depends on it, and when it is asleep the library still browses and
most of it still plays.

### Why this shape

Measured on the laptop (sustained, at playback rate):

| Rung | Runs on | Sustained cost on the laptop |
|---|---|---|
| `direct` | Laptop | Zero — file serving with HTTP Range |
| `remux` | Laptop | 0.37 s CPU for a 10-minute film (~0.06% of a core) |
| `transcode_audio` | **PC, NVENC** | 1.8% of a core if done locally — but it runs an encoder, so it goes to the PC |
| `transcode_full` | **PC, NVENC** | 114% of a core and 95 °C if done locally — so it isn't |

Remux is not transcoding. It is `-c copy`: the video stream is copied byte for
byte and only the container is rewritten. It stays on the laptop because MKV +
H.264 is the standard shipping format for anime, so remux is the *majority* rung
in a real library. Pushing it to the PC would make the PC a hard dependency for
most of the collection and defeat the point of this layout.

Nothing that re-encodes video ever runs on the laptop.

### The source is always a URL

The transcode worker takes a **URL**, never a path. This is the single rule that
keeps the design simple:

- a library file becomes `http://laptop:8000/api/stream/{id}`
- a live torrent becomes `http://127.0.0.1:5000/api/v1/torrent/video?...`

Both are just sources. Verified: ffmpeg opened an HTTP source, issued a `206
Partial Content` to seek two minutes in, and encoded without ever creating a
local copy. No shared filesystem is required for transcoding.

---

## 2. Storage

Media lives on the laptop's HDD at `/mnt/storage` (932 GB).

The PC mounts it so completed downloads land in the library directly instead of
being written to the PC and copied across afterwards.

**On the laptop** — export to the PC's tailnet address only:

```bash
sudo apt install -y nfs-kernel-server
echo '/mnt/storage 100.x.x.x(rw,sync,no_subtree_check,all_squash,anonuid=1000,anongid=1000)' \
  | sudo tee -a /etc/exports
sudo exportfs -ra
```

**On the PC, inside WSL2:**

```bash
sudo apt install -y nfs-common
sudo mkdir -p /mnt/storage
sudo mount -t nfs 100.71.150.101:/mnt/storage /mnt/storage
```

Add it to `/etc/fstab` on the PC so it survives a reboot.

An NFS mount inside WSL2 does **not** go through `drvfs`, so the "`/mnt/` is slow
for metadata" constraint in `SETUP.md` does not apply to it. Scanning this mount
may well be faster than scanning the PC's own Windows drives.

> **This is capacity, not durability.** One drive with no redundancy. If it dies
> the library is gone exactly as completely as before. A second location is only
> a backup if it is a second copy.

---

## 3. Two ways to watch

Acquisition offers an explicit choice rather than one hidden default.

### Download

```
Miru ──> downloader (PC) ──> writes to /mnt/storage ──> scanner picks it up
                                                    ──> normal library file
```

Behaves like everything else afterwards: instant seek, resume, subtitles,
next-episode. Costs disk and a wait.

When a download finishes, the file is marked **ready to watch** — Miru does not
navigate you into the player. Being pulled out of whatever you are doing forty
minutes later is not a feature.

### Watch Now

```
Miru ──> PC: downloader streams torrent ──> PC: ffmpeg NVENC ──> HLS ──> Miru ──> browser
         nothing written to /mnt/storage
```

Starts in seconds and stores nothing durably. The honest constraints:

- **Linear playback is fine.** Torrent streaming is sequential-friendly and this
  is the case it is good at.
- **Seeking forward past the downloaded region stalls**, because the byte range
  ffmpeg asks for maps to pieces the swarm has not delivered yet. Seeking
  backward is free. The UI should show what is buffered so a stall is
  predictable rather than mysterious.
- **"Nothing stored" is not literally true.** `torrent-stream` keeps pieces under
  `/tmp/torrent-stream/{infoHash}` while streaming. Point that at a tmpfs on the
  PC if it genuinely must stay off disk, and clean it up when the session ends.

Neither path requires forking `movies-downloader`. It already streams over HTTP
with Range support; it is run upstream and unmodified, bound to `127.0.0.1`.

---

## 4. Availability in the UI

The strategy ladder already knows which files need the PC, so availability is
**derived per request** from `(strategy, worker_up)` and never stored — a stored
value goes stale the moment the PC wakes.

| State | Meaning | Shown when |
|---|---|---|
| `available` | Laptop can serve it alone | `direct`, `remux` |
| `gpu-ready` | Plays normally | `transcode_audio` / `transcode_full`, worker reachable |
| `unavailable` | *"Needs the PC to transcode — it's offline"* | `transcode_audio` / `transcode_full`, worker down |

**Health probe rules.** A ~300 ms TCP check against the worker, cached ~10 s in
the API process. The library page renders from cache and never blocks on the
network. It fails **open** to unknown: a flaky probe must never hide a file that
would have played.

---

## 5. Addressing: Tailscale, not a static IP

**No static IP is needed.** A tailnet address is already stable in a stronger
sense than a DHCP reservation: it is bound to the machine's identity rather than
to your router, so it survives lease renewals, router reboots, and either machine
moving to a different network entirely.

Use the tailnet IP:

```bash
MIRU_TRANSCODE_WORKER=http://100.67.44.13:8010
```

MagicDNS names are **not** used here. Verified on this laptop: `/etc/resolv.conf`
holds only `1.1.1.1`, so Tailscale is not managing system DNS, and neither
`ban-pc` nor its full `.ts.net` form resolves for ordinary programs. Enable
`tailscale set --accept-dns=true` on both machines if you want names, and check
`getent hosts` before depending on it.

Two machines on the same LAN connect **directly over the LAN** — Tailscale does
local peer discovery, so there is no internet hop and no relay. Confirm any pair
with:

```bash
tailscale ping 100.67.44.13   # prints the path it took
```

A static LAN IP would give you less (home-only) for more work. Skip it.

### The WSL2 wrinkle

This is the part that bites. WSL2 sits behind a NAT inside Windows, so a service
listening on `:8001` **inside WSL2 is not reachable at the Windows machine's
tailnet address** by default. Three ways out, best first:

1. **Mirrored networking (recommended, Windows 11).** WSL shares the host's
   network interfaces outright, so a port bound in WSL2 is reachable on the
   host's addresses — including the tailnet one. Create `%UserProfile%\.wslconfig`:

   ```ini
   [wsl2]
   networkingMode=mirrored
   ```

   Then `wsl --shutdown` and restart. Run Tailscale on Windows as normal; nothing
   else is required. Needs Windows 11 22H2+ and WSL 2.0+.

2. **Tailscale inside WSL2.** The WSL instance becomes its own tailnet node with
   its own name. Works, but it is a second node to authenticate and `tailscaled`
   has to be started with the distro.

3. **Port proxy from Windows to WSL2.** `netsh interface portproxy` forwarding
   8001 to the WSL2 IP. Avoid this: WSL2's internal IP changes on every restart,
   so the rule needs re-creating each boot.

Verify from the laptop, whichever route you take:

```bash
curl -m 5 http://100.67.44.13:8010/health
```

---

## 6. The transcode worker

`apps/worker/`, deployed to the PC. A small FastAPI service with **no database,
no SQLAlchemy, and no domain models** — it takes a URL and returns HLS. That
boundary is what stops it becoming a second Miru.

### The rule

**If an encoder runs, it runs on the PC.** One rule, not a per-rung table to
remember:

| Rung | Where | Encoder? |
|---|---|---|
| `direct` | Laptop | No — serving bytes |
| `remux` | Laptop | No — `-c copy`, container rewrite only |
| `transcode_audio` | **PC** | Yes |
| `transcode_full` | **PC** | Yes |

`remux` stays on the laptop because no encoder runs and because MKV + H.264 is
the majority rung in an anime library; moving it would make the PC a dependency
for most of the collection.

### Contract

```
GET /health                                 → {ok, encoder, active_sessions}
GET /hls/{sid}/master.m3u8?src=<url>        → lazily starts ffmpeg, returns the master playlist
GET /hls/{sid}/{variant}/index.m3u8         → per-rendition media playlist
GET /hls/{sid}/{variant}/seg/{n}.ts         → segments
```

`sid = sha256(src|profile)[:16]`. Deterministic, so the endpoint is idempotent
and needs no session-creation call — two players asking for the same thing share
one encode. A per-`sid` lock makes a double request start exactly one ffmpeg.

**Encoder is auto-detected**: `h264_nvenc` when the device is present,
`libx264` otherwise. That fallback is what allows the whole worker to be built
and tested on a machine with no NVIDIA hardware before it is deployed.

**`src` is checked against an allowlist prefix.** The worker fetches whatever
URL it is handed, so without this it is an SSRF hole on the tailnet.

### Adaptive ladder

A master playlist with concurrent renditions, capped by the source — never
upscale:

| Variant | Bitrate |
|---|---|
| 1080p | 6 Mbps |
| 720p | 3 Mbps |
| 480p | 1.2 Mbps |

The 5060 encodes these simultaneously without strain, and the player's Quality
menu populates itself from the master playlist — there is no picker to write.
Dropping to a lower rung happens automatically when the connection cannot keep
up, which is the case that matters on mobile data.

**`transcode_audio` produces a single rendition.** Its video stream is copied,
so there is nothing to make variants from. The menu shows one entry, correctly.

### Playlist type and seeking

`-hls_playlist_type event`: segments are retained and the manifest grows.

| | Behaviour |
|---|---|
| Backward seek | Free — the segments already exist |
| Forward seek inside the encoded region | Free |
| Forward seek past the encoder | Waits (v1); restart the encode at that offset as a new session (v2) |
| First-play latency | Time to produce ~2 segments |

Roughly 5 GB for a two-hour film at 6 Mbps. That is why the HLS cache lives on
**the PC's local disk** — not tmpfs, which cannot hold it, and not the NFS
share, since writing it back over wifi would be absurd. Evicted by age and on
worker start.

### How Miru reaches it

`GET /api/stream/{id}/index.m3u8` on the laptop returns a **302 to the worker**,
so the browser pulls segments straight from the PC over the tailnet rather than
proxying every byte through the laptop. The client is already on the tailnet —
that is how it reached Miru at all.

---

## 7. Startup

**Laptop** — Docker already runs here, so Postgres is a container (this reverses
`ARCHITECTURE.md` §5; see below). Everything else is a systemd unit.

**PC** — Task Scheduler starts WSL2 at logon, as `SETUP.md` §5 describes; the
startup script mounts the NFS share, then starts the worker and the downloader.

`SETUP.md` §6's advice to disable sleep still applies **on the PC**, and now for
a narrower reason: there is no remote wake, so a sleeping PC means
`transcode_full` files are unavailable until someone walks over to it. Disabling
sleep is the whole mitigation. The laptop keeps serving everything else
regardless, so this degrades rather than breaks.

Wake-on-LAN was considered and dropped: it needs the PC on wired Ethernet with
the NIC armed in BIOS and Fast Startup disabled. Worth revisiting if the PC is
ever wired — the laptop being wifi-only is not an obstacle, since it would only
need to *send* a broadcast that the router bridges to the wired segment.

---

## 8. Decisions reversed here, and why

Recording these rather than letting the docs silently drift.

**Postgres moves from the WSL2 box to the laptop, in Docker** (was
`ARCHITECTURE.md` §5, decision D10). The original argument was that the WSL2 box
already needed a hand-written startup path for NVENC and the API, so one startup
story beat two. The API is no longer on that box, and the laptop already runs
Docker with systemd, so the container is the smaller thing to operate there.

**The API moves from the WSL2 box to the laptop** (was decision D2). D2 put it
next to the media and the GPU. Neither premise survived: the media moved to the
laptop's 932 GB drive, and the laptop's own encode capability turned out to be
irrelevant because we deliberately do not use it. What decided it was that the
laptop is always on and the PC sleeps.

**A second node now exists**, which the spec's §12 non-goals explicitly refused.
This is deliberate. The second node is driven by where the GPU physically is, not
by module boundaries, and the failure mode is graceful: only `transcode_full`
depends on it, and the UI says so plainly. The modular monolith is intact — the
worker is an ffmpeg wrapper with no database and no domain models, not a second
Miru.

**No static IP anywhere.** Tailscale addresses are stable by identity, so a DHCP
reservation would be strictly less useful. The real work is WSL2's NAT, handled
by mirrored networking mode — see §5.

**Wake-on-LAN was dropped**, so `SETUP.md` §6 (disable sleep on the PC) stands
unchanged. Revisit only if the PC moves to wired Ethernet.
