---
description: "Review an implementation document against the project codebase using Doc Quality Gate"
agent: plan
---

You are running a Doc Quality Gate review on an implementation document. Follow these steps exactly.

**Step 1 — Resolve the document path**

The user invoked `/dqg $ARGUMENTS`.

- If `$ARGUMENTS` is a file path (e.g. `docs/plan.md`), use that file.
- If `$ARGUMENTS` is empty or `.`, look for the most recently modified markdown file in the project that looks like an implementation document. Check these locations in order:
  1. `docs/*.md`
  2. `*.md` (project root, excluding README)
  3. `plans/*.md`
  4. `design/*.md`
  Pick the most recently modified one and confirm with the user before proceeding.

**Step 2 — Verify prerequisites**

Run this bash command to check if the LiteLLM proxy is running:

!`curl -s -o /dev/null -w "%{http_code}" http://localhost:4000/health 2>/dev/null || echo "proxy_down"`

If the proxy is down (response is not "200" or similar), tell the user:

"The LiteLLM proxy is not running. Start it first:

```bash
cd ~/Desktop/projects/framework/doc-quality-gate
source .venv/bin/activate
litellm --config config/litellm/config.yaml --port 4000
```

Then run `/dqg` again."

And STOP here.

**Step 3 — Run the Doc Quality Gate review**

Once the proxy is confirmed running, execute the review:

!`cd ~/Desktop/projects/framework/doc-quality-gate && source .venv/bin/activate && python -m app.cli review $1 -t $2 --project . 2>&1`

Where:
- `$1` = the document file path resolved in Step 1
- `$2` = document type (auto-detect if user didn't specify, use empty string)

If the user provided a specific type, use it. Otherwise omit the `-t` flag to auto-detect.

**Step 4 — Read and present the results**

After the review completes, read the generated report:

!`cat $(ls -td ~/Desktop/projects/framework/doc-quality-gate/outputs/runs/*/ | head -1)/report.md`

Then present the findings to the user in this format:

---

## Doc Quality Gate Results

**Score:** X.XX/10 — **PASS/FAIL**
**Action:** implement / revise_again / human_review

### Cross-Reference Issues (Codebase vs Document)
[List the cross_ref issues found — these are the most important for the user]

### Document Quality Issues
[Summarize the main quality issues]

### Dimension Scores
[Brief summary of weakest dimensions]

---

**Step 5 — Ask the user what to do next**

Present these options to the user:
1. **Fix issues** — You (opencode) will revise the implementation document based on the review findings
2. **Revise code** — You will update the actual codebase to align with the document
3. **Just show** — User just wanted to see the review, no action needed

If the user wants to fix issues, read the full issues.json and revised.md from the run output, then revise the implementation document accordingly.

If the user wants to revise code, read the revised document and implement the changes in the actual codebase.
