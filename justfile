# justfile — ratescan
#
# Behavior:
# - Running `just` with no args prints the full recipe list (with descriptions).
# - Each recipe has an annotation comment that appears in `just --list`.
#
# Assumptions:
# - Docker Compose v2: `docker compose ...`
# - docker-compose.yml is in repo root
# - Services: mongo, nats, ollama, api, worker, frontend

set dotenv-load := true
set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# -------- Config --------
compose := "docker compose"
project := env_var_or_default("COMPOSE_PROJECT_NAME", "ratescan")

# Hosts/ports (override via .env if you want)
API_BASE := env_var_or_default("API_BASE", "http://localhost:8000")
NATS_MONITOR := env_var_or_default("NATS_MONITOR", "http://localhost:8222")
OLLAMA_BASE := env_var_or_default("OLLAMA_BASE", "http://localhost:11434")
MONGO_HOST := env_var_or_default("MONGO_HOST", "localhost")
MONGO_PORT := env_var_or_default("MONGO_PORT", "27017")

# Show all recipes (with annotations) when running `just` alone
default:
  @just --list

# -------------------------
# Lifecycle
# -------------------------

# Pull images, build local services, and start the full stack (recommended first run)
setup:
  @echo "==> Pulling images..."
  {{compose}} pull
  @echo "==> Building local images..."
  {{compose}} build
  @echo "==> Starting stack..."
  {{compose}} up -d
  @echo "==> Done."

# Start the stack in detached mode
up:
  {{compose}} up -d

# Stop and remove containers (preserves volumes)
down:
  {{compose}} down

# Stop and remove containers AND volumes (DELETES Mongo + NATS + Ollama data)
nuke:
  {{compose}} down -v

# Restart the stack (down + up)
restart:
  {{compose}} down
  {{compose}} up -d

# Rebuild images (no start)
build:
  {{compose}} build

# Pull latest images (no build)
pull:
  {{compose}} pull

# Show running services and status
ps:
  {{compose}} ps

# -------------------------
# Logs
# -------------------------

# Tail logs for all services (follow)
logs:
  {{compose}} logs -f --tail=200

# Tail logs for a specific service (follow): `just logs-service api`
logs-service service:
  {{compose}} logs -f --tail=200 {{service}}

# Print a log snapshot for a specific service (no follow): `just logs-once worker`
logs-once service:
  {{compose}} logs --tail=400 {{service}}

# Tail API logs
logs-api:
  {{compose}} logs -f --tail=200 api

# Tail worker logs
logs-worker:
  {{compose}} logs -f --tail=200 worker

# Tail NATS logs
logs-nats:
  {{compose}} logs -f --tail=200 nats

# Tail Mongo logs
logs-mongo:
  {{compose}} logs -f --tail=200 mongo

# Tail Ollama logs
logs-ollama:
  {{compose}} logs -f --tail=200 ollama

# Tail frontend logs
logs-frontend:
  {{compose}} logs -f --tail=200 frontend

# -------------------------
# Connectivity checks
# -------------------------

# Run all connectivity checks (Mongo, NATS, JetStream, Ollama, API)
check: check-mongo check-nats check-jetstream check-ollama check-api
  @echo "==> All checks passed."

# Ping MongoDB (uses host mongosh if available, else execs inside mongo container)
check-mongo:
  @echo "==> Checking MongoDB..."
  @command -v mongosh >/dev/null 2>&1 || { \
    echo "mongosh not found on host. Trying via container..."; \
    {{compose}} exec -T mongo mongosh --quiet --eval 'db.runCommand({ ping: 1 })' >/dev/null; \
    echo "MongoDB OK (via container)"; \
    exit 0; \
  }
  @mongosh "mongodb://{{MONGO_HOST}}:{{MONGO_PORT}}/admin" --quiet --eval 'db.runCommand({ ping: 1 })' >/dev/null
  @echo "MongoDB OK"

# Check NATS monitoring endpoint (/varz)
check-nats:
  @echo "==> Checking NATS monitoring endpoint..."
  @curl -fsS "{{NATS_MONITOR}}/varz" >/dev/null
  @echo "NATS OK"

# Verify JetStream is enabled (/jsz shows enabled:true)
check-jetstream:
  @echo "==> Checking JetStream..."
  @curl -fsS "{{NATS_MONITOR}}/jsz" | grep -q '"enabled"[[:space:]]*:[[:space:]]*true' || { \
    echo "JetStream not enabled or /jsz unavailable"; exit 1; \
  }
  @echo "JetStream OK"

# Verify Ollama responds (/api/tags)
check-ollama:
  @echo "==> Checking Ollama..."
  @curl -fsS "{{OLLAMA_BASE}}/api/tags" >/dev/null
  @echo "Ollama OK"

# Verify API responds (expects GET /healthz)
check-api:
  @echo "==> Checking API..."
  @curl -fsS "{{API_BASE}}/healthz" >/dev/null
  @echo "API OK"

# -------------------------
# Debug helpers
# -------------------------

# Open a shell in a running service container: `just sh api`
sh service:
  {{compose}} exec {{service}} bash

# Show recent docker compose events (last 30 minutes)
events:
  {{compose}} events --since 30m

# Print the resolved compose config
config:
  {{compose}} config

# Curl the API (path defaults to "/"): `just curl-api /openapi.json`
curl-api path="/":
  @curl -fsS "{{API_BASE}}{{path}}" | sed -e 's/^/API: /'

# Curl the NATS monitoring endpoint (path defaults to "/varz"): `just curl-nats /jsz`
curl-nats path="/varz":
  @curl -fsS "{{NATS_MONITOR}}{{path}}" | sed -e 's/^/NATS: /'

# Curl the Ollama endpoint (path defaults to "/api/tags")
curl-ollama path="/api/tags":
  @curl -fsS "{{OLLAMA_BASE}}{{path}}" | sed -e 's/^/OLLAMA: /'

# Wait for core services to come up (polls NATS + Ollama)
wait:
  @echo "==> Waiting for services..."
  @for i in {1..60}; do \
    if curl -fsS "{{NATS_MONITOR}}/varz" >/dev/null 2>&1 && \
       curl -fsS "{{OLLAMA_BASE}}/api/tags" >/dev/null 2>&1; then \
      echo "Core services are up."; exit 0; \
    fi; \
    sleep 1; \
  done; \
  echo "Timed out waiting for services."; exit 1

# Rebuild and restart everything (down -> build -> up)
reup:
  {{compose}} down
  {{compose}} build
  {{compose}} up -d

# -------------------------
# POC
# -------------------------

# Run POC (uses canonical PDF path)
poc:
  uv run python poc/poc_extract.py data/documents/LGE-Electric-Rates-010126.pdf

# Run POC with a custom PDF path (usage: just poc-file documents/foo.pdf)
poc-file pdf:
  uv run python poc/poc_extract.py {{pdf}}

# Bring up ONLY the infrastructure needed for the POC (mongo, nats, ollama)
poc-up:
  @echo "==> Starting POC infrastructure (mongo, nats, ollama)..."
  {{compose}} up -d mongo nats ollama
  @echo "==> POC infrastructure is up."

# Stop POC infrastructure containers (preserves volumes)
poc-down:
  @echo "==> Stopping POC infrastructure..."
  {{compose}} stop mongo nats ollama


# Stop POC infra and delete volumes (DESTRUCTIVE)
poc-reset:
  @echo "==> Resetting POC infrastructure (volumes will be deleted)..."
  {{compose}} down -v mongo nats ollama
# -------------------------
# Ollama model management
# -------------------------

# Pull an Ollama model into the ollama volume (usage: just ollama-pull qwen2.5:7b-instruct)
ollama-pull model:
  @echo "==> Pulling Ollama model: {{model}}"
  {{compose}} exec -T ollama ollama pull {{model}}

# List installed Ollama models (in the ollama volume)
ollama-list:
  @echo "==> Listing Ollama models..."
  {{compose}} exec -T ollama ollama list

# Show details for a single Ollama model (usage: just ollama-show qwen2.5:7b-instruct)
ollama-show model:
  @echo "==> Showing Ollama model: {{model}}"
  {{compose}} exec -T ollama ollama show {{model}}

# Remove an Ollama model from the volume (usage: just ollama-rm qwen2.5:7b-instruct)
ollama-rm model:
  @echo "==> Removing Ollama model: {{model}}"
  {{compose}} exec -T ollama ollama rm {{model}}

# Force-remove an Ollama model (if supported by your ollama version) (usage: just ollama-rm-force qwen2.5:7b-instruct)
ollama-rm-force model:
  @echo "==> Force-removing Ollama model: {{model}}"
  {{compose}} exec -T ollama ollama rm --force {{model}}

# Query the Ollama HTTP API for installed models (requires curl on host)
ollama-tags:
  @echo "==> GET {{OLLAMA_BASE}}/api/tags"
  @curl -fsS "{{OLLAMA_BASE}}/api/tags" | sed -e 's/^/OLLAMA: /'

# Quick “smoke” generation test against the Ollama HTTP API
# Usage: just ollama-generate qwen2.5:7b-instruct "Hello world"
ollama-generate model prompt:
  python scripts/ollama_generate.py "{{model}}" "{{prompt}}"

# -------------------------
# NATS / JetStream utilities
# -------------------------

# Show NATS server info (monitoring endpoint)
nats-varz:
  @echo "==> GET {{NATS_MONITOR}}/varz"
  @curl -fsS "{{NATS_MONITOR}}/varz" | sed -e 's/^/NATS: /'

# Show JetStream status (monitoring endpoint)
nats-jsz:
  @echo "==> GET {{NATS_MONITOR}}/jsz"
  @curl -fsS "{{NATS_MONITOR}}/jsz" | sed -e 's/^/NATS: /'

# Open an interactive nats-box shell on the compose network
# Inside, try:
#   nats --server nats:4222 stream ls
#   nats --server nats:4222 consumer ls RATESCAN_JOBS
nats-box:
  @echo "==> Starting nats-box (interactive)."
  @echo "Tip: run 'nats --server nats:4222 stream ls' inside."
  docker run --rm -it --network "{{project}}_default" natsio/nats-box:latest

# List JetStream streams (non-interactive, via nats-box)
nats-streams:
  @echo "==> Listing JetStream streams..."
  docker run --rm --network "{{project}}_default" natsio/nats-box:latest \
    nats --server nats:4222 stream ls

# Show details for a stream (usage: just nats-stream-info RATESCAN_JOBS)
nats-stream-info stream:
  @echo "==> Stream info: {{stream}}"
  docker run --rm --network "{{project}}_default" natsio/nats-box:latest \
    nats --server nats:4222 stream info {{stream}}

# List consumers for a stream (usage: just nats-consumers RATESCAN_JOBS)
nats-consumers stream:
  @echo "==> Consumers for stream: {{stream}}"
  docker run --rm --network "{{project}}_default" natsio/nats-box:latest \
    nats --server nats:4222 consumer ls {{stream}}

# Show consumer info (usage: just nats-consumer-info RATESCAN_JOBS C_EXTRACT)
nats-consumer-info stream consumer:
  @echo "==> Consumer info: {{stream}} / {{consumer}}"
  docker run --rm --network "{{project}}_default" natsio/nats-box:latest \
    nats --server nats:4222 consumer info {{stream}} {{consumer}}

# -------------------------
# Mongo utilities
# -------------------------

# Open an interactive mongosh inside the mongo container
mongo-shell:
  @echo "==> Opening mongosh (inside container)..."
  {{compose}} exec mongo mongosh

# Quick Mongo ping (container)
mongo-ping:
  @echo "==> Pinging MongoDB..."
  {{compose}} exec -T mongo mongosh --quiet --eval 'db.runCommand({ ping: 1 })' >/dev/null
  @echo "MongoDB OK"

# List databases
mongo-dbs:
  {{compose}} exec -T mongo mongosh --quiet --eval 'db.adminCommand("listDatabases")'

# -------------------------
# Per-container convenience
# -------------------------

# Shell into API container
sh-api:
  {{compose}} exec api bash

# Shell into worker container
sh-worker:
  {{compose}} exec worker bash

# Shell into frontend container
sh-frontend:
  {{compose}} exec frontend sh

# Shell into ollama container
sh-ollama:
  {{compose}} exec ollama bash

# Shell into nats container
sh-nats:
  {{compose}} exec nats sh

# Restart an individual service (usage: just restart-service worker)
restart-service service:
  {{compose}} restart {{service}}

# Stop an individual service (usage: just stop-service worker)
stop-service service:
  {{compose}} stop {{service}}

# Start an individual service (usage: just start-service worker)
start-service service:
  {{compose}} start {{service}}

