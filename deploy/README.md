# Remote `notebooklm-mcp` — Docker + Cloudflare Tunnel

Run the MCP server as a **remote connector** (Claude Code / Claude.ai / Cursor)
behind a Cloudflare Tunnel: no public IP, no open ports, no TLS certificate to
manage. Single-tenant, self-hosted.

> ⚠️ **Use a dedicated / throwaway Google account.** The mounted
> `master_token.json` is a durable, full-account credential. Treat the mounted profile dir
> and `.env` as secrets (both are gitignored).

## Prerequisites
- Docker + Docker Compose.
- A domain on Cloudflare (free plan is fine) for the Tunnel hostname.

## 1. Bootstrap the master token (once, on a machine with a browser)
```bash
pip install "notebooklm-py[browser,headless]"
notebooklm login --master-token --account you@example.com
```
This writes `master_token.json` (+ a minted `storage_state.json`) into
`~/.notebooklm/profiles/<profile>/`. **You don't copy or chown anything** — the
container mounts that dir directly and runs as *your* uid:gid, so the files stay
owned by you (your `notebooklm` CLI keeps working) and are readable/writable with
no permission dance.

- **Default:** mounts `~/.notebooklm/profiles/default`.
- **Other profile:** set `NOTEBOOKLM_PROFILE_DIR` in `.env` (e.g. a
  dedicated/throwaway profile — recommended, since `master_token.json` is a
  full-account credential).

The dir is mounted **read-write** because the server re-mints/rotates cookies into
`storage_state.json` (+ its `.lock`) — a read-only mount makes the session die
~1 h in. Running as your uid is what makes that write work without a chown.
(`make` fills your uid/gid from `id` automatically; for raw `docker compose`, set
`NOTEBOOKLM_UID`/`NOTEBOOKLM_GID` in `.env`.)

## 2. Configure secrets
```bash
cp deploy/.env.example deploy/.env
# NOTEBOOKLM_MCP_TOKEN: python -c "import secrets; print(secrets.token_urlsafe(32))"
# CF_TUNNEL_TOKEN: from the Cloudflare dashboard (next step)
```

## 3. Choose a tunnel
The stack ships two tunnel sidecars as Compose **profiles** — pick one (the server
runs under either). Both terminate TLS at their edge, so there's no cert to manage and
no host ports are published.

### 3A. Cloudflare Tunnel (needs a domain in your Cloudflare account)
In the Cloudflare **Zero Trust** dashboard → **Networks → Tunnels**:
1. Create a tunnel; copy its **token** into `CF_TUNNEL_TOKEN` in `.env`.
2. Add a **Public Hostname** (e.g. `notebooklm.yourdomain.com`) → **Service**
   `http://notebooklm-mcp:9420`. Cloudflare auto-creates the DNS record + serves TLS.
   Route the **whole host** (path `/`) — not a `/mcp`-scoped ingress — so the root
   OAuth routes are reachable. (Profile: `cloudflare`, the default.)

### 3B. Tailscale Funnel (NO domain — free, stable `*.ts.net` HTTPS)
Best when you don't own a domain: Tailscale Funnel gives a stable public HTTPS
hostname on Tailscale's domain, free on the personal plan, no DNS to manage.
**One-time tailnet setup** (admin console — these are policy/feature prerequisites, not
per-machine toggles):
1. Enable **MagicDNS** and **HTTPS certificates** for the tailnet
   (admin console → DNS; → HTTPS Certificates).
2. Grant the **`funnel` node attribute**: admin console → **Settings → General**, scroll
   to **Funnel** → **Manage** → **Node attributes** (tab, bottom-left) → **Add node
   attribute** → add `funnel`. The JSON preview shows:
   ```json
   { "target": ["*"], "attr": ["funnel"] }
   ```
3. Create a **normal auth key** (Settings → Keys) and put it in `.env` as `TS_AUTHKEY`.
   (There is no "Funnel-capable" key type — Funnel comes from the policy in step 2.)

Then the compose `tailscale` sidecar (profile `tailscale`) runs `tailscale/tailscale`
with `deploy/tailscale/funnel.json` (`TS_SERVE_CONFIG`), which funnels public `:443 /`
→ `notebooklm-mcp:9420` (so the OAuth routes at `/` AND `/mcp` are reachable). The node
is `TS_HOSTNAME=notebooklm-mcp`, so your public origin is
`https://notebooklm-mcp.<your-tailnet>.ts.net`.
> **Find `<your-tailnet>`** on the admin console **DNS** page — the **"Tailnet name"**
> shown there (e.g. `tailXXXXXX.ts.net`). After the sidecar is up you can also confirm
> the full URL with `docker compose --profile tailscale exec tailscale tailscale serve status`.
> Funnel only serves on ports 443/8443/10000 — the config uses **443**, so the URL has
> no port suffix. The serve config is bind-mounted as a **directory** (`./tailscale` →
> `/config`) per Tailscale's Docker requirement. Not live-verified in this repo —
> check `docker compose --profile tailscale logs tailscale` for the served URL on first run.

Then set the matching **OAuth base URL** in `.env` (bare origin — see step 6):
```
# Cloudflare:  NOTEBOOKLM_MCP_OAUTH_BASE_URL=https://notebooklm.yourdomain.com
# Tailscale:   NOTEBOOKLM_MCP_OAUTH_BASE_URL=https://notebooklm-mcp.<your-tailnet>.ts.net
```

## 4. Run

The `Makefile` wraps the build modes + the tunnel choice — one command each:

```bash
cd deploy
make dev                       # build THIS checkout + start (Cloudflare tunnel, default)
make dev TUNNEL=tailscale      # ...with the Tailscale Funnel sidecar instead
make prod VERSION=0.8.0        # build + install a published PyPI release and start
make logs                      # tail the server log (expect: bound 0.0.0.0:9420)
make restart                   # rebuild + recreate after a source/config change
make down                      # stop and remove (pass the same TUNNEL you started with)
```

Equivalent raw compose (`--profile` selects the tunnel; build context is the repo root):
- **Cloudflare, from source:** `docker compose --profile cloudflare up -d --build`
- **Tailscale, from source:** `docker compose --profile tailscale up -d --build`
- **From a published release:** add `--build-arg
  NOTEBOOKLM_SPEC="notebooklm-py[mcp,headless]==0.8.0"` to the build (or uncomment
  `build.args.NOTEBOOKLM_SPEC` in `docker-compose.yml`).

## 5. Connect from Claude Code
```bash
claude mcp add --transport http notebooklm \
  https://notebooklm-mcp.yourdomain.com/mcp \
  --header "Authorization: Bearer $NOTEBOOKLM_MCP_TOKEN"
```
Claude **Desktop** also accepts the bearer. Claude **.ai** (web/mobile) does not —
its connector UI is OAuth-only — so use step 6 for it.

## 6. (Optional) Connect from claude.ai — self-hosted OAuth (one password)
claude.ai's connector UI has no bearer field; it speaks OAuth. Instead of an external
IdP, the server runs its own tiny OAuth authorization server gated by **one password**.
**Opt-in and additive** — leave both vars unset to stay bearer-only (Claude Code/Desktop
unaffected); when set, the bearer and OAuth work side by side on the same `/mcp`.

1. **`.env`** — set a strong password + your public URL (see `.env.example`):
   ```
   # a long random secret — the gate (>=16 chars):
   NOTEBOOKLM_MCP_OAUTH_PASSWORD=$(python -c "import secrets;print(secrets.token_urlsafe(24))")
   # the BARE public https origin — NOT the /mcp connector URL (the OAuth endpoints
   # /authorize, /token, /register, /login, /.well-known/* mount at the ROOT):
   NOTEBOOKLM_MCP_OAUTH_BASE_URL=https://notebooklm.example.com
   ```
   `make dev` (or `make prod VERSION=…`). Both required together — partial/weak/
   non-https/has-a-path config refuses to start.
2. **Cloudflare tunnel** — the Public Hostname must route the WHOLE host (path `/`,
   not a `/mcp`-scoped ingress) to `http://notebooklm-mcp:9420`, so the root OAuth
   routes are reachable. (The `notebooklm.` subdomain is created automatically when you
   add the Public Hostname; the zone just has to be in your Cloudflare account.)
3. **Verify** (after `make dev` + the tunnel is up):
   ```
   curl https://notebooklm.example.com/.well-known/oauth-authorization-server
   ```
   `issuer` should be your bare origin and `authorization_endpoint` should be
   `…/authorize` (at the root). If they show `…/mcp/authorize`, your BASE_URL has the
   `/mcp` path — drop it.
4. **claude.ai → Settings → Connectors → Add custom connector** → the URL **WITH** `/mcp`:
   `https://notebooklm.example.com/mcp`. claude.ai registers itself (DCR), then opens the
   server's **password page** in your browser; enter the password → you're connected.
   Claude Code keeps using the bearer.

   > **base URL vs connector URL:** `NOTEBOOKLM_MCP_OAUTH_BASE_URL` is the bare origin
   > (`https://host`); the claude.ai connector URL is that **+ `/mcp`**.

> **What it does NOT need vs an IdP:** no dashboard, no JWT template, no audience/email
> config — the password is the whole identity. Registered clients + tokens **persist**
> across restarts in `oauth_state.json` under the mounted profile, so a redeploy doesn't
> force re-login. **Treat `oauth_state.json` as a full-account secret** (it holds
> long-lived OAuth tokens — same tier as `master_token.json`).
> **Honest trade:** because the login page is served through your tunnel, **Cloudflare's
> edge sees the password in transit** (it terminates TLS) — use a throwaway Google
> account. Note: rotating the password does **not** revoke already-issued OAuth tokens
> (they're long-lived + persisted); **real revocation = delete `oauth_state.json` and
> restart**. (To remove Cloudflare from the path, self-host TLS instead.)

## Notes & security
- **Two auth layers.** The `NOTEBOOKLM_MCP_TOKEN` bearer gates *who can use the
  endpoint*; the master token authenticates *the server to Google*. The master
  token **never** traverses the tunnel — only MCP tool calls/results do. The
  bearer **does** terminate at Cloudflare (Cloudflare can see it in transit, like
  any reverse-proxied request), so rotate it freely.
- **Fail-closed.** The server refuses to start on a non-loopback bind with no auth
  at all (neither `NOTEBOOKLM_MCP_TOKEN` nor self-hosted OAuth), and refuses
  partial/weak/non-https OAuth config.
- **One container per account.** Do not scale replicas off one master token —
  concurrent re-mints invalidate each other's session.
- **Rotate the bearer**: change `NOTEBOOKLM_MCP_TOKEN` in `.env`,
  `docker compose up -d`, and update the `claude mcp add` header.
- **Files**: the connector moves text/references only. Add device files via
  Google Drive (`source_add` with a Drive id) or the NotebookLM app; consume
  generated podcasts/videos/slides in the NotebookLM app (same account).
- **Optional hardening**: instead of a single `rw` bind-mount, mount
  `master_token.json` as a separate read-only Docker secret and use a writable
  named volume for `storage_state.json` + `.storage_state.json.lock`.
