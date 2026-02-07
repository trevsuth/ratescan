#!/usr/bin/env python3
"""
scripts/ollama_generate.py

CLI helper for calling Ollama's /api/generate endpoint.
Intended for use from justfile recipes and ad-hoc testing.

Examples:
  python scripts/ollama_generate.py qwen2.5:7b-instruct "Hello"
  OLLAMA_BASE=http://localhost:11434 python scripts/ollama_generate.py llama3.1 "Write a haiku"
  python scripts/ollama_generate.py qwen2.5:7b-instruct "Hello" --json
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

import typer

app = typer.Typer(add_completion=False)

DEFAULT_OLLAMA_BASE = "http://localhost:11434"


def post_json(url: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        err_body = (
            e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        )
        raise RuntimeError(f"HTTP {e.code} calling {url}: {err_body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connection error calling {url}: {e}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON from {url}: {e}") from e


@app.command()
def generate(
    model: str = typer.Argument(..., help='Model name (e.g. "qwen2.5:7b-instruct")'),
    prompt: str = typer.Argument(..., help="Prompt text"),
    base: str = typer.Option(
        None,
        "--base",
        envvar="OLLAMA_BASE",
        help="Ollama base URL (defaults to env OLLAMA_BASE or http://localhost:11434)",
    ),
    timeout: float = typer.Option(
        300.0,
        "--timeout",
        help="HTTP timeout in seconds",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Print full JSON response instead of just the generated text",
    ),
) -> None:
    """
    Generate text using an Ollama model (non-streaming).
    """
    base_url = (base or DEFAULT_OLLAMA_BASE).rstrip("/")
    url = f"{base_url}/api/generate"

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }

    try:
        resp = post_json(url, payload, timeout)
    except Exception as e:
        typer.secho(f"ERROR: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if json_out:
        typer.echo(json.dumps(resp, indent=2, ensure_ascii=False))
        raise typer.Exit()

    text = resp.get("response", "")
    # Print raw text so this can be piped easily
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")


if __name__ == "__main__":
    app()
