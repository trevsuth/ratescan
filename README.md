# RateScan

RateScan extracts **structured utility rate schedules** from **text-based PDF tariff documents** using **self-hosted LLMs (Ollama, CPU-only)**. It is designed for traceable, reproducible extractions where every extracted field is backed by a source snippet.

## Status

This repo is early-stage. The current working implementation is the **POC pipeline** in `poc/poc_extract.py`. The API/worker/frontend services described in `docs/architecture.md` are planned but not yet implemented in this repository.

## What’s Here

- `poc/poc_extract.py`: End-to-end proof of concept that ingests a PDF, detects likely schedule pages, calls Ollama, validates output, and writes artifacts to MongoDB.
- `scripts/ollama_generate.py`: Small CLI helper for calling Ollama’s `/api/generate` endpoint.
- `docs/architecture.md`: The intended production architecture and data flow.
- `data/documents/`: Sample PDF(s) for development.
- `justfile`: Operational recipes for local infra and POC runs.

## Requirements

- Python `>= 3.13`
- Docker + Docker Compose v2
- Ollama (via Docker) for local CPU inference
- MongoDB (via Docker)
- Optional but recommended: `uv` and `just`

## Quick Start (POC)

1. Start POC infrastructure (Mongo, NATS, Ollama):

```bash
just poc-up
```

2. Pull an Ollama model (example):

```bash
just ollama-pull qwen2.5:7b-instruct
```

3. Run the POC against the sample PDF:

```bash
just poc
```

This writes documents to MongoDB and logs extraction details to stdout.

## POC Environment Variables

- `MONGO_URI` (default `mongodb://localhost:27017/ratescan`)
- `OLLAMA_URL` (default `http://localhost:11434`)
- `OLLAMA_MODEL` (default `qwen2.5:7b-instruct`)
- `UTILITY_NAME` (default `unknown_utility`)
- `LOG_LEVEL` (default `INFO`)

## Useful Recipes

- `just poc` – Run the POC on the sample PDF
- `just poc-file <path>` – Run the POC on a custom PDF
- `just ollama-list` – List installed Ollama models
- `just logs-ollama` – Tail Ollama logs

Run `just` with no args to see the full recipe list.

## Notes on Docker Compose

`docker-compose.yaml` currently references `./backend` and `./frontend`, but those directories are not present yet in this repository. The Compose file reflects the intended architecture and will become valid once those services exist.

## Documentation

See `docs/architecture.md` for the detailed system design, data flow, and queue model.

## License

TBD.
