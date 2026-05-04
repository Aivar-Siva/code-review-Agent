"""Pipeline orchestrator. Called by GitHub Actions. No business logic — just wiring."""
import os
import re
import sys
import traceback

import config
import agent.github_client as github
import agent.poster as poster
import agent.reviewer as reviewer
import agent.security_reviewer as security_reviewer
import agent.triage as triage
from agent.dedup import filter_findings
from agent.diff_parser import parse_patch
from agent.models import RiskLevel

_SECURITY_RE = re.compile(r"(auth|token|secret|password|jwt|crypto|payment|admin|\.sql$|migration)")


def _log(msg: str) -> None:
    print(msg, flush=True)


def _is_security_sensitive(filename: str) -> bool:
    return bool(_SECURITY_RE.search(filename.lower()))


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    pr_number = int(os.environ["PR_NUMBER"])
    _log(f"[agent] Starting review for {repo} PR#{pr_number}")

    try:
        pr_meta = github.get_pr_metadata(repo, pr_number)
        commit_sha = pr_meta["head"]["sha"]
        _log(f"[agent] Commit: {commit_sha[:8]}")

        raw_files = github.get_pr_files(repo, pr_number)
        _log(f"[agent] Files in PR: {len(raw_files)}")

        parsed_files = {
            f["filename"]: parse_patch(f["filename"], f.get("patch", ""), f.get("status", "modified"))
            for f in raw_files
        }

        triage_results = triage.rank_files(list(parsed_files.values()))
        _log(f"[agent] Triage complete: {[(k, v.risk_level.value) for k, v in triage_results.items()]}")

        skipped = [fname for fname, tr in triage_results.items() if tr.risk_level == RiskLevel.SKIP]
        active_files = [
            (fname, tr) for fname, tr in triage_results.items()
            if tr.risk_level != RiskLevel.SKIP
        ]
        risk_order = {RiskLevel.CRITICAL: 0, RiskLevel.HIGH: 1, RiskLevel.MEDIUM: 2, RiskLevel.LOW: 3}
        active_files.sort(key=lambda x: risk_order.get(x[1].risk_level, 99))

        existing_comments = github.get_existing_review_comments(repo, pr_number)
        all_findings = []
        token_budget = config.PR_TOKEN_BUDGET

        for fname, tr in active_files:
            _log(f"[agent] Reviewing {fname} ({tr.risk_level.value})")
            pf = parsed_files[fname]
            estimated_tokens = (pf.additions + pf.deletions) * 10

            if token_budget <= 0 and tr.risk_level == RiskLevel.LOW:
                skipped.append(fname)
                continue

            full_content = None
            if tr.risk_level == RiskLevel.CRITICAL:
                full_content = github.get_file_content(repo, fname, commit_sha)

            review_output = reviewer.review_file(pf, tr.risk_level, full_content)
            _log(f"[agent] {fname}: {len(review_output.findings)} findings")
            all_findings.extend(review_output.findings)

            if tr.risk_level in (RiskLevel.CRITICAL, RiskLevel.HIGH) and _is_security_sensitive(fname):
                sec_output = security_reviewer.review_file(pf, full_content)
                _log(f"[agent] {fname} security pass: {len(sec_output.findings)} findings")
                all_findings.extend(sec_output.findings)

            token_budget -= estimated_tokens

        all_findings = filter_findings(all_findings, existing_comments)
        _log(f"[agent] After dedup: {len(all_findings)} findings to post")

        poster.post_review(
            repo=repo,
            pr_number=pr_number,
            commit_sha=commit_sha,
            findings=all_findings,
            parsed_files=parsed_files,
            triage_results=triage_results,
            skipped=skipped,
        )
        _log("[agent] Done.")

    except Exception:
        tb = traceback.format_exc()
        _log(f"[agent] ERROR:\n{tb}")
        try:
            github.post_summary_comment(
                repo, pr_number,
                f"## ⚠️ PR Review Agent Error\n\nThe agent encountered an error. Manual review required.\n\n```\n{tb}\n```"
            )
        except Exception as e:
            _log(f"[agent] Failed to post error comment: {e}")
        sys.exit(1)


main()
