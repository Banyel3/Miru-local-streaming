# Go-live: magic-link auth for Miru

## Goal

Expose Miru beyond the tailnet to a handful of invited people. Access is
gated by email: only allowlisted addresses can sign in, via a magic link
(and a typeable OTP code in the same email) sent through Resend. Each
person/device gets its own server-side session. Everything else — worker,
Prowlarr, qBittorrent, aria2 — stays tailnet-only.

## What exists today

- `require_token` (apps/api/miru/core/auth.py): single shared token, off when
  `MIRU_TOKEN` is empty. Tailscale is the perimeter. This is replaced as the
  public gate but kept as-is for tailnet/API-internal use.
- nginx (deploy/nginx/miru.conf) already fronts everything on one origin:
  `/api/` → FastAPI :8000, `/hls` + `/worker/` → PC worker via tailnet,
  `/` → Next :3001. Public traffic can reuse this exactly; only TLS and the
  auth gate are new.
- `settings.public_worker_url` already exists — pointing it at the public
  origin routes HLS segments through nginx, so the PC worker needs no
  public exposure.
- SSR fetches go Next → `http://localhost:8000` directly (API_INTERNAL),
  not through nginx — so an nginx-level gate does not break server rendering.

## Architecture

```
                         internet
                            │ 443 (TLS)
                    ┌───────▼────────┐
                    │     nginx      │  auth_request /api/auth/check
                    │  (the ONE gate)│  cookie: miru_session
                    └──┬─────┬─────┬─┘
              no cookie│     │ ok  │ ok
                   302 ▼     ▼     ▼
                 /login   /api/*  /hls (→ PC worker, tailnet hop)
                 (Next)   (FastAPI :8000)
```

- **Single enforcement point: nginx `auth_request`.** Every location except
  `/login`, `/api/auth/*`, and Next static assets subrequests
  `GET /api/auth/check`; 204 passes. **On 401 the behaviour differs by
  location, deliberately**: page navigations (`location /`) redirect to
  `/login`; `/api/` and `/hls` return a plain 401 — an expired session
  mid-stream must surface as a status the player and pollers can react to,
  not as login HTML where JSON was expected (the exact failure shape of the
  "Couldn't load the releases" outage). The web's fetch paths send the
  browser to `/login` on a 401. The auth_request location itself sets
  `proxy_pass_request_body off` and clears `Content-Length`, or Server
  Action POSTs hang on the subrequest.
- **Sessions**: Postgres table `auth_sessions(id_hash, email, created_at,
  last_seen_at, expires_at, user_agent)`. Cookie `miru_session` = 256-bit
  random, HttpOnly, Secure, SameSite=Lax, Path=/. Stored hashed (sha256) so
  a DB read never leaks a usable cookie. 30-day sliding expiry with a
  **90-day absolute cap** from created_at — sliding alone means an active
  session never dies, which makes uninviting impossible. `last_seen`
  updated at most once a minute (same throttle pattern as the stream
  heartbeat). Logout deletes the row. **`check()` also re-validates the
  session's email against the current allowlist** (with the 60 s cache), so
  removing an address from `MIRU_ALLOWED_EMAILS` locks that person out
  within a minute — no manual SQL.
- **Magic link + OTP, one table**: `auth_logins(token_hash, code_hash,
  email, created_at, expires_at, consumed_at, attempts)`. POST
  `/api/auth/request {email}` → if allowlisted, insert row (token: 256-bit
  url-safe; code: 6 digits), send one Resend email containing both the link
  (`https://<host>/api/auth/verify?token=…`) and the code. **Response is an
  identical 200 whether or not the email is allowlisted** — no oracle for
  probing the allowlist.
- **Verify**: GET `/api/auth/verify?token` and POST `/api/auth/otp
  {email, code}` both: constant-time compare against hash, reject if
  expired (15 min) or consumed, mark consumed, create session, set cookie,
  redirect `/`. OTP allows 5 attempts per login row, then the row dies.
- **Allowlist**: `MIRU_ALLOWED_EMAILS` env, comma-separated, compared
  case-insensitively after trim. A couple of addresses; no admin UI.
- **Resend**: `MIRU_RESEND_API_KEY` + `MIRU_MAIL_FROM` env. One httpx POST
  to api.resend.com; failure logged, request still returns the neutral 200.
- **Rate limiting**: per-email 5 requests/hour and per-IP 20/hour, counted
  in their own `auth_rate(key, window_start, n)` table — deliberately NOT
  `auth_logins`, because non-allowlisted requests must be counted too and
  they never create a login row. The IP is `X-Forwarded-For` as set by
  tailscaled, trusted only when the connection itself comes from loopback
  (the Funnel door); anything else uses the socket address. Over limit →
  same neutral 200, no email.
- **Tailnet bypass**: requests arriving on the tailnet vhost keep working
  without login (server block for ts.net/100.x names has no auth_request).
  Public hostname gets the gate. Phones on the tailnet lose nothing.

## Exposure path — Tailscale Funnel (decided, review D3)

`tailscale funnel` terminates TLS at tailscaled and forwards plain HTTP to
a loopback port. Same hostname serves two audiences through two doors:

```
public visitor ──TLS──▶ tailscaled (Funnel) ──▶ 127.0.0.1:8081  nginx "public"
                                                    server block: auth_request ON
tailnet device ───────────────────────────────▶ 100.71.150.101:80  nginx "home"
                                                    server block: no gate (as today)
```

- New nginx server block `listen 127.0.0.1:8081` — identical proxy locations
  (via a shared snippet, not copies), plus `auth_request`. The existing
  tailnet block is untouched, so phones on the tailnet lose nothing.
- Enable: `tailscale funnel --bg 8081` (per-machine, survives reboot).
  Disable = one command; the gate stays either way.
- **Known ceiling, accepted in D3:** Funnel routes bytes through Tailscale's
  relays. The NVENC ladder (~3–6.7 Mbps) should stream fine; a 4K direct-play
  remux may buffer. If it bites, revisit port-forward 443 later — the nginx
  block is exposure-agnostic, only the front door changes.
- Cookies: browser side is HTTPS (Funnel cert), so `Secure` holds. Funnel
  forwards `X-Forwarded-For`; log it for the rate limiter's per-IP count.

## Files

| file | change |
|---|---|
| `apps/api/miru/auth/models.py` | AuthSession, AuthLogin tables |
| `apps/api/miru/auth/service.py` | request_login / verify_token / verify_otp / check / logout, rate limits |
| `apps/api/miru/auth/router.py` | /api/auth/{request,verify,otp,check,logout} |
| `apps/api/miru/auth/mail.py` | Resend client (faked in tests) |
| `apps/api/miru/core/config.py` | allowed_emails, resend key, mail_from, public_origin |
| `apps/web/app/login/page.tsx` | email form → code entry, Miru-styled |
| `deploy/nginx/miru-public.conf` | public server block: TLS, auth_request, error_page 401 → /login |
| `apps/api/tests/test_auth_*.py` | full coverage (below) |
| `docs/DEPLOYMENT.md` | go-live runbook §: certbot, DNS, env, rotation |

## Hard dependency: a sending domain for Resend

Resend will not deliver to third parties from an unverified domain — the
free `onboarding@resend.dev` sender only mails the account owner. Going
live therefore needs one cheap domain used purely for *sending*: add it in
Resend, publish the DKIM/SPF DNS records, set `MIRU_MAIL_FROM` to an
address on it. Serving still happens on the Funnel hostname; the domain
never points at the laptop.

## Security prerequisites before Funnel goes live (blocking)

1. Rotate the qBittorrent password (`deploy/rotate-qbittorrent-password.py`
   — script exists, run with PC awake).
2. Confirm worker/Prowlarr/aria2/qB listen only on tailnet/LAN, never on
   the public interface. **`MIRU_TOKEN` stays empty** — it is a global
   dependency, and setting it would 401 every browser and SSR fetch,
   including the login flow itself. The API's perimeter is its bind
   (tailnet + loopback; verify) plus the nginx gate.
3. `WORKER_ALLOWED_SOURCE_PREFIXES` unchanged (laptop API only).
4. Cookie flags verified Secure+HttpOnly on the public origin.
5. Gate curl checklist (runbook, mandatory — the nginx gate has no pytest):
   no cookie → 401 on `/`, `/api/library`, `/hls`; garbage cookie → 401;
   valid cookie → 200 on all three; a Server Action POST completes through
   the gate; `/login` and `/api/auth/request` reachable without a cookie.

## Tailnet behaviour with Funnel on (accepted)

tailscaled serves 443 on the tailnet too once Funnel is enabled, so a
tailnet device using `https://ban-1…` meets the gated door and logs in
once; `http://` keeps today's ungated behaviour. Documented, not
engineered around.

## Tests (all pytest, no network — Resend faked at the seam)

- allowlisted email → login row created, mail sent with link+code
- non-allowlisted email → identical 200, no row, no mail
- token verify: happy path sets cookie + session row
- token reuse → 401 (consumed)
- token expired (15 min) → 401
- OTP happy path; wrong code ×5 → row dead even with right code after
- rate limit: 6th request/hour same email → no new row
- check: valid cookie → 204; missing/garbage/expired → 401
- session sliding expiry updates last_seen (throttled)
- logout deletes the session; cookie cleared
- mail failure → neutral 200, no 500 (a downed Resend must not reveal
  allowlist membership either)
- emails compare case-insensitively
- absolute cap: session older than 90 days → 401 even if used yesterday
- allowlist revocation: check() 401s a live session whose email was removed
- rate rows count non-allowlisted requests too (the probe traffic they exist for)
- login page states (web): expired-link message with retry, OTP lockout
  message, mid-browse 401 → redirected to /login rather than a broken page

## What already exists (reused, not rebuilt)

- nginx single-origin proxy incl. worker HLS — the public block reuses the
  same location snippet; only the gate and listen socket are new.
- `public_worker_url` — already routes HLS through nginx when set.
- `require_token` — stays for what it was: a belt against misconfigured
  ports on the tailnet. Not the public gate.
- The heartbeat throttle pattern — reused for last_seen and check() cache.
- Postgres + SQLAlchemy models/create_all — sessions ride the same rails.

## NOT in scope

- Admin UI for allowlist/sessions — two emails live in an env var; a
  devices page is a follow-up TODO, not a gate for going live.
- Roles/permissions — everyone invited sees everything. One tier.
- Port-forward/CDN exposure — Funnel chosen (D3); revisit only if relay
  bandwidth actually hurts.
- Per-user watch progress server-side — progress stays in localStorage;
  merging it per-account is its own feature.
- OAuth/passkeys — magic link + OTP is the whole surface.

## Implementation tasks

- [ ] **T1 (P1)** — api — auth module: models, service, router, mail seam + full test list above
- [ ] **T2 (P1)** — api — config: allowed_emails, resend key, mail_from, public_origin
- [ ] **T3 (P1)** — web — /login page (email → link-sent → OTP entry), 401 redirect handling in fetch paths
- [ ] **T4 (P1)** — deploy — nginx public block on 127.0.0.1:8081 with auth_request + per-location 401 behaviour; shared location snippet refactor
- [ ] **T5 (P1)** — deploy — runbook: Resend domain verification, Funnel enable, gate curl checklist, qB rotation
- [ ] **T6 (P2)** — api — janitor: purge expired sessions/logins/rate rows daily
- [ ] **T7 (P3)** — web — devices page (list/revoke own sessions)

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Outside Voice | (auto, Claude subagent) | Independent 2nd opinion | 1 | ISSUES_FOUND | 8 findings: 6 folded into plan, 1 accepted limitation, 1 declined (re-argued settled D2) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 8 issues total, 0 critical gaps open; D2 build-in-app, D3 Funnel, D4 nginx auth_request |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 (prior, library) | CLEAR | score 4/10 → 9/10 (library redesign, shipped) |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**CROSS-MODEL:** Codex unavailable (auth); Claude subagent served as outside voice. Its findings 1 (MIRU_TOKEN bricks login), 3 (rate-limit contradiction), 4 (401-as-HTML failure class), 5 (unrevokable sliding sessions) and 8 (untested nginx gate) were all accepted and folded; finding 7 (skip app auth, share tailnet nodes) declined — settled at D2.

**VERDICT:** ENG CLEARED — ready to implement. Blocking external dependencies before go-live: Resend-verified sending domain, Funnel enable, qBittorrent password rotation.

NO UNRESOLVED DECISIONS
