# Architecture Overview — RateScan

## Purpose

RateScan is a system for extracting **structured utility rate schedule data** from **text-based PDF tariff documents** using **self-hosted LLMs (Ollama, CPU-only)**.

The system prioritizes:
- **Traceability** (every extracted value is cited to source text)
- **Reproducibility** (versioned prompts, schemas, and artifacts)
- **Replaceability** (especially the worker, which may be rewritten in a compiled language)
- **Operational simplicity** (Docker Compose, minimal infrastructure)

This document describes the **current architectural design** and the reasoning behind it.

---

## High-Level Architecture

RateScan is composed of **five logical components**, each running in its own container:

- **Frontend** — User interface for uploading PDFs, monitoring jobs, and reviewing extracted rate schedules and citations.
- **API** — Orchestrates ingestion, persistence, job submission, and querying of results.
- **NATS JetStream** — Durable pub/sub system used as a work queue for background jobs.
- **Worker** — Executes ingestion and extraction jobs, calls the LLM, and writes artifacts.
- **MongoDB** — System of record for documents, schedules, extracted text, extractions, and job state.
- **Ollama** — Local, CPU-only LLM inference runtime.

---

## Core Design Principles

### 1. Contract-First, Message-Driven

- The API and worker **never call each other directly**.
- All coordination happens via:
  - **JetStream messages** (jobs and optional events)
  - **MongoDB documents** (artifacts and job status)
- This ensures the worker can be replaced (e.g., Python → Go/Rust) without changing the API or frontend.

### 2. LLMs Are Used Surgically

- LLMs are responsible only for **semantic interpretation** of human-written tariff text.
- Deterministic code handles:
  - PDF text extraction
  - page boundary detection
  - schema validation
  - normalization and persistence
- Hallucinations are mitigated by:
  - strict, versioned JSON schemas
  - mandatory citations for all non-null fields
  - returning `null` when information is absent or ambiguous

### 3. Evidence Is First-Class

- Every extracted value must include:
  - the page number(s)
  - a verbatim source snippet
- MongoDB is treated as an **audit log**, not a cache.
- The system favors traceability and correctness over completeness.

---

## Directory Structure (Conceptual)

```text
ratescan/
├── api/            # FastAPI service (HTTP + orchestration)
├── worker/         # Background job executor (replaceable)
├── core/           # Shared domain logic (PDF, LLM, schemas, repos)
├── frontend/       # Non-Streamlit UI (Next.js or similar)
├── poc/            # Single-script proof of concept
├── contracts/      # Versioned JSON schemas (job + artifact contracts)
├── docs/           # Architecture & design docs
├── documents/      # Local PDF inputs (development only)
├── docker-compose.yaml
└── justfile
```

---

## Data Flow (Happy Path)

1. **PDF Ingestion**
   - A PDF is uploaded via the API or read from disk (development).
   - Text is extracted per page.
   - Document metadata and page text are stored in MongoDB.

2. **Schedule Boundary Detection**
   - Heuristics identify candidate page ranges likely containing rate schedules.
   - Each range becomes a `rate_schedule` record.

3. **Rate Text Assembly**
   - Pages within a schedule’s boundaries are concatenated.
   - The assembled text is stored as `rate_text` (raw and cleaned variants).

4. **Job Submission**
   - The API publishes an `extract` job to JetStream.
   - Job metadata is also recorded in MongoDB for frontend visibility.

5. **Extraction**
   - A worker pulls the job from JetStream.
   - The worker retrieves `rate_text` from MongoDB.
   - The worker calls Ollama to extract structured data.
   - Output is validated against the canonical schema.
   - Results are written to `rate_extractions`.
   - `rate_schedules.current` is updated to point to the latest extraction.

6. **Review and Export**
   - The frontend queries MongoDB via the API.
   - Users inspect extracted schedules and citations.
   - Data can be exported to CSV or JSON.

---

## MongoDB Data Model (Summary)

MongoDB is used intentionally with **bounded documents and references**, avoiding excessive embedding.

### `rate_schedules`
- One document per detected rate schedule.
- Stores identity, utility metadata, and page boundaries.
- Contains a small embedded “current extraction summary”.

### `rate_text`
- One document per rate schedule.
- Stores assembled extracted text (evidence).
- Includes page-to-character mappings.

### `rate_extractions`
- Versioned extraction outputs.
- Stores the canonical JSON payload and citations.
- Includes raw LLM output and validation status.

### `documents` and `pages` (optional but recommended)
- Original PDF metadata.
- Per-page extracted text.

### `jobs`
- API-owned job state for frontend display.
- Independent of JetStream internals.

---

## Job Queue: NATS JetStream

### Rationale

JetStream is used because it provides:
- Durable pub/sub semantics
- Language-neutral clients
- Pull-based consumers with explicit backpressure
- A clean path toward event-driven expansion

### Stream Design

- **Stream name:** `RATESCAN_JOBS`
- **Subjects:** `ratescan.jobs.>`
- **Retention:** work-queue (messages are removed on ack)
- **Storage:** file-backed

### Consumers (Pull-Based, Filtered)

- `C_INGEST` — `ratescan.jobs.ingest.requested`
- `C_DETECT` — `ratescan.jobs.detect.requested`
- `C_EXTRACT` — `ratescan.jobs.extract.requested` (concurrency = 1)
- `C_EXPORT` — `ratescan.jobs.export.requested`

**Invariants:**
- Messages are acked only after MongoDB writes succeed.
- Unacked messages are redelivered.
- Poison jobs are bounded via `MaxDeliver`.

---

## Worker Replaceability Strategy

The worker is explicitly designed to be rewritten without system impact.

### Stable Interfaces
- JetStream job message schemas (`contracts/`)
- MongoDB artifact schemas
- Ollama HTTP API

### Replaceable Components
- Worker language/runtime
- PDF parsing implementation
- LLM client library

As long as a worker:
- pulls jobs from JetStream,
- writes valid artifacts to MongoDB,
- and acks messages correctly,

…it is compatible with the system.

---

## Proof of Concept (POC)

The `poc/` directory exists to:
- demonstrate the full pipeline in a single script,
- validate schema and prompt design,
- reduce architectural churn early.

**POC Rules:**
- May be brittle.
- Uses real MongoDB and Ollama.
- Skips JetStream initially.
- Once logic stabilizes, functions are migrated into `core/`.

The POC is a learning scaffold, not production code.

---

## Non-Goals (Current)

- Web crawling or scraping
- OCR for scanned PDFs
- Rider dependency resolution
- GPU acceleration
-uv run python poc/poc_extract.py documents/LGE-Electric-Rates-010126.pdf Perfect tariff normalization

These are intentionally excluded from v1.

---

## Evolution Path

Planned future steps include:
1. Human-in-the-loop review UI
2. OCR fallback for scanned PDFs
3. Expanded charge normalization
4. Event-driven workflows via JetStream events
5. Optional worker rewrite in Go or Rust

---

## Summary

RateScan’s architecture is intentionally:
- **boring where possible**
- **explicit where correctness matters**
- **flexible where performance or language choices may change**

The system optimizes for **trustworth**
