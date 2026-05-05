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
    pr_overview: str,
    inline_count: int,
) -> str:
    reviewed = [(fname, tr) for fname, tr in triage_results.items() if tr.risk_level != RiskLevel.SKIP]
    total_reviewed = len(reviewed)
    total_files = total_reviewed + len(skipped)

    lines = ["## Pull request overview"]
    if pr_overview:
        lines.append(f"\n{pr_overview}\n")

    lines.append("**Changes:**\n")
    for fname, tr in triage_results.items():
        status_word = "Added" if tr.risk_level != RiskLevel.SKIP else "Skipped"
        lines.append(f"- {status_word} `{fname}`: {tr.reasoning}")

    lines.append(f"\n---\n")
    lines.append(f"Reviewed {total_reviewed} out of {total_files} changed files and generated {inline_count} comment{'s' if inline_count != 1 else ''}.\n")

    if reviewed:
        lines.append("| File | Description |")
        lines.append("|------|-------------|")
        for fname, tr in reviewed:
            desc = tr.reasoning if tr.reasoning and tr.reasoning != "heuristic fallback" else f"{tr.risk_level.value} risk file"
            lines.append(f"| `{fname}` | {desc} |")

    if skipped:
        lines.append(f"\n*Skipped {len(skipped)} auto-generated/lock file(s).*")

    lines.append(f"\n`{commit_sha[:8]}`")
    return "\n".join(lines)


def post_review(
    repo: str,
    pr_number: int,
    commit_sha: str,
    findings: List[Finding],
    parsed_files: Dict[str, ParsedFile],
    triage_results: Dict[str, TriageResult],
    skipped: List[str],
    pr_overview: str = "",
) -> None:
    inline_comments = _build_inline_comments(findings, parsed_files)
    summary = _build_summary(findings, triage_results, skipped, commit_sha, pr_overview, len(inline_comments))

    if inline_comments or findings:
        github.post_review(repo, pr_number, commit_sha, "", inline_comments)

    github.post_summary_comment(repo, pr_number, summary)
