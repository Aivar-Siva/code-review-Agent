"""Unified Bedrock proxy client. All LLM calls go through here."""
import json
import time
import urllib.request
from typing import List, Dict

import config


def _post(payload: dict) -> str:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        config.BEDROCK_PROXY_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read().decode()

    print(f"[bedrock] raw response (first 500 chars): {body[:500]}", flush=True)

    # Try parsing as a single JSON object first (non-streaming)
    try:
        obj = json.loads(body.strip())
        return _extract_text(obj)
    except (json.JSONDecodeError, ValueError):
        pass

    # Newline-delimited streaming JSON (SSE: "data: {...}")
    text = ""
    for line in body.strip().splitlines():
        line = line.strip()
        if not line or line == "data: [DONE]":
            continue
        if line.startswith("data: "):
            line = line[6:]
        try:
            chunk = json.loads(line)
            text += _extract_text(chunk)
        except json.JSONDecodeError:
            text += line
    return text.strip()


def _extract_text(obj: dict) -> str:
    """Extract text content from any known response shape."""
    # Llama: {"generation": "..."}
    if "generation" in obj:
        return obj["generation"]
    # OpenAI-style choices
    if "choices" in obj:
        for choice in obj["choices"]:
            # streaming delta
            content = choice.get("delta", {}).get("content") or ""
            # non-streaming message
            if not content:
                content = choice.get("message", {}).get("content") or ""
            if content:
                return content
    # Direct content field
    if "content" in obj:
        c = obj["content"]
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return "".join(block.get("text", "") for block in c if isinstance(block, dict))
    return ""


def call_with_retry(fn, *args, **kwargs) -> str:
    last_err = None
    for attempt in range(config.MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < config.MAX_RETRIES - 1:
                time.sleep(config.BACKOFF_SECONDS ** (attempt + 1))
    raise last_err


def call_llama(prompt: str, model_id: str = config.TRIAGE_MODEL, max_tokens: int = config.TRIAGE_MAX_TOKENS) -> str:
    return _post({"model_id": model_id, "prompt": prompt, "max_gen_len": max_tokens})


def call_qwen(messages: List[Dict], model_id: str = config.REVIEW_MODEL, max_tokens: int = config.REVIEW_MAX_TOKENS) -> str:
    return _post({"model_id": model_id, "messages": messages, "max_tokens": max_tokens})


def call_deepseek(messages: List[Dict], max_tokens: int = config.SECURITY_MAX_TOKENS) -> str:
    return _post({"model_id": config.SECURITY_MODEL, "messages": messages, "max_tokens": max_tokens})
