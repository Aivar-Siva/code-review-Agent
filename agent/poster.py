"""Format findings and post to GitHub as a single batch review."""
from typing import Dict, List

import agent.github_client as github
from agent.diff_parser import get_diff_position
from agent.models import Finding, ParsedFile, RiskLevel, Severity, TriageResult

_SEVERITY_EMOJI = {
    Severity.CRITICAL: "🔴",
    Severity.HIGH: "🟠",
    Severity.MEDIUM: "🟡",
    Severity.SUGGESTION: "💡",
}


def _format_comment(finding: Finding) -> str:
    emoji = _SEVERITY_EMOJI.get(finding.severity, "")
    return (
        f"{emoji} **[{finding.severity.value.upper()}/{finding.category.value.upper()}]** {finding.issue}\n\n"
        f"**Fix:** {finding.fix}\n\n"
        f"**Why:** {finding.reasoning}\n\n"
        f"<!-- fp:{finding.fingerprint} -->"
    )


def _build_inline_comments(findings: List[Finding], parsed_files: Dict[str, ParsedFile]) -> List[dict]:
    comments = []
    for finding in findings:
        pf = parsed_files.get(finding.file)
        if not pf:
            continue
        position = get_diff_position(pf, finding.line)
        if position is None:
            continue
        comments.append({
            "path": finding.file,
            "position": position,
            "body": _format_comment(finding),
            "severity": finding.severity.value,
        })
    return comments


def _build_summary(
    findings: List[Finding],
    triage_results: Dict[str, TriageResult],
    skipped: List[str],
    commit_sha: str,
) -> str:
    counts = {s: 0 for s in Severity}
    for f in findings:
        counts[f.severity] += 1

    lines = ["## 🤖 PR Review Agent Summary\n"]
    lines.append(f"**Commit reviewed:** `{commit_sha[:8]}`\n")

    if findings:
        lines.append("### Findings")
        lines.append(f"- 🔴 Critical: {counts[Severity.CRITICAL]}")
        lines.append(f"- 🟠 High: {counts[Severity.HIGH]}")
        lines.append(f"- 🟡 Medium: {counts[Severity.MEDIUM]}")
        lines.append(f"- 💡 Suggestions: {counts[Severity.SUGGESTION]}\n")
    else:
        lines.append("✅ No issues found.\n")

    if triage_results:
        lines.append("### Files Reviewed")
        for fname, tr in triage_results.items():
            if tr.risk_level != RiskLevel.SKIP:
                lines.append(f"- `{fname}` — **{tr.risk_level.value}** risk")

    if skipped:
        lines.append("\n### Skipped Files")
        for fname in skipped:
            lines.append(f"- `{fname}` (auto-generated / lock file)")

    return "\n".join(lines)


def post_review(
    repo: str,
    pr_number: int,
    commit_sha: str,
    findings: List[Finding],
    parsed_files: Dict[str, ParsedFile],
    triage_results: Dict[str, TriageResult],
    skipped: List[str],
) -> None:
    inline_comments = _build_inline_comments(findings, parsed_files)
    summary = _build_summary(findings, triage_results, skipped, commit_sha)

    if inline_comments or findings:
        github.post_review(repo, pr_number, commit_sha, "", inline_comments)

    github.post_summary_comment(repo, pr_number, summary)
