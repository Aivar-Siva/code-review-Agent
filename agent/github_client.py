"""GitHub REST API client. All GitHub I/O goes through here."""
import json
import os
import urllib.request
import urllib.parse
from typing import List, Dict, Optional

GITHUB_API = "https://api.github.com"


def _headers() -> dict:
    token = os.environ["GITHUB_TOKEN"]
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get(url: str) -> any:
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _get_paginated(url: str) -> List[dict]:
    results = []
    page = 1
    while True:
        sep = "&" if "?" in url else "?"
        data = _get(f"{url}{sep}per_page=100&page={page}")
        if not data:
            break
        results.extend(data)
        if len(data) < 100:
            break
        page += 1
    return results


def _post(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={**_headers(), "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def get_pr_metadata(repo: str, pr_number: int) -> dict:
    return _get(f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}")


def get_pr_files(repo: str, pr_number: int) -> List[dict]:
    return _get_paginated(f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/files")


def get_file_content(repo: str, path: str, ref: str) -> Optional[str]:
    import base64
    try:
        data = _get(f"{GITHUB_API}/repos/{repo}/contents/{urllib.parse.quote(path)}?ref={ref}")
        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return data.get("content", "")
    except Exception:
        return None


def get_existing_review_comments(repo: str, pr_number: int) -> List[dict]:
    return _get_paginated(f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/comments")


def get_existing_pr_comments(repo: str, pr_number: int) -> List[dict]:
    return _get_paginated(f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments")


def post_review(repo: str, pr_number: int, commit_sha: str, summary_body: str, inline_comments: List[dict]) -> dict:
    # Determine review event from inline comments
    severities = {c.get("severity", "") for c in inline_comments}
    if "critical" in severities:
        event = "REQUEST_CHANGES"
    elif inline_comments:
        event = "COMMENT"
    else:
        event = "APPROVE"

    payload = {
        "commit_id": commit_sha,
        "body": summary_body,
        "event": event,
        "comments": [
            {"path": c["path"], "position": c["position"], "body": c["body"]}
            for c in inline_comments
            if c.get("position")
        ],
    }
    return _post(f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/reviews", payload)


def post_summary_comment(repo: str, pr_number: int, body: str) -> dict:
    return _post(f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments", {"body": body})
