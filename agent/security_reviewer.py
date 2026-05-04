"""Security-focused second pass using DeepSeek R1 chain-of-thought reasoning."""
import json
import re
from typing import List, Optional

import agent.bedrock_client as bedrock
import config
from agent.models import Category, Finding, ParsedFile, ReviewOutput, Severity


def _load_prompt() -> str:
    try:
        with open("prompts/security_prompt.txt") as f:
            return f.read()
    except FileNotFoundError:
        return (
            "You are a security-focused code reviewer specializing in OWASP vulnerabilities. "
            "Analyze the diff for: SQL/command/LDAP injection, broken authentication, "
            "sensitive data exposure, SSRF, IDOR, path traversal, insecure deserialization, "
            "hardcoded secrets, missing rate limiting, JWT weaknesses, privilege escalation. "
            "Think step by step before concluding. Only report real vulnerabilities with confidence >= 0.75. "
            "Return ONLY valid JSON:\n"
            '{"findings": [{"file": "str", "line": int, "severity": "critical|high|medium|suggestion", '
            '"category": "security", "confidence": float, "issue": "str", "fix": "str", "reasoning": "str"}]}'
        )


def _parse_findings(raw: str, filename: str) -> List[Finding]:
    # DeepSeek R1 may include <think>...</think> blocks — strip them
    raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
    findings = []
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not json_match:
        return findings
    try:
        data = json.loads(json_match.group())
        for item in data.get("findings", []):
            try:
                findings.append(Finding(
                    file=item.get("file", filename),
                    line=int(item.get("line", 1)),
                    severity=Severity(item.get("severity", "high").lower()),
                    category=Category.SECURITY,
                    confidence=float(item.get("confidence", 0.0)),
                    issue=item.get("issue", ""),
                    fix=item.get("fix", ""),
                    reasoning=item.get("reasoning", ""),
                ))
            except (ValueError, KeyError):
                continue
    except json.JSONDecodeError:
        pass
    return findings


def review_file(parsed_file: ParsedFile, full_content: Optional[str] = None) -> ReviewOutput:
    system_prompt = _load_prompt()
    context = f"Full file content:\n```\n{full_content}\n```\n\n" if full_content else ""
    user_content = f"{context}Security review for `{parsed_file.filename}`:\n```diff\n{parsed_file.patch}\n```"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        raw = bedrock.call_with_retry(bedrock.call_deepseek, messages)
    except Exception:
        return ReviewOutput(filename=parsed_file.filename, findings=[])

    findings = [f for f in _parse_findings(raw, parsed_file.filename) if f.confidence >= config.CONFIDENCE_THRESHOLD]
    return ReviewOutput(filename=parsed_file.filename, findings=findings)
