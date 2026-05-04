"""Unit tests for triage with mocked LLM calls."""
import sys
import os
import json
from unittest.mock import patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.models import ParsedFile, RiskLevel
import agent.triage as triage_module


def _make_file(name, additions=10, deletions=5, status="modified"):
    return ParsedFile(filename=name, status=status, additions=additions, deletions=deletions)


def test_skip_lock_files():
    files = [_make_file("package-lock.json"), _make_file("yarn.lock")]
    results = triage_module.rank_files(files)
    for fname in ["package-lock.json", "yarn.lock"]:
        assert results[fname].risk_level == RiskLevel.SKIP


def test_skip_minified():
    files = [_make_file("dist/bundle.min.js")]
    results = triage_module.rank_files(files)
    assert results["dist/bundle.min.js"].risk_level == RiskLevel.SKIP


def test_llm_response_parsed():
    files = [_make_file("auth/login.py"), _make_file("utils/helper.py")]
    mock_response = json.dumps({
        "results": [
            {"filename": "auth/login.py", "risk_level": "critical", "reasoning": "auth file"},
            {"filename": "utils/helper.py", "risk_level": "low", "reasoning": "utility"},
        ]
    })
    with patch("agent.bedrock_client.call_with_retry", return_value=mock_response):
        results = triage_module.rank_files(files)
    assert results["auth/login.py"].risk_level == RiskLevel.CRITICAL
    assert results["utils/helper.py"].risk_level == RiskLevel.LOW


def test_heuristic_fallback_on_llm_error():
    files = [_make_file("auth/token.py"), _make_file("readme.md")]
    with patch("agent.bedrock_client.call_with_retry", side_effect=Exception("timeout")):
        results = triage_module.rank_files(files)
    # auth/token.py matches HIGH pattern
    assert results["auth/token.py"].risk_level == RiskLevel.HIGH


if __name__ == "__main__":
    test_skip_lock_files()
    test_skip_minified()
    test_llm_response_parsed()
    test_heuristic_fallback_on_llm_error()
    print("All tests passed.")
