"""Deep code review using Qwen3 Coder 480B. Falls back to Llama 4 Maverick on error."""
import json
import re
from typing import List, Optional

import agent.bedrock_client as bedrock
import config
from agent.diff_parser import extract_context_window
from agent.models import Category, Finding, ParsedFile, ReviewOutput, RiskLevel, Severity


def _load_prompt() -> str:
    try:
        with open("prompts/review_prompt.txt") as f:
            return f.read()
    except FileNotFoundError:
        return (
            "You are an expert code reviewer. Analyze the provided code diff and identify real bugs, "
            "logic errors, reliability issues, and missing test coverage. "
            "Do NOT flag style issues, naming conventions, or low-confidence observations. "
            "Only report findings with confidence >= 0.75. "
            "Return ONLY valid JSON matching this schema exactly:\n"
            '{"findings": [{"file": "str", "line": int, "severity": "critical|high|medium|suggestion", '
            '"category": "security|bug|logic|reliability|test_coverage", "confidence": float, '
            '"issue": "str", "fix": "str", "reasoning": "str"}]}'
        )


def _clean_raw(raw: str) -> str:
    """Strip think blocks and markdown fences before JSON extraction."""
    raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL)
    raw = re.sub(r'```(?:json)?\s*', '', raw)
    raw = raw.strip()
    return raw


def _parse_findings(raw: str, filename: str) -> List[Finding]:
    print(f"[reviewer] raw LLM response for {filename} (first 800 chars): {raw[:800]}", flush=True)
    raw = _clean_raw(raw)
    findings = []
    # Find the outermost JSON object
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not json_match:
        print(f"[reviewer] no JSON found in response for {filename}", flush=True)
        return findings
    try:
        data = json.loads(json_match.group())
        for item in data.get("findings", []):
            try:
                findings.append(Finding(
                    file=item.get("file", filename),
                    line=int(item.get("line", 1)),
                    severity=Severity(item.get("severity", "medium").lower()),
                    category=Category(item.get("category", "bug").lower()),
                    confidence=float(item.get("confidence", 0.0)),
                    issue=item.get("issue", ""),
                    fix=item.get("fix", ""),
                    reasoning=item.get("reasoning", ""),
                ))
            except (ValueError, KeyError) as e:
                print(f"[reviewer] skipping malformed finding: {e} — {item}", flush=True)
    except json.JSONDecodeError as e:
        print(f"[reviewer] JSON parse error: {e}", flush=True)
    print(f"[reviewer] parsed {len(findings)} findings for {filename}", flush=True)
    return findings


def _build_messages(system_prompt: str, filename: str, patch_content: str, full_content: Optional[str]) -> List[dict]:
    context = f"Full file content:\n```\n{full_content}\n```\n\n" if full_content else ""
    user_content = f"{context}Review this diff for `{filename}`:\n```diff\n{patch_content}\n```"
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def _review_chunk(messages: List[dict], filename: str) -> List[Finding]:
    try:
        raw = bedrock.call_with_retry(bedrock.call_qwen, messages)
    except Exception as e:
        print(f"[reviewer] Qwen3 failed: {e}, trying fallback", flush=True)
        try:
            raw = bedrock.call_with_retry(
                bedrock.call_qwen, messages,
                model_id=config.REVIEW_MODEL_FALLBACK,
                max_tokens=config.REVIEW_MAX_TOKENS,
            )
        except Exception as e2:
            print(f"[reviewer] fallback also failed: {e2}", flush=True)
            return []
    return _parse_findings(raw, filename)


def review_file(parsed_file: ParsedFile, risk_level: RiskLevel, full_content: Optional[str] = None) -> ReviewOutput:
    system_prompt = _load_prompt()
    all_findings: List[Finding] = []
    total_changes = parsed_file.additions + parsed_file.deletions

    if total_changes <= config.LARGE_FILE_LINE_THRESHOLD:
        messages = _build_messages(system_prompt, parsed_file.filename, parsed_file.patch, full_content)
        all_findings = _review_chunk(messages, parsed_file.filename)
    else:
        chunk_lines: List[str] = []
        chunk_token_estimate = 0
        seen_lines = set()

        for i, hunk in enumerate(parsed_file.hunks):
            hunk_text = extract_context_window(parsed_file, i)
            hunk_tokens = len(hunk_text) // 4

            if chunk_token_estimate + hunk_tokens > config.CHUNK_TOKEN_SIZE and chunk_lines:
                chunk_patch = "\n".join(chunk_lines)
                messages = _build_messages(system_prompt, parsed_file.filename, chunk_patch, None)
                for f in _review_chunk(messages, parsed_file.filename):
                    key = (f.line, f.category)
                    if key not in seen_lines:
                        seen_lines.add(key)
                        all_findings.append(f)
                chunk_lines = []
                chunk_token_estimate = 0

            chunk_lines.extend(hunk.lines)
            chunk_token_estimate += hunk_tokens

        if chunk_lines:
            chunk_patch = "\n".join(chunk_lines)
            messages = _build_messages(system_prompt, parsed_file.filename, chunk_patch, None)
            for f in _review_chunk(messages, parsed_file.filename):
                key = (f.line, f.category)
                if key not in seen_lines:
                    seen_lines.add(key)
                    all_findings.append(f)

    before = len(all_findings)
    all_findings = [f for f in all_findings if f.confidence >= config.CONFIDENCE_THRESHOLD]
    print(f"[reviewer] confidence filter: {before} → {len(all_findings)} (threshold={config.CONFIDENCE_THRESHOLD})", flush=True)
    return ReviewOutput(filename=parsed_file.filename, findings=all_findings)
