---
description: "Review an implementation document against the project codebase using Doc Quality Gate"
---

You are running a Doc Quality Gate (DQG) review.

CRITICAL RULES — READ THESE BEFORE PROCEEDING:
1. Do NOT run `start.ps1`, `start.bat`, `start.sh`, or any setup script. They will block the terminal.
2. Do NOT start LiteLLM proxy or web server manually.
3. Do NOT write your own Python commands or HTTP calls.
4. The ONLY commands you need are in Steps 2 and 3 below.

**Step 1 — Resolve the document path**

The user invoked `/dqg $ARGUMENTS`.

Parse the arguments:
- The first non-flag argument is the document path (e.g. `docs/plan.md` or `./plan.md`).
- If no document path is provided, look for the most recently modified markdown file in the project that looks like an implementation document. Check these locations in order:
  1. `docs/*.md`
  2. `*.md` (project root, excluding README)
  3. `plans/*.md`
  4. `design/*.md`
  Pick the most recently modified one and confirm with the user before proceeding.
- `--cp PATH` — (optional) Path to a structured domain context directory (e.g. `C:\OBTaskManager\obiletcontext`). This contains architecture, conventions, domain docs etc.

Save the absolute document path as DOC_PATH, the current working directory (project root) as PROJECT_PATH, and any context path as CONTEXT_PATH.

**Step 2 — Find DQG script and launch the review**

First, find the DQG script path:

!`python -c "from pathlib import Path; p=Path.home()/'.config'/'opencode'/'dqg_home'; print(Path(p.read_text('utf-8-sig').strip())/'scripts'/'dqg_run.py')"`

Then launch the review. This starts services if needed and kicks off the review asynchronously. It returns immediately with a REVIEW_ID:

!`python DQG_SCRIPT launch DOC_PATH --project PROJECT_PATH`{{if CONTEXT_PATH was provided, append: `--cp CONTEXT_PATH`}}

If the user specified a document type, add `--type TYPE`.

Save the REVIEW_ID from the output.

**Step 3 — Poll for results (repeat until complete)**

Poll for results using the REVIEW_ID from Step 2. Each poll checks a few times (~1 minute). If the review is still running, run the command again. Repeat until you get REVIEW_COMPLETE or REVIEW_FAILED:

!`python DQG_SCRIPT poll REVIEW_ID --max-attempts 6`

If output says `POLL_INCOMPLETE`, just run the same command again. The review pipeline takes 5-15 minutes total.

Set timeout to 120000ms (2 minutes) for each poll command.

**Step 4 — Present the results**

Parse the output from the poll command and present clearly:

## Doc Quality Gate Results

**Score:** X.XX/10 — **PASS/FAIL**
**Action:** implement / revise_again / human_review

### Cross-Reference Issues (Codebase vs Document)
[List the issues]

### Document Quality Issues
[Summarize the main quality issues]

### Dimension Scores
[Brief summary of weakest dimensions]

**Step 5 — Ask the user what to do next**

1. **Fix issues** — Revise the implementation document based on the review findings
2. **Revise code** — Update the actual codebase to align with the document
3. **Just show** — No action needed
