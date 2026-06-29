# Convenience wrapper for the remote notebooklm-mcp deploy.
#   make dev                    — build this checkout + start (Cloudflare tunnel, default)
#   make dev TUNNEL=tailscale   — same, but with the Tailscale Funnel sidecar (no domain)
#   make prod VERSION=x.y.z     — build + install a published PyPI release
#   make logs / restart / down / config
# TUNNEL selects the tunnel sidecar (Compose profile): cloudflare (default) or tailscale.
# Override the compose command with COMPOSE=... if needed.

COMPOSE ?= docker compose
TUNNEL ?= cloudflare
VERSION ?= 0.8.0

# Profile-aware compose invocation — selects exactly one tunnel sidecar.
DC = $(COMPOSE) --profile $(TUNNEL)

# Run the container as YOUR uid/gid so the mounted profile needs no chown, and
# default the mounted profile to your local "default" profile. All overridable
# (env or `make dev NOTEBOOKLM_PROFILE_DIR=...`); exported so compose sees them.
export NOTEBOOKLM_UID ?= $(shell id -u)
export NOTEBOOKLM_GID ?= $(shell id -g)
export NOTEBOOKLM_PROFILE_DIR ?= $(HOME)/.notebooklm/profiles/default

.PHONY: help dev prod up restart logs down config _require-tunnel-token

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-9s\033[0m %s\n", $$1, $$2}'

# Friendly pre-check for the chosen tunnel's token (shell env OR deploy/.env). Compose
# can't do a profile-scoped required-var (it interpolates the whole file before profile
# filtering), so the guard lives here instead of a `:?` in docker-compose.yml.
_require-tunnel-token:
	@if [ "$(TUNNEL)" = "cloudflare" ]; then \
		{ [ -n "$$CF_TUNNEL_TOKEN" ] || grep -qsE '^CF_TUNNEL_TOKEN=.+' .env; } \
			|| { echo "✗ TUNNEL=cloudflare needs CF_TUNNEL_TOKEN — set it in deploy/.env"; exit 1; }; \
	elif [ "$(TUNNEL)" = "tailscale" ]; then \
		{ [ -n "$$TS_AUTHKEY" ] || grep -qsE '^TS_AUTHKEY=.+' .env; } \
			|| { echo "✗ TUNNEL=tailscale needs TS_AUTHKEY — set it in deploy/.env"; exit 1; }; \
	else echo "✗ Unknown TUNNEL=$(TUNNEL) (use cloudflare or tailscale)"; exit 1; fi

dev: _require-tunnel-token ## Build from source (this checkout) and start (TUNNEL=cloudflare|tailscale)
	$(DC) up -d --build

prod: _require-tunnel-token ## Build from a published release and start (e.g. make prod VERSION=0.8.0)
	$(DC) build --build-arg NOTEBOOKLM_SPEC="notebooklm-py[mcp,headless]==$(VERSION)"
	$(DC) up -d

up: dev ## Alias for `dev`

restart: ## Rebuild + recreate after a source/config change
	$(DC) up -d --build

logs: ## Tail the server logs
	$(COMPOSE) logs -f notebooklm-mcp

down: ## Stop and remove the stack (pass the same TUNNEL you started with)
	$(DC) down

config: ## Validate the merged compose config
	$(DC) config
