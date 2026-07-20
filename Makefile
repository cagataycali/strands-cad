# strands-cad — canonical entrypoint. Run `make help` for the menu.
# Prompt-to-print printer cockpit: dashboard + slow-thinker + telegram bridge,
# runnable as a local venv, durable systemd services, or docker containers.

SHELL       := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

VENV ?= .venv
PY   ?= $(VENV)/bin/python
PIP  ?= $(VENV)/bin/pip

CURDIR   := $(shell pwd)
ABS_VENV := $(CURDIR)/$(VENV)
ABS_ENV  := $(CURDIR)/.env
USER_ID  := $(shell id -un)

DASH_PORT ?= 8099

.PHONY: help
help: ## show this menu
	@echo "🔧 strands-cad — printer cockpit"
	@awk -F':|##' '/^[a-zA-Z0-9_-]+:.*##/ {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$NF}' $(MAKEFILE_LIST)

# ============================================================================
# 📦 Local (venv) — dev / bare-metal
# ============================================================================
.PHONY: install
install: $(VENV)/.installed ## create venv + install strands-cad (editable)

$(VENV)/.installed: pyproject.toml
	@if command -v uv >/dev/null 2>&1; then 	  test -d $(VENV) || uv venv $(VENV); 	  VIRTUAL_ENV=$(ABS_VENV) uv pip install -e ".[dashboard]" ; 	else 	  test -d $(VENV) || $(shell command -v python3.12 || command -v python3.11 || command -v python3) -m venv $(VENV); 	  $(PIP) install --quiet --upgrade pip; 	  $(PIP) install --quiet -e ".[dashboard]" ; 	fi
	@touch $@
	@echo "✅ venv ready ($(VENV))"
	@test -f .env || { test -f .env-example && cp .env-example .env && echo "📝 created .env from .env-example — edit it!"; } || true

.PHONY: run dashboard
run: dashboard ## alias for 'dashboard'
dashboard: install ## start the dashboard (:$(DASH_PORT)) — runs thinker+telegram in-process
	@echo "🖥️  dashboard → http://localhost:$(DASH_PORT)"
	@set -a; [ -f .env ] && . ./.env; set +a; $(ABS_VENV)/bin/strands-cad-dashboard --no-auth

.PHONY: thinker
thinker: install ## start ONLY the slow-thinker loop (standalone)
	@set -a; [ -f .env ] && . ./.env; set +a; $(ABS_VENV)/bin/strands-cad-thinker

.PHONY: telegram
telegram: install ## start ONLY the telegram bridge (standalone)
	@set -a; [ -f .env ] && . ./.env; set +a; $(ABS_VENV)/bin/strands-cad-telegram

.PHONY: mcp
mcp: install ## run the MCP server (stdio) for editor/agent integration
	@$(ABS_VENV)/bin/strands-cad-mcp

.PHONY: test
test: install ## run the test suite
	@$(PY) -m pytest tests/ -q -o addopts="" || { command -v uv >/dev/null 2>&1 && VIRTUAL_ENV=$(ABS_VENV) uv pip install -q pytest && $(PY) -m pytest tests/ -q -o addopts=""; }

.PHONY: slicer
slicer: install ## install/locate a slicer (OrcaSlicer/PrusaSlicer)
	@$(ABS_VENV)/bin/strands-cad-install-slicer || true

.PHONY: clean
clean: ## remove venv + caches
	@rm -rf $(VENV) **/__pycache__ .pytest_cache *.egg-info
	@echo "🧹 cleaned"

# ============================================================================
# 🔧 systemd — durable services (dashboard + thinker + telegram) on Linux.
# `make persist` renders deploy/*.service with THIS dir + venv baked in, then
# enables them as --user units (survive logout with `loginctl enable-linger`).
# ============================================================================
SD_DIR   := $(HOME)/.config/systemd/user
UNITS    := strands-cad-dashboard strands-cad-thinker strands-cad-telegram
DEPLOY   := $(CURDIR)/deploy

.PHONY: persist
persist: install ## install + start all services as durable systemd --user units
	@mkdir -p "$(SD_DIR)"
	@for u in $(UNITS); do \
	  sed -e 's#@INSTALL_DIR@#$(CURDIR)#g' \
	      -e 's#@VENV@#$(ABS_VENV)#g' \
	      -e 's#@USER@#$(USER_ID)#g' \
	      "$(DEPLOY)/$$u.service" > "$(SD_DIR)/$$u.service"; \
	  echo "📝 wrote $(SD_DIR)/$$u.service"; \
	done
	@cp "$(DEPLOY)/strands-cad.target" "$(SD_DIR)/strands-cad.target"
	@systemctl --user daemon-reload
	@systemctl --user enable --now $(UNITS)
	@echo "🚀 strands-cad persisted. (run 'loginctl enable-linger $(USER_ID)' for boot w/o login)"
	@echo "   dashboard → http://localhost:$(DASH_PORT)"

.PHONY: unpersist
unpersist: ## stop + remove all durable systemd services
	@systemctl --user disable --now $(UNITS) 2>/dev/null || true
	@for u in $(UNITS); do rm -f "$(SD_DIR)/$$u.service" && echo "🗑  removed $$u"; done
	@rm -f "$(SD_DIR)/strands-cad.target"
	@systemctl --user daemon-reload
	@echo "✅ strands-cad unpersisted"

.PHONY: persist-restart
persist-restart: ## restart all durable services (e.g. after editing .env)
	@systemctl --user restart $(UNITS)
	@echo "🔄 restarted: $(UNITS)"

.PHONY: persist-status status
status: persist-status
persist-status: ## show status of durable services
	@systemctl --user status $(UNITS) --no-pager || true

.PHONY: persist-logs logs
logs: persist-logs
persist-logs: ## tail logs of all durable services
	@journalctl --user $(addprefix -u ,$(UNITS)) -n 50 -f

# ============================================================================
# 🐳 Docker — full stack in containers (dashboard + thinker + telegram).
# Slicing runs in the separate `orcaslicer` sidecar image (docker/orcaslicer).
# ============================================================================
COMPOSE ?= docker compose

.PHONY: docker-build
docker-build: ## build the strands-cad app image
	@$(COMPOSE) build

.PHONY: docker-slicer-build
docker-slicer-build: ## build the OrcaSlicer sidecar image (headless CLI)
	@docker build -t strands-cad/orcaslicer:2.5.0 -t strands-cad-orcaslicer:latest docker/orcaslicer
	@echo "✅ strands-cad/orcaslicer:2.5.0 built — slice_bambu auto-uses it (STRANDS_CAD_SLICER_DOCKER)"

.PHONY: docker-up
docker-up: ## start dashboard only (:$(DASH_PORT))
	@$(COMPOSE) up -d dashboard
	@echo "🖥️  dashboard → http://localhost:$(DASH_PORT)"

.PHONY: docker-up-all
docker-up-all: ## start EVERYTHING (dashboard + thinker + telegram)
	@$(COMPOSE) --profile all up -d
	@echo "🖥️  full stack up → http://localhost:$(DASH_PORT)"

.PHONY: docker-down
docker-down: ## stop + remove the stack
	@$(COMPOSE) --profile all down

.PHONY: docker-restart
docker-restart: ## restart the stack (e.g. after editing .env)
	@$(COMPOSE) --profile all restart

.PHONY: docker-logs
docker-logs: ## tail logs of all running containers
	@$(COMPOSE) --profile all logs -f

.PHONY: docker-ps
docker-ps: ## show container status
	@$(COMPOSE) --profile all ps

.PHONY: docker-shell
docker-shell: ## open a bash shell in a fresh app container
	@$(COMPOSE) run --rm --entrypoint bash dashboard

# ── OrcaSlicer container (reproducible Bambu-flavored slicer) ──────────────
ORCA_IMAGE ?= strands-cad/orcaslicer:2.5.0
ORCA_PKG   := docker/orcaslicer/orcaslicer-package.tar.gz

.PHONY: orca-image orca-package orca-test
orca-package:  ## Package the host-built OrcaSlicer into the docker build context
	@test -d $$HOME/.local/share/OrcaSlicer/bin || \
	  { echo "No host OrcaSlicer build at ~/.local/share/OrcaSlicer — build it first (see docker/orcaslicer/README.md)"; exit 1; }
	cd $$HOME/.local/share/OrcaSlicer && tar czf $(CURDIR)/$(ORCA_PKG) .
	@echo "packaged → $(ORCA_PKG)"

orca-image: ## Build the OrcaSlicer docker image (needs orca-package first)
	@test -f $(ORCA_PKG) || $(MAKE) orca-package
	docker build -t $(ORCA_IMAGE) docker/orcaslicer

orca-test: ## Smoke-test the OrcaSlicer image (slices a cube, checks Bambu markers)
	@rm -rf /tmp/orca_smoke && mkdir -p /tmp/orca_smoke && cp examples/props/cube_30.stl /tmp/orca_smoke/ 2>/dev/null || cp tests/fixtures/*.stl /tmp/orca_smoke/cube_30.stl
	docker run --rm --user $$(id -u):$$(id -g) -v /tmp/orca_smoke:/work $(ORCA_IMAGE) \
	  --load-settings "/opt/orcaslicer/resources/profiles/BBL/machine/Bambu Lab X2D 0.4 nozzle.json;/opt/orcaslicer/resources/profiles/BBL/process/0.20mm Standard @BBL X2D.json" \
	  --load-filaments "/opt/orcaslicer/resources/profiles/BBL/filament/Bambu PLA Basic @BBL X2D 0.4 nozzle.json" \
	  --slice 0 --outputdir /work /work/cube_30.stl
	@grep -q HEADER_BLOCK_START /tmp/orca_smoke/plate_1.gcode && echo "✅ Bambu gcode OK" || { echo "❌ missing Bambu markers"; exit 1; }
