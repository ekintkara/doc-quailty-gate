---
description: "Review an implementation document against the project codebase using Doc Quality Gate"
agent: plan
---

You are running a Doc Quality Gate (DQG) review. Follow these steps EXACTLY. Do NOT use curl. Do NOT use bash-only commands. Use ONLY the Python commands shown below.

**Step 1 — Resolve the document path**

The user invoked `/dqg $ARGUMENTS`.

- If `$ARGUMENTS` is a file path (e.g. `docs/plan.md`), use that file.
- If `$ARGUMENTS` is empty or `.`, look for the most recently modified markdown file in the project that looks like an implementation document. Check these locations in order:
  1. `docs/*.md`
  2. `*.md` (project root, excluding README)
  3. `plans/*.md`
  4. `design/*.md`
  Pick the most recently modified one and confirm with the user before proceeding.

**Step 2 — Check LiteLLM proxy**

Run this EXACT command — do NOT modify it, do NOT use curl:

!`python -c "import httpx; r=httpx.get('http://localhost:4000/health/liveliness',timeout=3); print('PROXY_OK' if r.status_code==200 else 'PROXY_DOWN')"`

If the output is NOT "PROXY_OK", tell the user:

"LiteLLM proxy is not running. Run the setup script first:
- **Windows:** Double-click `doc-quality-gate/scripts/win/setup.bat`
- **macOS/Linux:** `bash doc-quality-gate/scripts/mac/setup.sh`

Then run `/dqg` again."

And STOP here. Do NOT continue to Step 3.

**Step 3 — Run the DQG review**

The DQG project is located at the doc-quality-gate directory. Find it by checking: the current working directory, or a sibling directory named `doc-quality-gate`, or ask the user for its path.

Once you know the DQG directory path, run the review with the Bash tool. Replace DOC_PATH with the document path from Step 1:

!`python -m app.cli review DOC_PATH --project . 2>&1`

IMPORTANT: This command MUST be run from inside the DQG project directory with the venv activated. Use the Bash tool with the workdir parameter set to the DQG directory. If the DQG venv is at `.venv`, make sure it is activated.

If the user specified a document type (feature_spec, implementation_plan, architecture_change, etc.), add `-t TYPE` before `--project`.

**Step 4 — Read and present the results**

After the review completes, read the report from the latest run output:

!`python -c "from pathlib import Path; runs=sorted(Path('outputs/runs').iterdir(), key=lambda p:p.stat().st_mtime); d=runs[-1] if runs else None; print((d/'report.md').read_text()[:8000]) if d and (d/'report.md').exists() else print('No report found')"`

Present the findings in this format:

---

## Doc Quality Gate Results

**Score:** X.XX/10 — **PASS/FAIL**
**Action:** implement / revise_again / human_review

### Cross-Reference Issues (Codebase vs Document)
[List the cross_ref issues found]

### Document Quality Issues
[Summarize the main quality issues]

### Dimension Scores
[Brief summary of weakest dimensions]

---

**Step 5 — Ask the user what to do next**

Present these options:
1. **Fix issues** — Revise the implementation document based on the review findings
2. **Revise code** — Update the actual codebase to align with the document
3. **Just show** — No action needed

If the user wants to fix issues, read `issues.json` and `revised.md` from the run output directory, then revise the implementation document accordingly.
