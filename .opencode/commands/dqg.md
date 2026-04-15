---
description: "Review an implementation document against the project codebase using Doc Quality Gate"
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

**Step 2 — Find the DQG installation**

The DQG project is a standalone tool, NOT inside the user's project. Find it by running:

!`python -c "from pathlib import Path; candidates=[Path.home()/'.config/opencode/commands/dqg.md', Path.home()/'Desktop/doc-quailty-gate', Path.home()/'Desktop/doc-quality-gate', Path.home()/'projects/doc-quality-gate', Path.home()/'repos/doc-quality-gate']; found=[c for c in candidates if c.exists()]; print(found[0].parent if found else 'NOT_FOUND')"`

If the result is NOT_FOUND, check the AGENTS.md file in the current project — it may contain the DQG path. If still not found, ask the user: "Where is the Doc Quality Gate project installed?"

Once you have the DQG directory path, save it as DQG_DIR for all subsequent steps.

**Step 3 — Check LiteLLM proxy**

Run this EXACT Python command. Do NOT use curl. Do NOT use Invoke-WebRequest. Do NOT use /health (use /health/liveliness):

!`python -c "import httpx; r=httpx.get('http://localhost:4000/health/liveliness',timeout=3); print('PROXY_OK' if r.status_code==200 else 'PROXY_DOWN')"`

If the output is NOT "PROXY_OK", tell the user:

"LiteLLM proxy is not running. Run the setup script first:
- **Windows:** Double-click `DQG_DIR/scripts/win/setup.bat`
- **macOS/Linux:** `bash DQG_DIR/scripts/mac/setup.sh`

Then run `/dqg` again."

And STOP here. Do NOT continue.

**Step 4 — Run the DQG review**

Run the review command using the Bash tool with workdir set to DQG_DIR. Replace DOC_PATH with the document path from Step 1:

!`python -m app.cli review DOC_PATH --project . 2>&1`

If the user specified a document type (feature_spec, implementation_plan, architecture_change, etc.), add `-t TYPE` before `--project`.

**Step 5 — Read and present the results**

Read the report from the latest run output in DQG_DIR:

!`python -c "from pathlib import Path; runs=sorted(Path('outputs/runs').iterdir(), key=lambda p:p.stat().st_mtime); d=runs[-1] if runs else None; print((d/'report.md').read_text()[:8000]) if d and (d/'report.md').exists() else print('No report found')"`

Present the findings:

## Doc Quality Gate Results

**Score:** X.XX/10 — **PASS/FAIL**
**Action:** implement / revise_again / human_review

### Cross-Reference Issues (Codebase vs Document)
[List the cross_ref issues found]

### Document Quality Issues
[Summarize the main quality issues]

### Dimension Scores
[Brief summary of weakest dimensions]

**Step 6 — Ask the user what to do next**

1. **Fix issues** — Revise the implementation document based on the review findings
2. **Revise code** — Update the actual codebase to align with the document
3. **Just show** — No action needed

If the user wants to fix issues, read `issues.json` and `revised.md` from the run output directory, then revise the implementation document.
