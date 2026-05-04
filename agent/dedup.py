"""Fingerprint-based deduplication. Prevents re-posting comments across PR re-runs."""
import hashlib
import re
from typing import List, Set

from agent.models import Finding

_FP_PATTERN = re.compile(r'<!-- fp:([0-9a-f]{16}) -->')


def compute_fingerprint(finding: Finding) -> str:
    key = f"{finding.file}:{finding.line}:{finding.category}:{finding.issue[:50]}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def extract_fingerprints(existing_comments: List[dict]) -> Set[str]:
    fps = set()
    for comment in existing_comments:
        body = comment.get("body", "")
        for match in _FP_PATTERN.finditer(body):
            fps.add(match.group(1))
    return fps


def filter_findings(findings: List[Finding], existing_comments: List[dict]) -> List[Finding]:
    posted_fps = extract_fingerprints(existing_comments)
    result = []
    for finding in findings:
        fp = compute_fingerprint(finding)
        if fp not in posted_fps:
            finding.fingerprint = fp
            result.append(finding)
    return result
