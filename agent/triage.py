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


def _minimum_risk(f: ParsedFile) -> RiskLevel:
    """Hard floor on risk level based on file metadata — LLM cannot go below this."""
    if f.status == "added":
        return RiskLevel.MEDIUM   # new files always get at least medium
    if _HIGH_PATTERNS.search(f.filename.lower()):
        return RiskLevel.HIGH
    if f.additions + f.deletions > 200:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


_RISK_ORDER = {RiskLevel.CRITICAL: 4, RiskLevel.HIGH: 3, RiskLevel.MEDIUM: 2, RiskLevel.LOW: 1, RiskLevel.SKIP: 0}


def _max_risk(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    return a if _RISK_ORDER[a] >= _RISK_ORDER[b] else b


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
        print(f"[triage] raw response: {raw[:500]}", flush=True)
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
        print(f"[triage] LLM failed, using heuristics: {e}", flush=True)

    # Apply minimum risk floor — LLM cannot downgrade new/sensitive files
    for f in to_triage:
        floor = _minimum_risk(f)
        if f.filename in results:
            final = _max_risk(results[f.filename].risk_level, floor)
            if final != results[f.filename].risk_level:
                results[f.filename] = TriageResult(f.filename, final, results[f.filename].reasoning + " [floor applied]")
        else:
            results[f.filename] = TriageResult(f.filename, floor, "heuristic fallback")

    print(f"[triage] results: {[(k, v.risk_level.value) for k, v in results.items()]}", flush=True)
    return results
