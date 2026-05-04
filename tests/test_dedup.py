"""Unit tests for dedup. No network calls."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.dedup import compute_fingerprint, extract_fingerprints, filter_findings
from agent.models import Category, Finding, Severity


def _make_finding(line=10, issue="null deref"):
    return Finding(
        file="app.py", line=line, severity=Severity.HIGH,
        category=Category.BUG, confidence=0.9,
        issue=issue, fix="check for None", reasoning="can crash",
    )


def test_fingerprint_stable():
    f = _make_finding()
    assert compute_fingerprint(f) == compute_fingerprint(f)


def test_fingerprint_differs_by_line():
    f1 = _make_finding(line=10)
    f2 = _make_finding(line=11)
    assert compute_fingerprint(f1) != compute_fingerprint(f2)


def test_fingerprint_length():
    assert len(compute_fingerprint(_make_finding())) == 16


def test_extract_fingerprints_empty():
    assert extract_fingerprints([]) == set()


def test_extract_fingerprints_from_comments():
    comments = [
        {"body": "some text <!-- fp:abc123def456789a --> more text"},
        {"body": "no fingerprint here"},
        {"body": "<!-- fp:1234567890abcdef -->"},
    ]
    fps = extract_fingerprints(comments)
    assert "abc123def456789a" in fps
    assert "1234567890abcdef" in fps
    assert len(fps) == 2


def test_filter_findings_removes_duplicates():
    f = _make_finding()
    fp = compute_fingerprint(f)
    existing = [{"body": f"<!-- fp:{fp} -->"}]
    result = filter_findings([f], existing)
    assert result == []


def test_filter_findings_keeps_new():
    f = _make_finding()
    result = filter_findings([f], [])
    assert len(result) == 1
    assert result[0].fingerprint is not None


if __name__ == "__main__":
    test_fingerprint_stable()
    test_fingerprint_differs_by_line()
    test_fingerprint_length()
    test_extract_fingerprints_empty()
    test_extract_fingerprints_from_comments()
    test_filter_findings_removes_duplicates()
    test_filter_findings_keeps_new()
    print("All tests passed.")
