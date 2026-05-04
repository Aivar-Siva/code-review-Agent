"""Pipeline orchestrator. Called by GitHub Actions. No business logic — just wiring."""
import os
import sys
import traceback

import agent.github_client as github
import agent.poster as poster
import agent.reviewer as reviewer
import agent.security_reviewer as security_reviewer
import agent.triage as triage
import re

import config
from agent.dedup import filter_findings
from agent.diff_parser import parse_patch
from agent.models import RiskLevel

_SECURITY_RE = re.compile(r"(auth|token|secret|password|jwt|crypto|payment|admin|\.sql$|migration)")


def _is_security_sensitive(filename: str) -> bool:
    return bool(_SECURITY_RE.search(filename.lower()))


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    pr_number = int(os.environ["PR_NUMBER"])

    try:
        # 1. Fetch PR metadata and files
        pr_meta = github.get_pr_metadata(repo, pr_number)
        commit_sha = pr_meta["head"]["sha"]
        base_sha = pr_meta["base"]["sha"]

        raw_files = github.get_pr_files(repo, pr_number)

        # 2. Parse diffs
        parsed_files = {
            f["filename"]: parse_patch(f["filename"], f.get("patch", ""), f.get("status", "modified"))
            for f in raw_files
        }

        # 3. Triage
        triage_results = triage.rank_files(list(parsed_files.values()))

        skipped = [fname for fname, tr in triage_results.items() if tr.risk_level == RiskLevel.SKIP]
        active_files = [
            (fname, tr) for fname, tr in triage_results.items()
            if tr.risk_level != RiskLevel.SKIP
        ]
        # Sort by risk: critical first
        risk_order = {RiskLevel.CRITICAL: 0, RiskLevel.HIGH: 1, RiskLevel.MEDIUM: 2, RiskLevel.LOW: 3}
        active_files.sort(key=lambda x: risk_order.get(x[1].risk_level, 99))

        # 4. Fetch existing comments for dedup
        existing_comments = github.get_existing_review_comments(repo, pr_number)

        # 5. Review each file
        all_findings = []
        token_budget = config.PR_TOKEN_BUDGET

        for fname, tr in active_files:
            pf = parsed_files[fname]
            estimated_tokens = (pf.additions + pf.deletions) * 10  # rough estimate

            if token_budget <= 0 and tr.risk_level == RiskLevel.LOW:
                skipped.append(fname)
                continue

            # Context enrichment based on risk level
            full_content = None
            if tr.risk_level == RiskLevel.CRITICAL:
                full_content = github.get_file_content(repo, fname, commit_sha)
            elif tr.risk_level == RiskLevel.HIGH:
                full_content = None  # patch + context window is enough

            # Main review
            review_output = reviewer.review_file(pf, tr.risk_level, full_content)
            all_findings.extend(review_output.findings)

            # Security pass for critical/high security-sensitive files
            if tr.risk_level in (RiskLevel.CRITICAL, RiskLevel.HIGH) and _is_security_sensitive(fname):
                sec_output = security_reviewer.review_file(pf, full_content)
                all_findings.extend(sec_output.findings)

            token_budget -= estimated_tokens

        # 6. Dedup
        all_findings = filter_findings(all_findings, existing_comments)

        # 7. Post
        poster.post_review(
            repo=repo,
            pr_number=pr_number,
            commit_sha=commit_sha,
            findings=all_findings,
            parsed_files=parsed_files,
            triage_results=triage_results,
            skipped=skipped,
        )

    except Exception:
        tb = traceback.format_exc()
        try:
            github.post_summary_comment(
                repo, pr_number,
                f"## ⚠️ PR Review Agent Error\n\nThe agent encountered an error. Manual review required.\n\n```\n{tb}\n```"
            )
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
