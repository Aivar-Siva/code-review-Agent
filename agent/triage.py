"""Risk ranking of PR files using Llama 3.3 70B. Metadata only — never reads patch content."""
import json
import re
from typing import Dict, List

import agent.bedrock_client as bedrock
from agent.models import ParsedFile, RiskLevel, TriageResult

_SKIP_PATTERNS = re.compile(
    r"(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|\.min\.js|/dist/|/build/|__pycache__|\.pyc$|\.map$)"
)
_HIGH_PATTERNS = re.compile(
    r"(auth|token|secret|password|jwt|crypto|payment|admin|\.sql$|migration|\.env)"
)


def _load_prompt() -> str:
    try:
        with open("prompts/triage_prompt.txt") as f:
            return f.read()
    except FileNotFoundError:
        return (
            "You are a code review triage assistant. Given a list of files changed in a pull request, "
            "rank each file by review priority. Return ONLY valid JSON: "
            '{{"results": [{{"filename": "...", "risk_level": "critical|high|medium|low|skip", "reasoning": "..."}}]}}'
        )


def rank_files(files: List[ParsedFile]) -> Dict[str, TriageResult]:
    results: Dict[str, TriageResult] = {}

    # Pre-filter obvious skips locally (no LLM needed)
    to_triage = []
    for f in files:
        if _SKIP_PATTERNS.search(f.filename):
            results[f.filename] = TriageResult(f.filename, RiskLevel.SKIP, "auto-generated or lock file")
        else:
            to_triage.append(f)

    if not to_triage:
        return results

    file_list = "\n".join(
        f"- {f.filename} ({f.status}, +{f.additions}/-{f.deletions} lines)"
        for f in to_triage
    )
    system_prompt = _load_prompt()
    prompt = f"{system_prompt}\n\nFiles changed in this PR:\n{file_list}\n\nRespond with JSON only."

    try:
        raw = bedrock.call_with_retry(bedrock.call_llama, prompt)
        # Extract JSON from response
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            for item in data.get("results", []):
                fname = item["filename"]
                try:
                    level = RiskLevel(item["risk_level"].lower())
                except ValueError:
                    level = RiskLevel.MEDIUM
                results[fname] = TriageResult(fname, level, item.get("reasoning", ""))
    except Exception as e:
        # Fallback: use heuristics
        for f in to_triage:
            if _HIGH_PATTERNS.search(f.filename.lower()):
                level = RiskLevel.HIGH
            elif f.additions + f.deletions > 200:
                level = RiskLevel.MEDIUM
            else:
                level = RiskLevel.LOW
            results[f.filename] = TriageResult(f.filename, level, f"heuristic fallback (triage error: {e})")

    # Fill any missing files from LLM response
    for f in to_triage:
        if f.filename not in results:
            results[f.filename] = TriageResult(f.filename, RiskLevel.MEDIUM, "not returned by triage model")

    return results
