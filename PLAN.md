# PR Review Agent — Architecture Plan

> **Goal:** An automated AI code reviewer that triggers on every PR across all org repos, posts specific inline comments (like CodeRabbit), avoids duplicates, handles large diffs, and uses AWS Bedrock models via the existing proxy.

---

## 1. My Recommendations (Open Questions Answered)

| Question | Decision | Reasoning |
|---|---|---|
| **Trigger mechanism** | GitHub Actions (Reusable Workflow) | Fastest to ship. No server to maintain. GitHub manages runners. Migrate to GitHub App later if needed. |
| **Hosting** | Serverless (Actions runner) | Zero infra cost. Each PR = ephemeral runner = clean state. Perfect for your timeline. |
| **Primary LLM** | `qwen.qwen3-coder-480b-a35b-v1:0` | Purpose-built for code. 480B params = best reasoning quality for code semantics. Falls back to Llama 4 Maverick if quota hit. |
| **Security / Reasoning LLM** | `us.deepseek.r1-v1:0` | Used exclusively for security-focused second pass on high-risk files. R1's chain-of-thought reasoning = fewer false positives on security. |
| **Triage LLM** | `us.meta.llama3-3-70b-instruct-v1:0` | Cheap, fast. Used only for risk ranking (triage phase). Not for deep review. |
| **Dedup storage** | GitHub comment body (embedded fingerprint) | No external DB. State lives in the PR itself. CodeRabbit does the same. |
| **Scope** | Org-wide via `.github` repo reusable workflow | One workflow definition, all repos call it. Secrets managed at org level. |

---

## 2. How We Compare to CodeRabbit

Understanding CodeRabbit's approach informs our design decisions.

| Feature | CodeRabbit | Our Agent |
|---|---|---|
| Trigger | GitHub App (webhook) | GitHub Actions (event trigger) |
| Summary comment | Yes — walkthrough + sequence diagrams | Yes — PR summary + file risk map |
| Inline comments | Yes — line-level | Yes — line-level with `position` param |
| Incremental review | Yes — only new commits reviewed | Yes — commit SHA tracking via fingerprint |
| Large diff handling | Intelligent triage + lighter reviews on low-complexity files | Hierarchical chunking: triage → deep → post |
| Dedup | State stored in PR itself (ephemeral backend) | Fingerprint embedded in comment HTML comment tag |
| Multi-model | Yes (undisclosed models) | Yes — 3 models with explicit roles |
| Confidence filtering | Noise suppression built-in | Explicit confidence score threshold (≥ 0.75) |
| Context enrichment | Codegraph, commit history, learnings | Surrounding file context via GitHub Contents API |
| Cost | $15–19/month per user (Pro) | ~$0 (Bedrock proxy already available) |

**Key insight from CodeRabbit's architecture:** They store all state *within the pull request itself*, not in their backend, for privacy. We do the same via comment fingerprinting. Their biggest challenge was context window limits — they solve it with summaries + smart prioritization, exactly our hierarchical chunking strategy.

---

## 3. System Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  GITHUB ORG                                                  │
│                                                              │
│  your-username/.github/                                      │
│  └── workflows/                                             │
│      └── pr-review-reusable.yml   ← SINGLE SOURCE OF TRUTH │
│                                                              │
│  repo-A/.github/workflows/pr-review.yml  ← 10-line caller  │
│  repo-B/.github/workflows/pr-review.yml  ← 10-line caller  │
│  repo-N/.github/workflows/pr-review.yml  ← 10-line caller  │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ PR opened / new commit pushed
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  GITHUB ACTIONS RUNNER (ephemeral, per PR)                  │
│                                                              │
│  agent/                                                      │
│  ├── main.py              ← orchestrator / entry point      │
│  ├── github_client.py     ← all GitHub API interactions     │
│  ├── diff_parser.py       ← unified diff → structured hunks │
│  ├── triage.py            ← risk ranking (Llama 3.3 70B)   │
│  ├── reviewer.py          ← deep review (Qwen3 Coder 480B) │
│  ├── security_reviewer.py ← security pass (DeepSeek R1)    │
│  ├── dedup.py             ← fingerprint generation + check  │
│  ├── poster.py            ← GitHub Review API posting       │
│  ├── bedrock_client.py    ← unified Bedrock proxy client    │
│  └── models.py            ← shared dataclasses / schemas    │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ HTTP POST to Bedrock Proxy
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  AWS BEDROCK PROXY                                           │
│  https://ug36pewdpyfaepokw55klfit7y0ltgbn.lambda-url...     │
│                                                              │
│  Triage    → Llama 3.3 70B   (fast, cheap)                 │
│  Review    → Qwen3 Coder 480B (deep code understanding)    │
│  Security  → DeepSeek R1     (chain-of-thought reasoning)  │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Module Breakdown (Modular Programming Pattern)

Each module has a single responsibility, clear inputs/outputs, and no shared mutable state.

---

### 4.1 `models.py` — Shared Data Schemas

**Responsibility:** Define all dataclasses and enums used across modules. No logic, no I/O.

**Contains:**
- `DiffHunk` — one contiguous block of changes in a file (start line, end line, raw lines)
- `ParsedFile` — one file from the PR diff (filename, status, list of hunks, additions count, deletions count)
- `RiskLevel` — enum: `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, `SKIP`
- `TriageResult` — per-file risk level + reasoning string
- `Finding` — one review comment (file, line number, severity, category, confidence score, issue description, fix description, reasoning)
- `ReviewOutput` — list of findings for a file
- `Severity` — enum: `CRITICAL`, `HIGH`, `MEDIUM`, `SUGGESTION`
- `Category` — enum: `SECURITY`, `BUG`, `LOGIC`, `RELIABILITY`, `TEST_COVERAGE`

**Design rule:** All other modules import from `models.py`. No circular imports.

---

### 4.2 `bedrock_client.py` — Unified LLM Interface

**Responsibility:** Single point of contact for all Bedrock proxy calls. Handles model-specific payload formats, retries, and error normalization.

**Interface:**
- `call_llama(prompt, model_id, max_tokens)` → raw text response
- `call_deepseek(messages, max_tokens)` → raw text response  
- `call_qwen(messages, max_tokens)` → raw text response
- `call_with_retry(fn, max_retries=3, backoff_seconds=2)` → wraps any call with exponential backoff

**Payload format handling (per `AVAILABLE_MODELS.md`):**

| Model Family | Format | Key Fields |
|---|---|---|
| Llama (all) | `prompt` string | `model_id`, `prompt`, `max_gen_len` |
| DeepSeek R1 | `messages` array | `model_id`, `messages`, `max_tokens` |
| Qwen3 Coder | `messages` array | `model_id`, `messages`, `max_tokens` |

**Design rule:** Never let model-specific payload logic leak into other modules. All callers just pass text.

---

### 4.3 `github_client.py` — GitHub API Interface

**Responsibility:** All reads and writes to GitHub API. Uses `GITHUB_TOKEN` (auto-injected by Actions).

**Interface:**
- `get_pr_metadata(repo, pr_number)` → PR title, description, base SHA, head SHA, author
- `get_pr_files(repo, pr_number)` → list of files with raw patch strings
- `get_file_content(repo, path, ref)` → full file content at a given commit (for context enrichment)
- `get_existing_review_comments(repo, pr_number)` → all existing inline comments on the PR
- `get_existing_pr_comments(repo, pr_number)` → all top-level PR comments
- `post_review(repo, pr_number, commit_sha, summary_body, inline_comments)` → single batch review post
- `post_summary_comment(repo, pr_number, body)` → top-level summary comment (not inline)

**Design rule:** No business logic here. Just HTTP calls. Pagination handled internally.

---

### 4.4 `diff_parser.py` — Diff Parsing

**Responsibility:** Convert raw GitHub patch strings (unified diff format) into structured `ParsedFile` objects with exact line numbers.

**Why this is non-trivial:** The GitHub API returns patches in unified diff format with `@@ -old_start,old_count +new_start,new_count @@` headers. Line numbers in the new file must be computed by walking the hunk. The `position` parameter for GitHub inline comments is the line offset within the diff, not the file line number — this distinction matters.

**Interface:**
- `parse_patch(filename, patch_string)` → `ParsedFile`
- `get_diff_position(parsed_file, new_line_number)` → diff position integer (for GitHub API)
- `extract_context_window(parsed_file, hunk_index, context_lines=20)` → surrounding lines for LLM

**Design rule:** Pure functions only. No I/O, no network calls. Fully unit-testable.

---

### 4.5 `triage.py` — Risk Ranking

**Responsibility:** Given the list of all files in the PR, rank them by review priority. Uses the cheapest/fastest model (Llama 3.3 70B) because this is a classification task, not deep reasoning.

**Input:** List of `ParsedFile` objects (filenames + line counts + file status only — no full patch)

**Output:** Dict mapping filename → `TriageResult` (risk level + reasoning)

**Triage signals considered:**
- Filename patterns: `auth`, `token`, `secret`, `password`, `jwt`, `crypto`, `payment`, `admin` → bump risk
- File type: `.sql`, `.env`, migration files → HIGH by default
- File status: `deleted` files → lower priority; `renamed` → medium
- Change volume: files with >200 additions → flag for chunking
- Test files: deprioritize unless coverage gaps are the focus
- Auto-generated: `package-lock.json`, `*.min.js`, `dist/`, `__pycache__` → SKIP

**Prompt strategy:** Send ALL filenames + metadata in one call. Ask for JSON output with risk level per file. This costs ~500 tokens total regardless of PR size — cheap triage before expensive review.

**Design rule:** Triage never reads patch content. Only metadata. Keep it fast.

---

### 4.6 `reviewer.py` — Deep Code Review

**Responsibility:** Per-file deep review using Qwen3 Coder 480B. Produces structured findings with line numbers, severity, and fix instructions.

**Input:** Single `ParsedFile` + optional full file content (for context)

**Output:** `ReviewOutput` (list of `Finding` objects)

**Review focus areas (what to flag):**
- Bugs: null dereference, off-by-one errors, unhandled exceptions, incorrect boolean logic
- Logic errors: wrong condition direction, missing edge cases, dead code that should be alive
- Reliability: missing error handling, unbounded loops, resource leaks, missing timeouts
- Test coverage: risky changes (auth, payment, data mutation) with no corresponding test changes

**What NOT to flag (calibration):**
- Style preferences (naming conventions, formatting)
- Subjective architectural opinions
- Issues already caught by linters (ESLint, Pylint)
- Low-confidence observations (suppress if confidence < 0.75)

**Context enrichment strategy:**
- For HIGH risk files: fetch full file via `github_client.get_file_content()` and prepend as context
- For MEDIUM risk: send hunk + 20 surrounding lines from `diff_parser.extract_context_window()`
- For LOW risk: patch only, no extra context

**Chunking for large files (>300 lines changed):**
- Split into logical hunk groups of max ~3,000 tokens each
- Review each chunk independently
- Merge findings, deduplicating by line number

**Output schema (strict JSON):**
```
{
  "findings": [
    {
      "file": "string",
      "line": integer,
      "severity": "critical|high|medium|suggestion",
      "category": "security|bug|logic|reliability|test_coverage",
      "confidence": float (0.0 to 1.0),
      "issue": "string — what is wrong",
      "fix": "string — exactly what to do",
      "reasoning": "string — why this is a problem"
    }
  ]
}
```

**Design rule:** Reviewer module never calls GitHub API. It receives parsed data, returns findings. I/O is the orchestrator's job.

---

### 4.7 `security_reviewer.py` — Security Second Pass

**Responsibility:** A focused second pass on HIGH/CRITICAL risk files using DeepSeek R1. R1's chain-of-thought reasoning is better suited for catching subtle security vulnerabilities (SSRF, injection, auth bypass, privilege escalation) than standard instruction-following models.

**When triggered:** Only for files triaged as `CRITICAL` or `HIGH` AND in security-sensitive categories (auth, payments, database, infra configs).

**Focus areas (OWASP-aligned):**
- Injection: SQL, command, LDAP, XML injection
- Auth: broken authentication, missing authorization checks, JWT weaknesses
- Sensitive data exposure: hardcoded secrets, unencrypted storage, logging of sensitive fields
- SSRF / IDOR / path traversal
- Insecure deserialization
- Missing rate limiting on sensitive endpoints

**Output schema:** Same as `reviewer.py`. Findings merged into final output.

**Design rule:** Security pass runs *after* main review, not in parallel, to avoid duplicate findings on the same lines. Dedup handles any overlap.

---

### 4.8 `dedup.py` — Duplicate Prevention

**Responsibility:** Prevent the same comment from being posted when a PR is updated and the agent re-runs.

**How it works:**

1. Before posting, fetch all existing review comments from GitHub
2. Each existing comment body is scanned for an embedded HTML comment fingerprint: `<!-- fp:XXXXXXXXXXXXXXXX -->`
3. Build a set of all previously posted fingerprints
4. For each new finding, compute its fingerprint
5. Only post findings whose fingerprint is NOT in the existing set

**Fingerprint computation:**
- Input: `file + line_number + category + first_50_chars_of_issue`
- Algorithm: SHA-256 → take first 16 hex chars
- Stable: same issue on same line always produces same fingerprint across runs
- Embedded in comment: appended as `<!-- fp:a3f9b2c1d4e5f678 -->` (invisible to readers)

**Incremental review logic:**
- On re-run (new commit pushed), the agent also checks which files changed in the NEW commit only
- Only re-reviews files touched by the latest commit (not already-reviewed unchanged files)
- This mirrors CodeRabbit's incremental review behavior

**Design rule:** Dedup is a filter, not a reviewer. It never generates findings. Pure set operations.

---

### 4.9 `poster.py` — GitHub Review Posting

**Responsibility:** Format findings into GitHub API payload and post as a single batch review.

**GitHub API used:** `POST /repos/{owner}/{repo}/pulls/{pr_number}/reviews`

**Why batch (not individual comments):**
- One API call = one rate limit hit (not N)
- Appears as a single review from the bot (cleaner UX)
- Atomic — either all comments post or none do (no partial state)

**Comment format per finding:**
```
**[{SEVERITY}/{CATEGORY}]** {issue}

**Fix:** {fix}

**Why:** {reasoning}

<!-- fp:{fingerprint} -->
```

**PR-level summary comment (top-level, not inline):**
Posted separately as a regular comment (not part of the review batch). Contains:
- Total findings count by severity
- List of reviewed files with their risk level
- Commit SHA reviewed
- Which files were skipped and why (auto-generated, lock files, etc.)
- Agent version tag for traceability

**Severity → GitHub review event mapping:**
- Any `CRITICAL` finding present → `REQUEST_CHANGES`
- Only `HIGH`/`MEDIUM` → `COMMENT`
- Only `SUGGESTION` → `COMMENT`
- No findings → `APPROVE` with note "No issues found"

**Design rule:** Poster never generates content. It only formats and transmits. Retry on 5xx, fail-fast on 4xx.

---

### 4.10 `main.py` — Orchestrator

**Responsibility:** Compose all modules into the full pipeline. Called by the GitHub Actions runner. No business logic — just pipeline wiring.

**Pipeline steps (in order):**

```
1. Read env vars: GITHUB_TOKEN, REPO, PR_NUMBER, BEDROCK_PROXY_URL
2. github_client.get_pr_metadata() → PR context
3. github_client.get_pr_files() → raw file list + patches
4. diff_parser.parse_patch() per file → list of ParsedFile
5. triage.rank_files() → TriageResult per file
6. Filter: SKIP files removed from pipeline
7. For each non-skipped file (ordered by risk, highest first):
   a. reviewer.review_file() → ReviewOutput
   b. If CRITICAL/HIGH security file: security_reviewer.review_file() → additional findings
   c. Merge findings from (a) and (b)
8. dedup.filter_findings() → remove already-posted findings
9. Filter: drop findings with confidence < 0.75
10. poster.post_review() → single GitHub API call
11. poster.post_summary_comment() → top-level PR summary
```

**Error handling:**
- If Qwen3 Coder fails → fallback to `us.meta.llama4-maverick-17b-instruct-v1:0`
- If entire pipeline fails → post a single top-level comment: "PR Review Agent encountered an error. Manual review required."
- Never fail silently

---

## 5. GitHub Actions Setup

### 5.1 Central Reusable Workflow (`your-username/.github/workflows/pr-review-reusable.yml`)

**Trigger:** `workflow_call` (called by other repos)

**Permissions needed:**
- `contents: read` — fetch file contents
- `pull-requests: write` — post review comments

**Secrets passed through:**
- `BEDROCK_PROXY_URL` — the Lambda function URL (org-level secret)

**Auto-available (no config needed):**
- `GITHUB_TOKEN` — injected per-run by GitHub, scoped to current repo

**Steps:**
1. `actions/checkout@v4`
2. `actions/setup-python@v5` with Python 3.11
3. `pip install` dependencies (pinned versions in `requirements.txt`)
4. `python agent/main.py`

### 5.2 Per-Repo Caller Workflow (copy-paste once per repo)

**File:** `.github/workflows/pr-review.yml`

**Trigger events:**
- `pull_request` types: `opened`, `synchronize`, `reopened`

**Calls:** `your-username/.github/workflows/pr-review-reusable.yml@main`

**Secrets inherited:** `BEDROCK_PROXY_URL` from org-level secrets

### 5.3 Org-Level Secret Setup

Set once in: Org Settings → Secrets → Actions → New org secret

| Secret Name | Value | Access |
|---|---|---|
| `BEDROCK_PROXY_URL` | `https://ug36pewdpyfaepokw55klfit7y0ltgbn.lambda-url.us-west-2.on.aws/` | All repositories |

### 5.4 Repo Permission Config (set org-wide)

Org Settings → Actions → General → Workflow permissions:
- `Read and write permissions` ✅
- `Allow GitHub Actions to create and approve pull requests` ✅

---

## 6. Large PR Handling Strategy

This is the hardest problem and where most naive implementations fail.

### Phase 1: Triage (always, all files)
- Cost: ~500–800 tokens flat, regardless of PR size
- Input: filenames + line counts only (no patch content)
- Output: risk ranking + skip list
- Skipped files never enter the pipeline

### Phase 2: Budget allocation
- Total token budget per PR: ~80,000 tokens (safe for Qwen3 Coder)
- Budget consumed by risk level: CRITICAL (full file + patch), HIGH (patch + context), MEDIUM (patch only), LOW (skip or patch summary)
- If budget would be exceeded: drop LOW files entirely, reduce MEDIUM to summary only

### Phase 3: Chunking for large single files
- If one file has >300 lines changed: split into hunk groups of ~3,000 tokens each
- Each chunk reviewed independently
- Findings merged at the end
- Duplicate findings on boundary lines removed by dedup

### Phase 4: Summary-only fallback
- If a file is too large to review at any context budget: post a single top-level note in the summary comment listing the file as "too large for automated inline review — manual review recommended"

**The result:** PRs of any size get *some* review. Critical files always get full review. Large/low-risk files get proportionally lighter treatment. This matches CodeRabbit's documented behavior.

---

## 7. Model Selection Rationale

### Primary: Qwen3 Coder 480B A35B
- Purpose-built for code (not a general model prompted for code)
- 480B parameters with MoE architecture (35B active) = high quality at manageable latency
- Best for: logic errors, bug detection, test coverage gaps, reliability issues

### Secondary: DeepSeek R1
- Chain-of-thought reasoning model
- Explicitly reasons before answering = lower false positive rate on ambiguous security issues
- Best for: security vulnerabilities where subtle context matters (SSRF, auth bypass, injection)
- Trade-off: slower than Qwen3, used only for high-risk security files

### Triage: Llama 3.3 70B
- Fast, small, cheap
- Classification task (risk ranking) doesn't need 480B params
- Keeps triage latency under 5 seconds

### Fallback: Llama 4 Maverick 17B
- Used if Qwen3 Coder quota is exhausted or returns error
- Smaller but capable; acceptable degradation for non-critical files

---

## 8. File Structure

```
your-username/.github/
├── workflows/
│   └── pr-review-reusable.yml
│
agent/
├── main.py                  ← pipeline orchestrator
├── models.py                ← dataclasses, enums, schemas
├── bedrock_client.py        ← Bedrock proxy HTTP client
├── github_client.py         ← GitHub REST API client
├── diff_parser.py           ← unified diff → ParsedFile
├── triage.py                ← risk ranking (Llama 3.3 70B)
├── reviewer.py              ← deep review (Qwen3 Coder 480B)
├── security_reviewer.py     ← security pass (DeepSeek R1)
├── dedup.py                 ← fingerprint dedup
├── poster.py                ← GitHub Review API posting
│
prompts/
├── triage_prompt.txt        ← triage system prompt
├── review_prompt.txt        ← review system prompt
├── security_prompt.txt      ← security review system prompt
│
tests/
├── test_diff_parser.py      ← unit tests (no network)
├── test_dedup.py            ← unit tests (no network)
├── test_triage.py           ← unit tests with mock LLM
├── fixtures/
│   ├── sample_patch.txt     ← real diff fixtures
│   └── sample_findings.json ← expected output fixtures
│
requirements.txt             ← pinned dependencies
README.md                    ← setup instructions
```

---

## 9. Build Order (MVP-First)

Given time constraints, build in this exact order:

| Phase | Modules | Deliverable | Est. Time |
|---|---|---|---|
| 1 | `models.py`, `diff_parser.py` | Parse any GitHub patch correctly with line numbers | 2–3 hours |
| 2 | `bedrock_client.py` | Verify all 3 model families respond correctly | 1 hour |
| 3 | `github_client.py` + `poster.py` | Fetch PR + post a test review comment manually | 2 hours |
| 4 | `reviewer.py` (Qwen3 only, no triage yet) | Full review on a single file | 3 hours |
| 5 | `dedup.py` | Fingerprint system working | 1 hour |
| 6 | `main.py` (basic pipeline) | End-to-end on small PR | 2 hours |
| 7 | GitHub Actions workflow | Trigger on real PR | 1 hour |
| 8 | `triage.py` | Risk ranking + skip logic | 2 hours |
| 9 | `security_reviewer.py` | DeepSeek R1 security pass | 2 hours |
| 10 | Large PR chunking in `reviewer.py` | Handle 500+ line PRs | 2 hours |

**Total: ~18–20 hours. MVP (phases 1–7) is functional in ~12 hours.**

---

## 10. Key Design Principles

**Single responsibility per module.** Each file does one thing. `reviewer.py` reviews. `poster.py` posts. `dedup.py` deduplicates. Never mix.

**No shared mutable state.** Every function takes inputs and returns outputs. No global variables. Safe for future parallelization.

**Fail loudly, never silently.** Every error posts a visible comment on the PR. Authors always know if the agent failed.

**State lives in GitHub.** No database, no Redis, no external state. Fingerprints embedded in comments = fully recoverable on re-run.

**Prompt files are separate from code.** `prompts/` directory holds all LLM instructions. Iterate on prompts without touching Python.

**Calibration over coverage.** Post fewer, higher-confidence comments. A 30–40% action rate on comments is the target (CodeRabbit's own benchmark). Noise trains developers to ignore the bot.
