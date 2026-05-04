"""Unified Bedrock proxy client. All LLM calls go through here."""
import json
import time
import urllib.request
import urllib.error
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

    # Handle streaming (newline-delimited JSON chunks)
    text = ""
    for line in body.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Llama format
        if "generation" in chunk:
            text += chunk["generation"]
        # Messages format (Qwen / DeepSeek)
        elif "choices" in chunk:
            for choice in chunk["choices"]:
                delta = choice.get("delta", {})
                text += delta.get("content", "")
                if not delta:
                    text += choice.get("message", {}).get("content", "")
        # Single-shot response
        elif "content" in chunk:
            text += chunk["content"]

    return text.strip()


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
