from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import structlog
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import load_app_config
from app.orchestrator import Orchestrator
from app.utils.files import find_run_dir

logger = structlog.get_logger("web")

app = FastAPI(title="Doc Quality Gate", version="0.1.0")

UPLOAD_DIR = Path("/tmp/dqg_uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

_static = Path(__file__).parent / "static"
if _static.exists():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")


def _get_orchestrator() -> Orchestrator:
    config = load_app_config()
    return Orchestrator(config)


@app.get("/", response_class=HTMLResponse)
async def index():
    return _render_page("review")


@app.get("/review", response_class=HTMLResponse)
async def review_page():
    return _render_page("review")


@app.get("/runs", response_class=HTMLResponse)
async def runs_page():
    return _render_page("runs")


@app.get("/run/{run_id}", response_class=HTMLResponse)
async def run_detail(run_id: str):
    return _render_page("run_detail", run_id=run_id)


@app.post("/api/review")
async def api_review(
    file: Optional[UploadFile] = File(None),
    content: Optional[str] = Form(None),
    doc_type: str = Form("feature_spec"),
    project_path: Optional[str] = Form(None),
):
    if not file and not content:
        raise HTTPException(400, "Provide a file or paste document content")

    if file:
        file_content = (await file.read()).decode("utf-8")
        filename = file.filename or "document.md"
    else:
        file_content = content or ""
        filename = "document.md"

    if not file_content.strip():
        raise HTTPException(400, "Document is empty")

    upload_path = UPLOAD_DIR / filename
    upload_path.write_text(file_content, encoding="utf-8")

    try:
        orch = _get_orchestrator()
        artifacts = orch.run(str(upload_path), doc_type, project_path=project_path)
        return _artifacts_to_response(artifacts)
    except Exception as e:
        logger.error("review_failed", error=str(e))
        raise HTTPException(500, str(e))


@app.get("/api/runs")
async def api_list_runs():
    config = load_app_config()
    runs_dir = Path(config.output_base_dir)
    if not runs_dir.exists():
        return {"runs": []}

    runs = []
    for d in sorted(runs_dir.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        meta_path = d / "metadata.json"
        score_path = d / "scorecard.json"
        meta = {}
        score = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        if score_path.exists():
            try:
                score = json.loads(score_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        runs.append(
            {
                "run_id": d.name,
                "timestamp": meta.get("timestamp", ""),
                "document_type": meta.get("document_type", ""),
                "status": meta.get("execution_status", ""),
                "overall_score": score.get("overall_score"),
                "passed": score.get("passed"),
                "recommended_next_action": score.get("recommended_next_action", ""),
            }
        )

    return {"runs": runs}


@app.get("/api/runs/{run_id}")
async def api_get_run(run_id: str):
    config = load_app_config()
    run_dir = find_run_dir(config.output_base_dir, run_id)
    if not run_dir:
        raise HTTPException(404, f"Run not found: {run_id}")

    result: dict = {"run_id": run_id}

    for name in ["metadata", "scorecard", "issues", "validations"]:
        p = run_dir / f"{name}.json"
        if p.exists():
            try:
                result[name] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                result[name] = None

    for name in ["original.md", "revised.md"]:
        p = run_dir / name
        if p.exists():
            result[name] = p.read_text(encoding="utf-8")

    return result


@app.get("/api/runs/{run_id}/report")
async def api_get_report(run_id: str):
    config = load_app_config()
    run_dir = find_run_dir(config.output_base_dir, run_id)
    if not run_dir:
        raise HTTPException(404, f"Run not found: {run_id}")

    html_path = run_dir / "report.html"
    if html_path.exists():
        return FileResponse(str(html_path), media_type="text/html")

    md_path = run_dir / "report.md"
    if md_path.exists():
        return FileResponse(str(md_path), media_type="text/markdown")

    raise HTTPException(404, "Report not found")


@app.get("/api/smoke")
async def api_smoke_test():
    try:
        orch = _get_orchestrator()
        results = orch.smoke_test()
        return results
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/demo")
async def api_demo():
    try:
        config = load_app_config()
        orch = Orchestrator(config)
        results = []
        examples = {
            "feature_spec": "examples/feature_spec/sample.md",
            "implementation_plan": "examples/implementation_plan/sample.md",
            "architecture_change": "examples/architecture_change/sample.md",
        }
        for doc_type, path in examples.items():
            if Path(path).exists():
                artifacts = orch.run(path, doc_type)
                results.append(_artifacts_to_response(artifacts))
        return {"results": results}
    except Exception as e:
        logger.error("demo_failed", error=str(e))
        raise HTTPException(500, str(e))


def _artifacts_to_response(artifacts) -> dict:
    scorecard = artifacts.scorecard
    return {
        "run_id": artifacts.run_id,
        "output_dir": artifacts.output_dir,
        "issues_count": len(artifacts.issues),
        "valid_issues": sum(1 for v in artifacts.validations if v.decision.value == "valid"),
        "scorecard": scorecard.model_dump() if scorecard else None,
        "passed": scorecard.passed if scorecard else None,
        "overall_score": scorecard.overall_score if scorecard else None,
        "recommended_next_action": (scorecard.recommended_next_action.value if scorecard else None),
    }


def _render_page(page: str, **kwargs) -> str:
    if page == "review":
        return _review_html()
    elif page == "runs":
        return _runs_html()
    elif page == "run_detail":
        return _run_detail_html(kwargs.get("run_id", ""))
    return "<html><body>Not found</body></html>"


def _review_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Doc Quality Gate</title>
<style>
  :root { --bg: #0f172a; --surface: #1e293b; --border: #334155; --text: #e2e8f0; --dim: #94a3b8; --accent: #3b82f6; --green: #22c55e; --red: #ef4444; --yellow: #eab308; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  nav { background: var(--surface); border-bottom: 1px solid var(--border); padding: 0.75rem 1.5rem; display: flex; gap: 1.5rem; align-items: center; }
  nav .brand { font-weight: 700; font-size: 1.1rem; color: var(--accent); }
  nav a { color: var(--dim); text-decoration: none; font-size: 0.9rem; transition: color 0.15s; }
  nav a:hover, nav a.active { color: var(--text); }
  .container { max-width: 960px; margin: 2rem auto; padding: 0 1.5rem; }
  h1 { font-size: 1.6rem; margin-bottom: 1.5rem; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem; }
  label { display: block; font-size: 0.85rem; color: var(--dim); margin-bottom: 0.4rem; font-weight: 500; }
  select, textarea, input[type="file"] { width: 100%; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 0.6rem; color: var(--text); font-size: 0.9rem; margin-bottom: 1rem; }
  textarea { min-height: 280px; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 0.85rem; resize: vertical; }
  select:focus, textarea:focus { outline: none; border-color: var(--accent); }
  .row { display: flex; gap: 1rem; }
  .row > * { flex: 1; }
  button { background: var(--accent); color: #fff; border: none; border-radius: 6px; padding: 0.7rem 1.5rem; font-size: 0.95rem; font-weight: 600; cursor: pointer; transition: opacity 0.15s; }
  button:hover { opacity: 0.9; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-secondary { background: var(--border); }
  .result-box { margin-top: 1.5rem; display: none; }
  .result-box.visible { display: block; }
  .gate-pass { border-left: 4px solid var(--green); }
  .gate-fail { border-left: 4px solid var(--red); }
  .score-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.75rem; margin-top: 1rem; }
  .score-item { background: var(--bg); border-radius: 6px; padding: 0.75rem; text-align: center; }
  .score-item .label { font-size: 0.75rem; color: var(--dim); margin-bottom: 0.3rem; text-transform: uppercase; letter-spacing: 0.05em; }
  .score-item .value { font-size: 1.5rem; font-weight: 700; }
  .score-good { color: var(--green); }
  .score-ok { color: var(--yellow); }
  .score-bad { color: var(--red); }
  .bar-track { height: 4px; background: var(--border); border-radius: 2px; margin-top: 0.4rem; }
  .bar-fill { height: 100%; border-radius: 2px; }
  .spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.6s linear infinite; margin-right: 0.5rem; vertical-align: middle; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .status-msg { padding: 0.5rem 0; font-size: 0.9rem; color: var(--dim); }
  .meta-row { display: flex; gap: 2rem; margin-top: 0.5rem; font-size: 0.9rem; color: var(--dim); }
  .meta-row strong { color: var(--text); }
  .issues-table { width: 100%; border-collapse: collapse; margin-top: 1rem; font-size: 0.85rem; }
  .issues-table th, .issues-table td { padding: 0.5rem; border-bottom: 1px solid var(--border); text-align: left; }
  .issues-table th { color: var(--dim); font-weight: 500; text-transform: uppercase; font-size: 0.75rem; letter-spacing: 0.05em; }
  .badge { display: inline-block; padding: 0.1rem 0.4rem; border-radius: 3px; font-size: 0.75rem; font-weight: 600; }
  .badge-critical { background: #7f1d1d; color: #fca5a5; }
  .badge-high { background: #713f12; color: #fde047; }
  .badge-medium { background: #1e3a5f; color: #93c5fd; }
  .badge-low { background: #3b0764; color: #d8b4fe; }
  .badge-pass { background: #14532d; color: #86efac; }
  .badge-fail { background: #7f1d1d; color: #fca5a5; }
  .tab-bar { display: flex; gap: 0; margin-bottom: 1rem; }
  .tab { padding: 0.5rem 1rem; background: var(--bg); border: 1px solid var(--border); cursor: pointer; font-size: 0.85rem; color: var(--dim); }
  .tab:first-child { border-radius: 6px 0 0 6px; }
  .tab:last-child { border-radius: 0 6px 6px 0; }
  .tab.active { background: var(--accent); color: #fff; border-color: var(--accent); }
</style>
</head>
<body>
<nav>
  <div class="brand">DQG</div>
  <a href="/" class="active">Review</a>
  <a href="/runs">Runs</a>
  <a href="#" onclick="runSmoke(); return false;">Smoke Test</a>
</nav>

<div class="container">
  <h1>Review Document</h1>

  <div class="card">
    <form id="reviewForm" onsubmit="submitReview(event)">
      <div class="row">
        <div>
          <label>Document Type</label>
          <select id="docType">
            <option value="">Auto-detect</option>
            <option value="feature_spec">Feature Specification</option>
            <option value="implementation_plan">Implementation Plan</option>
            <option value="architecture_change">Architecture Change</option>
            <option value="refactor_plan">Refactor Plan</option>
            <option value="migration_plan">Migration Plan</option>
            <option value="incident_action_plan">Incident Action Plan</option>
            <option value="custom">Custom</option>
          </select>
        </div>
        <div>
          <label>Upload File (optional)</label>
          <input type="file" id="fileInput" accept=".md,.txt,.markdown">
        </div>
      </div>

      <div class="row" style="margin-bottom:1rem;">
        <div>
          <label>Project Path (for cross-reference analysis)</label>
          <input type="text" id="projectPath" placeholder="/path/to/your/project" style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:0.6rem;color:var(--text);font-size:0.9rem;">
        </div>
      </div>

      <label>Or paste document content</label>
      <textarea id="docContent" placeholder="Paste your implementation document here..."></textarea>

      <div style="display:flex; gap:0.75rem; align-items:center;">
        <button type="submit" id="submitBtn">Run Review</button>
        <button type="button" class="btn-secondary" onclick="runDemo()">Run Demo</button>
        <span id="statusMsg" class="status-msg"></span>
      </div>
    </form>
  </div>

  <div id="resultBox" class="card result-box">
    <div id="gateResult"></div>
    <div id="scoreGrid" class="score-grid"></div>
    <div id="issuesSection"></div>
    <div class="meta-row" id="metaRow"></div>
  </div>
</div>

<script>
async function submitReview(e) {
  e.preventDefault();
  const btn = document.getElementById('submitBtn');
  const status = document.getElementById('statusMsg');
  const fileInput = document.getElementById('fileInput');
  const docType = document.getElementById('docType').value;
  const docContent = document.getElementById('docContent').value;
  const projectPath = document.getElementById('projectPath').value;

  if (!fileInput.files.length && !docContent.trim()) {
    alert('Upload a file or paste document content.');
    return;
  }

  btn.disabled = true;
  status.innerHTML = '<span class="spinner"></span>Running pipeline...';

  const formData = new FormData();
  if (fileInput.files.length) {
    formData.append('file', fileInput.files[0]);
  } else {
    formData.append('content', docContent);
  }
  formData.append('doc_type', docType || 'custom');
  if (projectPath.trim()) {
    formData.append('project_path', projectPath.trim());
  }

  try {
    const resp = await fetch('/api/review', { method: 'POST', body: formData });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || 'Review failed');
    showResult(data);
    status.textContent = 'Done.';
  } catch(err) {
    status.textContent = 'Error: ' + err.message;
  } finally {
    btn.disabled = false;
  }
}

async function runDemo() {
  const btn = document.querySelector('.btn-secondary');
  const status = document.getElementById('statusMsg');
  btn.disabled = true;
  status.innerHTML = '<span class="spinner"></span>Running demo...';
  try {
    const resp = await fetch('/api/demo', { method: 'POST' });
    const data = await resp.json();
    if (data.results && data.results.length > 0) {
      showResult(data.results[0]);
      status.textContent = 'Demo complete. Showing first result.';
    }
  } catch(err) {
    status.textContent = 'Error: ' + err.message;
  } finally {
    btn.disabled = false;
  }
}

async function runSmoke() {
  const status = document.getElementById('statusMsg');
  status.innerHTML = '<span class="spinner"></span>Running smoke test...';
  try {
    const resp = await fetch('/api/smoke');
    const data = await resp.json();
    const lines = Object.entries(data).map(([k,v]) => {
      const ok = v.status === 'ok' || v.available === true;
      return ok ? `✓ ${k}` : `✗ ${k}: ${v.error || 'failed'}`;
    });
    status.textContent = lines.join(' | ');
  } catch(err) {
    status.textContent = 'Smoke test error: ' + err.message;
  }
}

function scoreColor(s) {
  if (s >= 8) return 'score-good';
  if (s >= 6) return 'score-ok';
  return 'score-bad';
}

function barColor(s) {
  if (s >= 8) return 'var(--green)';
  if (s >= 6) return 'var(--yellow)';
  return 'var(--red)';
}

function showResult(data) {
  const box = document.getElementById('resultBox');
  box.classList.add('visible');

  const sc = data.scorecard;
  if (!sc) return;

  const gateClass = data.passed ? 'gate-pass' : 'gate-fail';
  const gateLabel = data.passed ? 'PASS' : 'FAIL';
  const gateBadge = data.passed ? 'badge-pass' : 'badge-fail';

  document.getElementById('gateResult').innerHTML = `
    <div class="card ${gateClass}" style="margin-bottom:1rem;">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div><span class="badge ${gateBadge}" style="font-size:1rem;padding:0.3rem 0.8rem;">${gateLabel}</span></div>
        <div style="font-size:2rem;font-weight:700;" class="${scoreColor(data.overall_score)}">${data.overall_score}/10</div>
      </div>
      <div class="meta-row" style="margin-top:0.75rem;">
        <span>Action: <strong>${data.recommended_next_action}</strong></span>
        <span>Issues: <strong>${data.issues_count}</strong></span>
        <span>Valid: <strong>${data.valid_issues}</strong></span>
      </div>
      ${sc.blocking_reasons && sc.blocking_reasons.length > 0 ? '<div style="margin-top:0.5rem;color:var(--red);font-size:0.85rem;">' + sc.blocking_reasons.map(r => '• ' + r).join('<br>') + '</div>' : ''}
    </div>`;

  if (sc.dimension_scores) {
    const ds = sc.dimension_scores;
    const dims = ['correctness','completeness','implementability','consistency','edge_case_coverage','testability','risk_awareness','clarity'];
    document.getElementById('scoreGrid').innerHTML = dims.map(d => {
      const val = ds[d] || 0;
      return `<div class="score-item">
        <div class="label">${d.replace(/_/g,' ')}</div>
        <div class="value ${scoreColor(val)}">${val}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${val*10}%;background:${barColor(val)};"></div></div>
      </div>`;
    }).join('');
  }

  document.getElementById('metaRow').innerHTML = `<span>Run: <strong>${data.run_id}</strong></span><span>Dir: <strong>${data.output_dir}</strong></span>`;

  box.scrollIntoView({ behavior: 'smooth' });
}
</script>
</body>
</html>"""


def _runs_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Runs - Doc Quality Gate</title>
<style>
  :root { --bg: #0f172a; --surface: #1e293b; --border: #334155; --text: #e2e8f0; --dim: #94a3b8; --accent: #3b82f6; --green: #22c55e; --red: #ef4444; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  nav { background: var(--surface); border-bottom: 1px solid var(--border); padding: 0.75rem 1.5rem; display: flex; gap: 1.5rem; align-items: center; }
  nav .brand { font-weight: 700; font-size: 1.1rem; color: var(--accent); }
  nav a { color: var(--dim); text-decoration: none; font-size: 0.9rem; }
  nav a:hover, nav a.active { color: var(--text); }
  .container { max-width: 960px; margin: 2rem auto; padding: 0 1.5rem; }
  h1 { font-size: 1.6rem; margin-bottom: 1.5rem; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 0.6rem 0.75rem; border-bottom: 1px solid var(--border); text-align: left; font-size: 0.9rem; }
  th { color: var(--dim); font-weight: 500; text-transform: uppercase; font-size: 0.75rem; letter-spacing: 0.05em; }
  tr:hover { background: var(--surface); }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 3px; font-size: 0.8rem; font-weight: 600; }
  .badge-pass { background: #14532d; color: #86efac; }
  .badge-fail { background: #7f1d1d; color: #fca5a5; }
</style>
</head>
<body>
<nav>
  <div class="brand">DQG</div>
  <a href="/">Review</a>
  <a href="/runs" class="active">Runs</a>
</nav>
<div class="container">
  <h1>Past Runs</h1>
  <table>
    <thead><tr><th>Run ID</th><th>Type</th><th>Score</th><th>Result</th><th>Action</th><th>Time</th></tr></thead>
    <tbody id="runsBody"></tbody>
  </table>
</div>
<script>
async function loadRuns() {
  const resp = await fetch('/api/runs');
  const data = await resp.json();
  const tbody = document.getElementById('runsBody');
  tbody.innerHTML = data.runs.map(r => `<tr>
    <td><a href="/run/${r.run_id}">${r.run_id}</a></td>
    <td>${r.document_type}</td>
    <td>${r.overall_score !== null ? r.overall_score + '/10' : '-'}</td>
    <td>${r.passed !== null ? '<span class="badge ' + (r.passed ? 'badge-pass' : 'badge-fail') + '">' + (r.passed ? 'PASS' : 'FAIL') + '</span>' : '-'}</td>
    <td>${r.recommended_next_action || '-'}</td>
    <td>${r.timestamp ? new Date(r.timestamp).toLocaleString() : '-'}</td>
  </tr>`).join('');
}
loadRuns();
</script>
</body>
</html>"""


def _run_detail_html(run_id: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Run {run_id} - Doc Quality Gate</title>
<style>
  :root {{ --bg: #0f172a; --surface: #1e293b; --border: #334155; --text: #e2e8f0; --dim: #94a3b8; --accent: #3b82f6; --green: #22c55e; --red: #ef4444; --yellow: #eab308; }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }}
  nav {{ background: var(--surface); border-bottom: 1px solid var(--border); padding: 0.75rem 1.5rem; display: flex; gap: 1.5rem; align-items: center; }}
  nav .brand {{ font-weight: 700; font-size: 1.1rem; color: var(--accent); }}
  nav a {{ color: var(--dim); text-decoration: none; font-size: 0.9rem; }}
  nav a:hover {{ color: var(--text); }}
  .container {{ max-width: 960px; margin: 2rem auto; padding: 0 1.5rem; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 1rem; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; margin-bottom: 1rem; }}
  .score-grid {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 0.5rem; }}
  .score-item {{ background: var(--bg); border-radius: 6px; padding: 0.6rem; text-align: center; }}
  .score-item .label {{ font-size: 0.7rem; color: var(--dim); text-transform: uppercase; letter-spacing: 0.04em; }}
  .score-item .value {{ font-size: 1.3rem; font-weight: 700; }}
  .score-good {{ color: var(--green); }} .score-ok {{ color: var(--yellow); }} .score-bad {{ color: var(--red); }}
  .badge {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 3px; font-size: 0.8rem; font-weight: 600; }}
  .badge-pass {{ background: #14532d; color: #86efac; }} .badge-fail {{ background: #7f1d1d; color: #fca5a5; }}
  .gate-pass {{ border-left: 4px solid var(--green); }} .gate-fail {{ border-left: 4px solid var(--red); }}
  pre {{ background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 1rem; overflow-x: auto; font-size: 0.82rem; white-space: pre-wrap; }}
  .bar-track {{ height: 4px; background: var(--border); border-radius: 2px; margin-top: 0.3rem; }}
  .bar-fill {{ height: 100%; border-radius: 2px; }}
  .tab-bar {{ display: flex; gap: 0; margin-bottom: 1rem; }}
  .tab {{ padding: 0.4rem 0.8rem; background: var(--bg); border: 1px solid var(--border); cursor: pointer; font-size: 0.85rem; color: var(--dim); }}
  .tab:first-child {{ border-radius: 6px 0 0 6px; }} .tab:last-child {{ border-radius: 0 6px 6px 0; }}
  .tab.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
  .tab-content {{ display: none; }} .tab-content.active {{ display: block; }}
</style>
</head>
<body>
<nav>
  <div class="brand">DQG</div>
  <a href="/">Review</a>
  <a href="/runs">Runs</a>
</nav>
<div class="container">
  <h1>Run: <span id="runId">{run_id}</span></h1>
  <div id="content">Loading...</div>
</div>
<script>
function scoreColor(s) {{ return s >= 8 ? 'score-good' : s >= 6 ? 'score-ok' : 'score-bad'; }}
function barColor(s) {{ return s >= 8 ? 'var(--green)' : s >= 6 ? 'var(--yellow)' : 'var(--red)'; }}

async function load() {{
  const resp = await fetch('/api/runs/{run_id}');
  const data = await resp.json();
  const sc = data.scorecard || {{}};
  const ds = sc.dimension_scores || {{}};
  const dims = ['correctness','completeness','implementability','consistency','edge_case_coverage','testability','risk_awareness','clarity'];
  const passed = sc.passed;
  const gateClass = passed ? 'gate-pass' : 'gate-fail';
  const gateBadge = passed ? 'badge-pass' : 'badge-fail';

  let html = `
    <div class="card ${{gateClass}}">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <span class="badge ${{gateBadge}}" style="font-size:1rem;padding:0.3rem 0.8rem;">${{passed ? 'PASS' : 'FAIL'}}</span>
        <span style="font-size:1.8rem;font-weight:700;" class="${{scoreColor(sc.overall_score||0)}}">${{sc.overall_score||0}}/10</span>
      </div>
      <div style="margin-top:0.5rem;color:var(--dim);font-size:0.85rem;">
        Action: ${{sc.recommended_next_action||'-'}} | Unresolved critical: ${{sc.unresolved_critical_issues_count||0}}
      </div>
      ${{sc.blocking_reasons && sc.blocking_reasons.length ? '<div style="margin-top:0.5rem;color:var(--red);font-size:0.85rem;">' + sc.blocking_reasons.map(r=>'• '+r).join('<br>') + '</div>' : ''}}
    </div>
    <div class="score-grid">
      ${{dims.map(d => {{
        const v = ds[d] || 0;
        return '<div class="score-item"><div class="label">'+d.replace(/_/g,' ')+'</div><div class="value ${{scoreColor(v)}}">'+v+'</div><div class="bar-track"><div class="bar-fill" style="width:'+v*10+'%;background:'+barColor(v)+';"></div></div></div>';
      }}).join('')}}
    </div>
    <div class="tab-bar" style="margin-top:1rem;">
      <div class="tab active" onclick="switchTab('original')">Original</div>
      <div class="tab" onclick="switchTab('revised')">Revised</div>
      <div class="tab" onclick="switchTab('issues')">Issues</div>
      <div class="tab" onclick="switchTab('fullreport')">Full Report</div>
    </div>
    <div id="tab-original" class="tab-content active"><pre>${{(data['original.md']||'').replace(/</g,'&lt;')}}</pre></div>
    <div id="tab-revised" class="tab-content"><pre>${{(data['revised.md']||'').replace(/</g,'&lt;')}}</pre></div>
    <div id="tab-issues" class="tab-content"><pre>${{JSON.stringify(data.issues||[], null, 2).replace(/</g,'&lt;')}}</pre></div>
    <div id="tab-fullreport" class="tab-content"><iframe src="/api/runs/{run_id}/report" style="width:100%;height:80vh;border:none;border-radius:6px;"></iframe></div>
  `;
  document.getElementById('content').innerHTML = html;
}}

function switchTab(name) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  event.target.classList.add('active');
}}

load();
</script>
</body>
</html>"""
