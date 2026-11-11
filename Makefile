SHELL := /bin/bash
.DEFAULT_GOAL := help
export PATH := $(HOME)/.local/bin:$(PATH)
export NODE_OPTIONS ?= --max-old-space-size=768
UV ?= uv
COMPOSE := docker compose -f deploy/docker-compose.yml --env-file .env

.PHONY: help env sync protocol protocol-check ui admin build test lint fmt typecheck dev-brain dev-ui dev-node up down logs install-pi demo clean

help: ## Show targets
	@grep -hE "^[a-zA-Z_-]+:.*?## " $(MAKEFILE_LIST) | awk -F ":.*?## " "{printf \"  \\033[36m%-16s\\033[0m %s\\n\", \$$1, \$$2}"

env: ## Create .env with generated secrets (never overwrites existing values)
	python3 scripts/gen-env.py

sync: ## Install Python + JS dependencies
	$(UV) sync --all-packages
	pnpm install

protocol: ## Regenerate TS types from the pydantic protocol
	$(UV) run python packages/protocol/scripts/export_schema.py packages/protocol/schema/events.schema.json
	pnpm --filter @jarvis/protocol gen

protocol-check: protocol ## Fail if generated protocol files are stale
	@if [ -n "$$(git status --porcelain -- packages/protocol/schema packages/protocol/ts)" ]; then \
		git status --short -- packages/protocol; \
		echo "protocol types are stale: run 'make protocol' and commit"; exit 1; fi

ui: ## Build the kiosk UI
	pnpm --filter @jarvis/ui build

admin: ## Build the admin UI
	pnpm --filter @jarvis/admin build

build: ui admin ## Build all frontends

test: ## Run all tests
	$(UV) run pytest -q
	pnpm -r --if-present test

lint: ## Lint Python + typecheck TS
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	pnpm -r --if-present typecheck

fmt: ## Auto-format Python
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

dev-brain: ## Run the brain locally (reload on change)
	$(UV) run --package jarvis-brain jarvis-brain

dev-ui: ## Vite dev server for the kiosk UI
	pnpm --filter @jarvis/ui dev

dev-node: ## Run the audio node in the foreground
	$(UV) run --package jarvis-audio-node jarvis-audio-node

up: ## Start the brain (docker compose if available, else native user service)
	@test -f .env || python3 scripts/gen-env.py
	@if command -v docker >/dev/null 2>&1; then $(COMPOSE) up -d --build; \
	else echo "docker not found: running the brain natively"; BRAIN=1 scripts/install-brain-native.sh; fi

down: ## Stop the brain
	@if command -v docker >/dev/null 2>&1; then $(COMPOSE) down; else systemctl --user stop jarvis-brain; fi

logs: ## Follow logs of all JARVIS services
	@if command -v docker >/dev/null 2>&1; then $(COMPOSE) logs -f; \
	else journalctl --user -f -u "jarvis-*"; fi

install-pi: ## Install audio node + kiosk on this Pi (BRAIN=1 to also run the brain here)
	scripts/install-pi.sh

demo: ## Replay recorded audio through the full pipeline
	$(UV) run --package jarvis-audio-node python -m jarvis_audio.demo

clean: ## Remove build outputs
	rm -rf apps/*/dist .pytest_cache .ruff_cache
