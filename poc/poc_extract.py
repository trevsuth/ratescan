#!/usr/bin/env python3
"""
POC: Extract a single utility rate schedule from a text-based PDF.

Verbose logging via loguru.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger
from pydantic import BaseModel, Field
from pymongo import MongoClient
from pypdf import PdfReader


# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logger.remove()
logger.add(
    sys.stderr,
    level=LOG_LEVEL,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "{message}"
    ),
)

logger.debug("Logger initialized with level {}", LOG_LEVEL)


# ---------------------------------------------------------------------------
# Config (env overridable)
# ---------------------------------------------------------------------------

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/ratescan")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")

PROMPT_VERSION = "poc_v1"
UTILITY_NAME_DEFAULT = os.getenv("UTILITY_NAME", "unknown_utility")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sha256_file(path: str) -> str:
    logger.debug("Computing SHA256 for {}", path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    digest = "sha256:" + h.hexdigest()
    logger.debug("SHA256 computed: {}", digest)
    return digest


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def collapse_ws(s: str) -> str:
    return re.sub(
        r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", s.replace("\r", ""))
    ).strip()


# ---------------------------------------------------------------------------
# Boundary detection (POC heuristics)
# ---------------------------------------------------------------------------

BOUNDARY_MARKERS = [
    r"\brate schedule\b",
    r"\bschedule\b",
    r"\bapplicable to\b",
    r"\bavailability\b",
    r"\bcharacter of service\b",
    r"\bcustomer charge\b",
    r"\bdemand charge\b",
    r"\benergy charge\b",
]

MARKER_RE = re.compile("|".join(BOUNDARY_MARKERS), flags=re.IGNORECASE)


@dataclass(frozen=True)
class PageHit:
    page_index: int
    score: int


def score_pages(pages: List[str]) -> List[PageHit]:
    logger.info("Scoring {} pages for boundary markers", len(pages))
    hits: List[PageHit] = []
    for i, txt in enumerate(pages):
        matches = MARKER_RE.findall(txt or "")
        if matches:
            hits.append(PageHit(i, len(matches)))
            logger.debug("Page {} matched {} markers", i + 1, len(matches))
    logger.info("Detected {} candidate pages", len(hits))
    return hits


def cluster_ranges(hits: List[PageHit], gap: int = 1) -> List[Tuple[int, int]]:
    if not hits:
        logger.warning("No page hits found for clustering")
        return []
    idxs = sorted(h.page_index for h in hits)
    ranges: List[Tuple[int, int]] = []
    start = prev = idxs[0]

    for idx in idxs[1:]:
        if idx <= prev + gap + 1:
            prev = idx
        else:
            ranges.append((start, prev))
            start = prev = idx
    ranges.append((start, prev))
    logger.info("Clustered into {} page ranges", len(ranges))
    return ranges


def expand_ranges(
    ranges: List[Tuple[int, int]],
    num_pages: int,
    pad_after: int = 2,
) -> List[Tuple[int, int]]:
    expanded: List[Tuple[int, int]] = []
    for s, e in ranges:
        e2 = min(num_pages - 1, e + pad_after)
        expanded.append((s, e2))
    logger.debug("Expanded ranges: {}", expanded)
    return expanded


# ---------------------------------------------------------------------------
# Canonical-ish schema (POC)
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    field: str
    page: int
    snippet: str


class EligibilityRules(BaseModel):
    demand_kw_max: Optional[float] = None
    service_voltage: Optional[str] = None
    geography: Optional[str] = None
    metering: Optional[str] = None


class Eligibility(BaseModel):
    summary: str
    rules: EligibilityRules = Field(default_factory=EligibilityRules)
    exclusions: Optional[str] = None


class Charge(BaseModel):
    type: str
    value: Optional[float] = None
    unit: Optional[str] = None
    structure: Optional[str] = None
    tiers: Optional[list] = None
    notes: Optional[str] = None


class Schedule(BaseModel):
    schedule_name: str
    schedule_code: Optional[str] = None
    effective_date: Optional[str] = None
    customer_class: Optional[str] = None
    eligibility: Eligibility
    charges: List[Charge]
    citations: List[Citation]


class ExtractionPayload(BaseModel):
    schedules: List[Schedule]


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


def build_prompt(schedule_text: str) -> str:
    logger.debug("Building extraction prompt (chars={})", len(schedule_text))

    # Keep the schema concrete and small-model-friendly.
    schema_hint = {
        "schedules": [
            {
                "schedule_name": "string",
                "schedule_code": "string|null",
                "effective_date": "string|null",
                "customer_class": "string|null",
                "eligibility": {
                    "summary": "string",
                    "rules": {
                        "demand_kw_max": "number|null",
                        "service_voltage": "string|null",
                        "geography": "string|null",
                        "metering": "string|null",
                    },
                    "exclusions": "string|null",
                },
                "charges": [
                    {
                        "type": "customer|energy|demand|other",
                        "value": "number|null",
                        "unit": "string|null",
                        "structure": "flat|tiered|tou|seasonal|null",
                        "tiers": "array|null",
                        "notes": "string|null",
                    }
                ],
                "citations": [
                    {
                        "field": "schedule_name",
                        "page": 1,
                        "snippet": "verbatim supporting text",
                    }
                ],
            }
        ]
    }

    # IMPORTANT: spell out "no code fences" because many models default to ```json
    return f"""
You are an information extraction engine. Extract ONE OR MORE utility rate schedules from the tariff excerpt.

OUTPUT REQUIREMENTS (must follow exactly):
- Output MUST be a single JSON object, and MUST start with '{{' and end with '}}'.
- Do NOT output markdown. Do NOT wrap in ``` or ```json. Do NOT include explanations.
- The JSON MUST match this schema shape (keys, nesting, arrays):
{json.dumps(schema_hint, indent=2)}

CITATION RULES:
- Every non-null field must be supported by at least one citation in "citations".
- Citation objects:
  - field: the exact field name or dot-path (examples: "schedule_name", "eligibility.summary", "charges[0].value")
  - page: 1-based page number from the excerpt markers (e.g., "--- PAGE 7 ---" means page=7)
  - snippet: a short verbatim excerpt that supports the value
- If you are not confident, use null and do NOT cite.

CHARGE RULES:
- Include customer charge, energy charge, and demand charge if present.
- If charges are tiered or TOU, set structure accordingly and put details in "tiers" (can be a list of dicts).
- If you cannot reliably structure tiers, set tiers=null and include a short description in notes.

TARIFF EXCERPT:
<<<BEGIN EXCERPT>>>
{schedule_text}
<<<END EXCERPT>>>
""".strip()


async def ollama_generate(prompt: str) -> str:
    url = f"{OLLAMA_URL.rstrip('/')}/api/generate"
    payload = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}

    logger.info("Calling Ollama model={} chars={}", OLLAMA_MODEL, len(prompt))
    t0 = time.time()

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        out = resp.json().get("response", "")

    logger.info("Ollama response received in {:.1f}s", time.time() - t0)
    logger.debug("Raw LLM output length={}", len(out))
    return out


# ---------------------------------------------------------------------------
# Mongo
# ---------------------------------------------------------------------------


def get_db():
    logger.debug("Connecting to MongoDB at {}", MONGO_URI)
    client = MongoClient(MONGO_URI)

    # If the URI includes a database name, PyMongo can return it here.
    default_db = client.get_default_database()
    if default_db is not None:
        return default_db

    # Otherwise, fall back to a sane default DB name.
    return client["ratescan"]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def read_pdf_pages(path: str) -> List[str]:
    logger.info("Reading PDF {}", path)
    reader = PdfReader(path)
    pages: List[str] = []
    for i, p in enumerate(reader.pages):
        try:
            text = p.extract_text() or ""
        except Exception as e:
            logger.warning("Failed extracting page {}: {}", i + 1, e)
            text = ""
        pages.append(text)
    logger.info("Extracted {} pages from PDF", len(pages))
    return pages


def main(pdf_path: str) -> int:
    logger.info("Starting POC extraction for {}", pdf_path)

    if not os.path.exists(pdf_path):
        logger.error("PDF not found: {}", pdf_path)
        return 1

    db = get_db()

    doc_id = sha256_file(pdf_path)
    logger.info("Document ID {}", doc_id)

    db.documents.update_one(
        {"doc_id": doc_id},
        {"$set": {"doc_id": doc_id, "path": pdf_path, "ingested_at": now_iso()}},
        upsert=True,
    )

    pages = read_pdf_pages(pdf_path)
    hits = score_pages(pages)
    ranges = expand_ranges(cluster_ranges(hits), len(pages))

    if not ranges:
        logger.error("No schedule candidate ranges detected")
        return 2

    start, end = ranges[0]
    logger.info("Using page range {}–{}", start + 1, end + 1)

    schedule_text = collapse_ws(
        "\n".join(f"\n--- PAGE {i + 1} ---\n{pages[i]}" for i in range(start, end + 1))
    )

    rate_id = f"rate_{uuid.uuid4().hex[:12]}"
    logger.info("Generated rate_id {}", rate_id)

    db.rate_text.insert_one(
        {
            "rate_id": rate_id,
            "doc_id": doc_id,
            "utility": UTILITY_NAME_DEFAULT,
            "page_start": start + 1,
            "page_end": end + 1,
            "status": "creted",
            # "text": schedule_text,
            "created_at": now_iso(),
        }
    )

    prompt = build_prompt(schedule_text)

    try:
        raw = asyncio.run(ollama_generate(prompt))
        json_text = extract_json_object(raw)
        payload = ExtractionPayload.model_validate(json.loads(json_text))
    except Exception as e:
        logger.exception("Extraction or validation failed")
        db.rate_extractions.insert_one(
            {
                "rate_id": rate_id,
                "doc_id": doc_id,
                "status": "failed",
                "error": str(e),
                "raw_output": raw if "raw" in locals() else None,
                "created_at": now_iso(),
            }
        )
        return 3

    extraction_id = f"ext_{uuid.uuid4().hex[:12]}"
    logger.info("Extraction succeeded (id={})", extraction_id)

    db.rate_extractions.insert_one(
        {
            "extraction_id": extraction_id,
            "rate_id": rate_id,
            "doc_id": doc_id,
            "status": "ok",
            "payload": payload.model_dump(),
            "created_at": now_iso(),
        }
    )

    db.rate_schedules.update_one(
        {"rate_id": rate_id},
        {"$set": {"status": "extracted", "current_extraction_id": extraction_id}},
    )

    sched = payload.schedules[0]
    logger.success(
        "Extracted schedule '{}' with {} charges and {} citations",
        sched.schedule_name,
        len(sched.charges),
        len(sched.citations),
    )

    return 0


def extract_json_object(text: str) -> str:
    """
    Extract the first top-level JSON object from a string (robust to ```json fences or extra text).
    """
    t = text.strip()

    # Strip markdown fences if present
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE).strip()
        t = re.sub(r"\s*```$", "", t).strip()

    # If it's already a JSON object, return it
    if t.startswith("{") and t.endswith("}"):
        return t

    # Bracket-match first JSON object
    start = t.find("{")
    if start == -1:
        raise ValueError("No '{' found in model output")

    depth = 0
    for i in range(start, len(t)):
        ch = t[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return t[start : i + 1]

    raise ValueError("Unbalanced braces in model output")


if __name__ == "__main__":
    pdf = (
        sys.argv[1] if len(sys.argv) > 1 else "documents/LGE-Electric-Rates-010126.pdf"
    )
    sys.exit(main(pdf))
