from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import uuid
from pathlib import Path

import structlog
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import load_app_config, load_model_routing
from app.orchestrator import Orchestrator, PipelineCancelledError
from app.utils.files import find_run_dir
from app.web.log_stream import LogBroadcaster

logger = structlog.get_logger("web")

_async_reviews: dict[str, dict] = {}
_active_runs: dict[str, threading.Event] = {}

app = FastAPI(title="Doc Quality Gate", version="0.1.0")

_UPLOAD_DIR = Path(tempfile.gettempdir()) / "dqg_uploads"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

_static = Path(__file__).parent / "static"
if _static.exists():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")


def _get_orchestrator() -> Orchestrator:
    config = load_app_config()
    return Orchestrator(config)


@app.get("/", response_class=HTMLResponse)
async def index():
    return _render_page("dashboard")


@app.get("/runs", response_class=HTMLResponse)
async def runs_page():
    return _render_page("runs")


@app.get("/smoke", response_class=HTMLResponse)
async def smoke_page():
    return _render_page("smoke")


@app.get("/run/{run_id}", response_class=HTMLResponse)
async def run_detail(run_id: str):
    return _render_page("run_detail", run_id=run_id)


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
                "duration_ms": meta.get("duration_ms"),
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


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    return _dashboard_html()


@app.get("/api/events")
async def sse_events():
    broadcaster = LogBroadcaster.get()
    client_id, queue = broadcaster.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            broadcaster.unsubscribe(client_id)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/status")
async def api_status():
    broadcaster = LogBroadcaster.get()
    state = broadcaster.setup_state

    config = load_app_config()
    proxy_url = config.proxy_base_url

    proxy_ok = False
    try:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{proxy_url}/health/liveliness", timeout=3)
            proxy_ok = r.status_code == 200
    except Exception:
        pass

    return {
        "proxy": {"url": proxy_url, "healthy": proxy_ok},
        "setup": state,
    }


@app.get("/settings", response_class=HTMLResponse)
async def settings_page():
    return _render_page("settings")


@app.get("/api/models")
async def api_get_models():
    config = load_app_config()
    routing = load_model_routing(config.config_dir)

    groups = {}
    for name, g in routing.model_groups.items():
        groups[name] = {
            "name": name,
            "provider": g.provider,
            "model": g.model,
            "description": g.description,
        }

    return {
        "groups": groups,
        "routing": config.model_aliases,
    }


@app.post("/api/models/routing")
async def api_update_routing(data: dict):
    new_routing = data.get("routing", {})
    if not new_routing:
        raise HTTPException(400, "routing field required")

    config = load_app_config()
    config_dir = Path(config.config_dir)
    app_yaml = config_dir / "app.yaml"

    if not app_yaml.exists():
        raise HTTPException(500, "app.yaml not found")

    with open(app_yaml) as f:
        raw = yaml.safe_load(f)

    raw["model_aliases"] = new_routing

    with open(app_yaml, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    logger.info("model_routing_updated", new_routing=new_routing)
    return {"status": "ok", "routing": new_routing}


@app.post("/api/models/group/{group_name}")
async def api_update_model_group(group_name: str, data: dict):
    model_value = data.get("model", "")
    if not model_value:
        raise HTTPException(400, "model field required")

    config = load_app_config()
    config_dir = Path(config.config_dir)
    routing_yaml = config_dir / "model_routing.yaml"
    litellm_yaml = config_dir / "litellm" / "config.yaml"

    if routing_yaml.exists():
        with open(routing_yaml) as f:
            routing_raw = yaml.safe_load(f)
        if group_name in routing_raw.get("model_groups", {}):
            routing_raw["model_groups"][group_name]["model"] = model_value
            with open(routing_yaml, "w") as f:
                yaml.dump(routing_raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    if litellm_yaml.exists():
        with open(litellm_yaml) as f:
            litellm_raw = yaml.safe_load(f)
        for entry in litellm_raw.get("model_list", []):
            if entry.get("model_name") == group_name:
                entry["litellm_params"]["model"] = model_value
        with open(litellm_yaml, "w") as f:
            yaml.dump(litellm_raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    logger.info("model_group_updated", group=group_name, model=model_value)
    return {"status": "ok", "group": group_name, "model": model_value}


@app.get("/api/copilot/status")
async def api_copilot_status():
    config = load_app_config()
    proxy_url = config.proxy_base_url

    copilot_info = {
        "provider": "github_copilot",
        "model_group": "strong_judge",
        "configured": False,
        "authenticated": False,
        "model": "",
        "proxy_healthy": False,
        "error": None,
        "subscription": None,
    }

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            health_resp = await client.get(f"{proxy_url}/health/liveliness")
            copilot_info["proxy_healthy"] = health_resp.status_code == 200
    except Exception as e:
        copilot_info["error"] = f"Proxy unreachable: {e}"
        return copilot_info

    routing = load_model_routing(config.config_dir)
    judge_group = routing.model_groups.get("strong_judge")
    if judge_group:
        copilot_info["configured"] = True
        copilot_info["model"] = judge_group.model

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            test_payload = {
                "model": "strong_judge",
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": 10,
                "temperature": 0.0,
            }
            headers = {"Content-Type": "application/json"}
            if config.proxy_api_key:
                headers["Authorization"] = f"Bearer {config.proxy_api_key}"

            test_resp = await client.post(
                f"{proxy_url}/chat/completions",
                json=test_payload,
                headers=headers,
            )
            if test_resp.status_code == 200:
                copilot_info["authenticated"] = True
                body = test_resp.json()
                usage = body.get("usage", {})
                copilot_info["subscription"] = {
                    "status": "active",
                    "model_responded": body.get("model", "unknown"),
                    "test_tokens": usage.get("total_tokens", 0),
                }
            else:
                err_detail = test_resp.text[:200]
                copilot_info["authenticated"] = False
                if "401" in str(test_resp.status_code):
                    copilot_info["error"] = (
                        "Authentication failed - run `litellm --config config/litellm/config.yaml` and complete OAuth flow"
                    )
                    copilot_info["subscription"] = {"status": "not_authenticated"}
                else:
                    copilot_info["error"] = f"HTTP {test_resp.status_code}: {err_detail}"
                    copilot_info["subscription"] = {"status": "error", "detail": err_detail}
    except Exception as e:
        copilot_info["authenticated"] = False
        copilot_info["error"] = f"Test request failed: {e}"
        copilot_info["subscription"] = {"status": "error", "detail": str(e)}

    return copilot_info


@app.post("/api/pipeline/cancel")
async def api_cancel_pipeline(payload: dict):
    run_id = payload.get("run_id")
    if not run_id:
        active_ids = list(_active_runs.keys())
        if not active_ids:
            raise HTTPException(400, "No active pipeline to cancel")
        run_id = active_ids[-1]

    event = _active_runs.get(run_id)
    if not event:
        raise HTTPException(404, f"No active pipeline found for run: {run_id}")

    event.set()
    logger.info("pipeline_cancel_requested", run_id=run_id)
    return {"status": "cancelling", "run_id": run_id}


@app.get("/api/smoke")
async def api_smoke_test():
    try:
        orch = _get_orchestrator()
        results = orch.smoke_test()
        return results
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/review")
async def api_review(payload: dict):
    doc_path = payload.get("file_path")
    if not doc_path:
        raise HTTPException(400, "file_path is required")

    doc_type = payload.get("doc_type")
    project_path = payload.get("project_path")
    context_path = payload.get("context_path")

    p = Path(doc_path)
    if not p.exists():
        raise HTTPException(400, f"File not found: {doc_path}")

    config = load_app_config()
    orch = Orchestrator(config)

    loop = asyncio.get_event_loop()

    def _run_sync():
        return orch.run(doc_path, doc_type, project_path=project_path, context_path=context_path)

    try:
        artifacts = await loop.run_in_executor(None, _run_sync)
        return _artifacts_to_response(artifacts)
    except Exception as e:
        logger.error("review_failed", error=str(e))
        raise HTTPException(500, str(e))


def _run_review_background(
    review_id: str,
    doc_path: str,
    doc_type: str | None,
    project_path: str | None,
    context_path: str | None = None,
    cancel_event: threading.Event | None = None,
):
    try:
        _async_reviews[review_id]["status"] = "running"
        config = load_app_config()
        orch = Orchestrator(config)
        artifacts = orch.run(
            doc_path, doc_type, project_path=project_path, context_path=context_path, cancel_event=cancel_event
        )
        _async_reviews[review_id]["status"] = "complete"
        _async_reviews[review_id]["result"] = _artifacts_to_response(artifacts)
    except PipelineCancelledError:
        _async_reviews[review_id]["status"] = "cancelled"
        _async_reviews[review_id]["error"] = "Pipeline cancelled by user"
    except Exception as e:
        logger.exception("async_review_failed", review_id=review_id, error=str(e), exc_info=True)
        _async_reviews[review_id]["status"] = "failed"
        _async_reviews[review_id]["error"] = str(e)
    finally:
        _active_runs.pop(review_id, None)


@app.post("/api/review/start")
async def api_review_start(payload: dict):
    doc_path = payload.get("file_path")
    if not doc_path:
        raise HTTPException(400, "file_path is required")

    p = Path(doc_path)
    if not p.exists():
        raise HTTPException(400, f"File not found: {doc_path}")

    doc_type = payload.get("doc_type")
    project_path = payload.get("project_path")
    context_path = payload.get("context_path")
    review_id = uuid.uuid4().hex[:12]

    _async_reviews[review_id] = {
        "review_id": review_id,
        "status": "queued",
        "doc_path": doc_path,
        "doc_type": doc_type,
        "project_path": project_path,
        "result": None,
        "error": None,
    }

    cancel_event = threading.Event()
    _active_runs[review_id] = cancel_event

    t = threading.Thread(
        target=_run_review_background,
        args=(review_id, doc_path, doc_type, project_path),
        kwargs={"context_path": context_path, "cancel_event": cancel_event},
        daemon=True,
    )
    t.start()

    return {"review_id": review_id, "status": "queued"}


@app.get("/api/review/status/{review_id}")
async def api_review_status(review_id: str):
    review = _async_reviews.get(review_id)
    if not review:
        raise HTTPException(404, f"Review not found: {review_id}")

    return {
        "review_id": review_id,
        "status": review["status"],
        "result": review["result"],
        "error": review["error"],
    }


@app.post("/api/demo")
async def api_demo():
    try:
        config = load_app_config()
        orch = Orchestrator(config)
        results = []
        examples = {
            "feature_spec": str(_PROJECT_ROOT / "examples" / "feature_spec" / "sample.md"),
            "implementation_plan": str(_PROJECT_ROOT / "examples" / "implementation_plan" / "sample.md"),
            "architecture_change": str(_PROJECT_ROOT / "examples" / "architecture_change" / "sample.md"),
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
    if page == "runs":
        return _runs_html()
    elif page == "run_detail":
        return _run_detail_html(kwargs.get("run_id", ""))
    elif page == "dashboard":
        return _dashboard_html()
    elif page == "settings":
        return _settings_html()
    elif page == "smoke":
        return _smoke_html()
    return "<html><body>Not found</body></html>"


def _runs_html() -> str:
    return """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Çalışmalar - Doc Quality Gate</title>
<style>
  :root { --bg: #0f172a; --surface: #1e293b; --border: #334155; --text: #e2e8f0; --dim: #94a3b8; --accent: #3b82f6; --green: #22c55e; --red: #ef4444; --purple: #a855f7; --orange: #f97316; }
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
  .badge-cancelled { background: #422006; color: #fbbf24; }
  .badge-running { background: #1e3a5f; color: #93c5fd; }
  .dur { color: var(--purple); font-weight: 600; }
  .status-cancelled { color: var(--orange); }
</style>
</head>
<body>
<nav>
  <div class="brand">DQG</div>
  <a href="/dashboard">Dashboard</a>
  <a href="/runs" class="active">Çalışmalar</a>
  <a href="/settings">Ayarlar</a>
  <a href="/smoke">Smoke Test</a>
</nav>
<div class="container">
  <h1>Geçmiş Çalışmalar</h1>
  <table>
    <thead><tr><th>Çalışma ID</th><th>Tür</th><th>Puan</th><th>Sonuç</th><th>Süre</th><th>Durum</th><th>Zaman</th></tr></thead>
    <tbody id="runsBody"></tbody>
  </table>
</div>
<script>
function fmtDur(ms){if(ms==null||ms===undefined)return'<span style="color:var(--dim)">-</span>';if(ms<1000)return ms+'ms';if(ms<60000)return(ms/1000).toFixed(1)+'s';var m=Math.floor(ms/60000);var s=Math.round((ms%60000)/1000);return m+'dk '+s+'sn';}
async function loadRuns() {
  const resp = await fetch('/api/runs');
  const data = await resp.json();
  const tbody = document.getElementById('runsBody');
  tbody.innerHTML = data.runs.map(r => {
    var statusBadge = '-';
    if(r.overall_score!=null&&r.overall_score!==undefined) statusBadge=r.overall_score>=8?'<span class="badge badge-pass">GEÇTİ</span>':'<span class="badge badge-fail">KALDI</span>';
    else if(r.status==='cancelled') statusBadge='<span class="badge badge-cancelled">İPTAL</span>';
    else if(r.status==='running') statusBadge='<span class="badge badge-running">ÇALIŞIYOR</span>';
    else if(r.status==='failed') statusBadge='<span class="badge badge-fail">HATA</span>';
    else if(r.status) statusBadge=r.status;
    return `<tr>
    <td><a href="/run/${r.run_id}">${r.run_id}</a></td>
    <td>${r.document_type}</td>
    <td>${r.overall_score !== null && r.overall_score !== undefined ? r.overall_score + '/10' : '-'}</td>
    <td>${statusBadge}</td>
    <td class="dur">${fmtDur(r.duration_ms)}</td>
    <td>${r.recommended_next_action || '-'}</td>
    <td>${r.timestamp ? new Date(r.timestamp).toLocaleString('tr-TR') : '-'}</td>
  </tr>`}).join('');
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
   <a href="/dashboard">Dashboard</a>
   <a href="/runs">Runs</a>
   <a href="/settings">Settings</a>
   <a href="/smoke">Smoke Test</a>
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
  const passed = sc.overall_score != null && sc.overall_score >= 8;
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


def _settings_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Settings - Doc Quality Gate</title>
<style>
  :root { --bg: #0f172a; --surface: #1e293b; --border: #334155; --text: #e2e8f0; --dim: #94a3b8; --accent: #3b82f6; --green: #22c55e; --red: #ef4444; --yellow: #eab308; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  nav { background: var(--surface); border-bottom: 1px solid var(--border); padding: 0.75rem 1.5rem; display: flex; gap: 1.5rem; align-items: center; }
  nav .brand { font-weight: 700; font-size: 1.1rem; color: var(--accent); }
  nav a { color: var(--dim); text-decoration: none; font-size: 0.9rem; }
  nav a:hover, nav a.active { color: var(--text); }
  .container { max-width: 960px; margin: 2rem auto; padding: 0 1.5rem; }
  h1 { font-size: 1.6rem; margin-bottom: 1.5rem; }
  h2 { font-size: 1.2rem; margin-bottom: 1rem; color: var(--dim); }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem; }
  label { display: block; font-size: 0.85rem; color: var(--dim); margin-bottom: 0.4rem; font-weight: 500; }
  select, input[type="text"] { width: 100%; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 0.6rem; color: var(--text); font-size: 0.9rem; margin-bottom: 1rem; }
  select:focus, input:focus { outline: none; border-color: var(--accent); }
  button { background: var(--accent); color: #fff; border: none; border-radius: 6px; padding: 0.7rem 1.5rem; font-size: 0.95rem; font-weight: 600; cursor: pointer; transition: opacity 0.15s; }
  button:hover { opacity: 0.9; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-sm { padding: 0.4rem 0.8rem; font-size: 0.8rem; }
  .btn-secondary { background: var(--border); }
  .btn-green { background: var(--green); }
  .btn-red { background: var(--red); }
  table { width: 100%; border-collapse: collapse; margin-bottom: 1rem; }
  th, td { padding: 0.6rem 0.75rem; border-bottom: 1px solid var(--border); text-align: left; font-size: 0.9rem; }
  th { color: var(--dim); font-weight: 500; text-transform: uppercase; font-size: 0.75rem; letter-spacing: 0.05em; }
  .badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 3px; font-size: 0.8rem; font-weight: 600; }
  .badge-ok { background: #14532d; color: #86efac; }
  .badge-err { background: #7f1d1d; color: #fca5a5; }
  .badge-warn { background: #713f12; color: #fde047; }
  .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 0.4rem; }
  .dot-green { background: var(--green); }
  .dot-red { background: var(--red); }
  .dot-yellow { background: var(--yellow); }
  .status-section { margin-top: 1rem; }
  .flex-between { display: flex; justify-content: space-between; align-items: center; }
  .msg { padding: 0.5rem 0; font-size: 0.85rem; }
  .msg-ok { color: var(--green); }
  .msg-err { color: var(--red); }
  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.6s linear infinite; margin-right: 0.5rem; vertical-align: middle; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<nav>
  <div class="brand">DQG</div>
  <a href="/dashboard">Dashboard</a>
  <a href="/runs">Runs</a>
  <a href="/settings" class="active">Settings</a>
  <a href="/smoke">Smoke Test</a>
</nav>

<div class="container">
  <h1>Settings</h1>

  <!-- COPilot STATUS -->
  <h2>GitHub Copilot Subscription</h2>
  <div class="card" id="copilotCard">
    <div class="flex-between">
      <span>Checking status...</span>
      <span class="spinner"></span>
    </div>
  </div>

  <!-- MODEL GROUPS -->
  <h2>Model Groups</h2>
  <div class="card">
    <p style="color:var(--dim);font-size:0.85rem;margin-bottom:1rem;">Configure the underlying model for each group. Changes are written to config files and take effect on next pipeline run. Restart the LiteLLM proxy to apply changes.</p>
    <table>
      <thead><tr><th>Group</th><th>Provider</th><th>Current Model</th><th>New Model</th><th></th></tr></thead>
      <tbody id="groupsBody"></tbody>
    </table>
    <div id="groupMsg" class="msg"></div>
  </div>

  <!-- STAGE ROUTING -->
  <h2>Stage Routing</h2>
  <div class="card">
    <p style="color:var(--dim);font-size:0.85rem;margin-bottom:1rem;">Map pipeline stages to model groups. Changes take effect on next pipeline run.</p>
    <table>
      <thead><tr><th>Stage</th><th>Model Group</th></tr></thead>
      <tbody id="routingBody"></tbody>
    </table>
    <div style="margin-top:1rem;">
      <button onclick="saveRouting()">Save Routing</button>
    </div>
    <div id="routingMsg" class="msg"></div>
  </div>
</div>

<script>
const GROUPS_ORDER = ['cheap_large_context', 'cheap_large_context_alt', 'strong_judge', 'fallback_general'];
const STAGES_ORDER = ['critic_a', 'critic_b', 'critic_judge', 'validator', 'reviser', 'scorer', 'fallback'];
let currentRouting = {};
let currentGroups = {};

async function loadModels() {
  const resp = await fetch('/api/models');
  const data = await resp.json();
  currentGroups = data.groups || {};
  currentRouting = data.routing || {};
  renderGroups();
  renderRouting();
}

function renderGroups() {
  const tbody = document.getElementById('groupsBody');
  tbody.innerHTML = GROUPS_ORDER.filter(g => currentGroups[g]).map(g => {
    const grp = currentGroups[g];
    return `<tr>
      <td><strong>${g}</strong><br><span style="font-size:0.75rem;color:var(--dim)">${grp.description}</span></td>
      <td>${grp.provider}</td>
      <td><code style="color:var(--accent)">${grp.model}</code></td>
      <td><input type="text" id="model-${g}" value="${grp.model}" style="width:100%;padding:0.4rem;font-size:0.85rem;"></td>
      <td><button class="btn-sm" onclick="updateGroup('${g}')">Update</button></td>
    </tr>`;
  }).join('');
}

function renderRouting() {
  const tbody = document.getElementById('routingBody');
  tbody.innerHTML = STAGES_ORDER.map(stage => {
    const current = currentRouting[stage] || '';
    return `<tr>
      <td><code>${stage}</code></td>
      <td>
        <select id="route-${stage}" style="width:100%;padding:0.4rem;font-size:0.85rem;">
          ${GROUPS_ORDER.map(g => `<option value="${g}" ${g === current ? 'selected' : ''}>${g}</option>`).join('')}
        </select>
      </td>
    </tr>`;
  }).join('');
}

async function updateGroup(groupName) {
  const input = document.getElementById('model-' + groupName);
  const newModel = input.value.trim();
  const msg = document.getElementById('groupMsg');
  if (!newModel) { msg.className = 'msg msg-err'; msg.textContent = 'Model cannot be empty'; return; }

  msg.className = 'msg'; msg.innerHTML = '<span class="spinner"></span>Saving...';
  try {
    const resp = await fetch('/api/models/group/' + groupName, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({model: newModel})
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || 'Failed');
    msg.className = 'msg msg-ok'; msg.textContent = 'Updated ' + groupName + ' to ' + newModel;
    setTimeout(() => loadModels(), 500);
  } catch(e) {
    msg.className = 'msg msg-err'; msg.textContent = 'Error: ' + e.message;
  }
}

async function saveRouting() {
  const msg = document.getElementById('routingMsg');
  const newRouting = {};
  STAGES_ORDER.forEach(stage => {
    const sel = document.getElementById('route-' + stage);
    if (sel) newRouting[stage] = sel.value;
  });

  msg.className = 'msg'; msg.innerHTML = '<span class="spinner"></span>Saving...';
  try {
    const resp = await fetch('/api/models/routing', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({routing: newRouting})
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || 'Failed');
    currentRouting = data.routing;
    msg.className = 'msg msg-ok'; msg.textContent = 'Routing saved successfully.';
  } catch(e) {
    msg.className = 'msg msg-err'; msg.textContent = 'Error: ' + e.message;
  }
}

async function loadCopilotStatus() {
  const card = document.getElementById('copilotCard');
  card.innerHTML = '<div class="flex-between"><span>Checking Copilot status...</span><span class="spinner"></span></div>';

  try {
    const resp = await fetch('/api/copilot/status');
    const data = await resp.json();

    let proxyDot = data.proxy_healthy ? '<span class="status-dot dot-green"></span>Proxy: Healthy' : '<span class="status-dot dot-red"></span>Proxy: Unhealthy';
    let configDot = data.configured ? '<span class="status-dot dot-green"></span>Configured' : '<span class="status-dot dot-yellow"></span>Not Configured';
    let authDot = data.authenticated ? '<span class="status-dot dot-green"></span>Authenticated' : '<span class="status-dot dot-red"></span>Not Authenticated';

    let subBadge = '';
    if (data.subscription) {
      const st = data.subscription.status;
      if (st === 'active') subBadge = '<span class="badge badge-ok">Subscription Active</span>';
      else if (st === 'not_authenticated') subBadge = '<span class="badge badge-err">Not Authenticated</span>';
      else subBadge = '<span class="badge badge-warn">' + st + '</span>';
    }

    let details = '';
    if (data.model) details += '<div style="margin-top:0.5rem;font-size:0.85rem;">Model: <code style="color:var(--accent)">' + data.model + '</code></div>';
    if (data.subscription && data.subscription.model_responded) {
      details += '<div style="font-size:0.85rem;">Responded as: <code>' + data.subscription.model_responded + '</code></div>';
    }
    if (data.error) {
      details += '<div style="margin-top:0.5rem;font-size:0.85rem;color:var(--red);">' + data.error + '</div>';
    }

    card.innerHTML = `
      <div style="display:flex;gap:1.5rem;flex-wrap:wrap;align-items:center;">
        ${proxyDot} &nbsp; ${configDot} &nbsp; ${authDot} &nbsp; ${subBadge}
      </div>
      ${details}
      <div style="margin-top:1rem;">
        <button class="btn-sm btn-secondary" onclick="loadCopilotStatus()">Refresh</button>
      </div>
    `;
  } catch(e) {
    card.innerHTML = '<div class="msg msg-err">Failed to check status: ' + e.message + '</div>';
  }
}

loadModels();
loadCopilotStatus();
</script>
</body>
</html>"""


def _dashboard_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dashboard - Doc Quality Gate</title>
<style>
  :root { --bg: #0f172a; --surface: #1e293b; --border: #334155; --text: #e2e8f0; --dim: #94a3b8; --accent: #3b82f6; --green: #22c55e; --red: #ef4444; --yellow: #eab308; --orange: #f97316; --purple: #a855f7; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  nav { background: var(--surface); border-bottom: 1px solid var(--border); padding: 0.75rem 1.5rem; display: flex; gap: 1.5rem; align-items: center; }
  nav .brand { font-weight: 700; font-size: 1.1rem; color: var(--accent); }
  nav a { color: var(--dim); text-decoration: none; font-size: 0.9rem; transition: color 0.15s; }
  nav a:hover, nav a.active { color: var(--text); }
  .container { max-width: 1100px; margin: 1.5rem auto; padding: 0 1.5rem; }
  h1 { font-size: 1.5rem; margin-bottom: 1.25rem; }
  .status-bar { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
  .status-card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem 1.25rem; }
  .status-card .label { font-size: 0.75rem; color: var(--dim); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 0.4rem; }
  .status-card .value { font-size: 1.1rem; font-weight: 600; display: flex; align-items: center; gap: 0.5rem; }
  .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  .dot-green { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .dot-red { background: var(--red); box-shadow: 0 0 6px var(--red); }
  .dot-yellow { background: var(--yellow); box-shadow: 0 0 6px var(--yellow); animation: pulse 1.5s infinite; }
  @keyframes pulse { 0%,100%{opacity:1}50%{opacity:.4} }
  .progress-section { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; margin-bottom: 1.5rem; }
  .progress-section h2 { font-size: 1rem; margin-bottom: 0.75rem; color: var(--dim); text-transform: uppercase; letter-spacing: 0.05em; }
  .stage-list { display: flex; flex-direction: column; gap: 0.5rem; }
  .stage-row { display: flex; align-items: center; gap: 0.75rem; padding: 0.5rem 0.75rem; border-radius: 6px; background: var(--bg); font-size: 0.88rem; }
  .stage-row.active { border-left: 3px solid var(--accent); }
  .stage-row.done { border-left: 3px solid var(--green); }
  .stage-row.error { border-left: 3px solid var(--red); }
  .stage-icon { width: 18px; text-align: center; }
  .stage-name { flex: 1; }
  .stage-status { font-size: 0.78rem; color: var(--dim); }
  .stage-detail { font-size: 0.78rem; color: var(--dim); font-family: monospace; }
  .stage-duration { font-size: 0.78rem; color: var(--purple); font-weight: 600; min-width: 60px; text-align: right; }
  .log-section { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; }
  .log-section h2 { font-size: 1rem; margin-bottom: 0.75rem; color: var(--dim); text-transform: uppercase; letter-spacing: 0.05em; display: flex; justify-content: space-between; align-items: center; }
  .log-controls { display: flex; gap: 0.5rem; }
  .log-controls button { background: var(--border); color: var(--text); border: none; border-radius: 4px; padding: 0.25rem 0.6rem; font-size: 0.75rem; cursor: pointer; }
  .log-controls button:hover { background: var(--accent); }
  .log-controls select { background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 4px; padding: 0.2rem 0.4rem; font-size: 0.75rem; }
  #logBox { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 0.75rem; max-height: 600px; overflow-y: auto; font-family: 'Consolas','SF Mono',monospace; font-size: 0.8rem; line-height: 1.6; }
  .ll { display: flex; gap: 0.5rem; padding: 0.1rem 0; }
  .lt { color: var(--dim); min-width: 70px; }
  .lv { min-width: 50px; font-weight: 600; }
  .lv.info { color: var(--accent); } .lv.warning { color: var(--yellow); } .lv.error { color: var(--red); } .lv.debug { color: var(--dim); }
  .lm { word-break: break-all; } .ls { color: var(--orange); font-size: 0.75rem; }
  .run-badge { font-size: 0.65rem; font-weight: 700; padding: 0.1rem 0.4rem; border-radius: 3px; background: var(--purple); color: #fff; white-space: nowrap; letter-spacing: 0.03em; }

  .llm-card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; margin: 0.5rem 0; overflow: hidden; }
  .llm-header { display: flex; align-items: center; gap: 0.75rem; padding: 0.6rem 0.75rem; cursor: pointer; user-select: none; }
  .llm-header:hover { background: rgba(59,130,246,0.07); }
  .llm-badge { font-size: 0.7rem; font-weight: 700; padding: 0.15rem 0.5rem; border-radius: 3px; text-transform: uppercase; letter-spacing: 0.04em; background: var(--purple); color: #fff; }
  .llm-model { font-size: 0.82rem; color: var(--text); font-weight: 600; }
  .llm-model-real { font-size: 0.72rem; color: var(--dim); }
  .llm-meta { display: flex; gap: 1rem; margin-left: auto; font-size: 0.75rem; color: var(--dim); align-items: center; }
  .llm-meta .dur { color: var(--purple); font-weight: 600; }
  .llm-meta .tok { color: var(--accent); }
  .llm-chevron { color: var(--dim); font-size: 0.7rem; transition: transform 0.15s; }
  .llm-chevron.open { transform: rotate(90deg); }
  .llm-body { display: none; border-top: 1px solid var(--border); }
  .llm-body.open { display: block; }
  .llm-section { padding: 0.5rem 0.75rem; }
  .llm-section + .llm-section { border-top: 1px dashed var(--border); }
  .llm-section-label { font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--dim); margin-bottom: 0.3rem; cursor: pointer; user-select: none; display: flex; align-items: center; gap: 0.3rem; }
  .llm-section-label:hover { color: var(--text); }
  .llm-section-content { display: none; }
  .llm-section-content.open { display: block; }
  .llm-msg { padding: 0.2rem 0; font-size: 0.78rem; }
  .llm-msg-role { font-weight: 600; color: var(--accent); margin-right: 0.3rem; }
  .llm-msg-text { color: var(--dim); white-space: pre-wrap; word-break: break-all; max-height: 300px; overflow-y: auto; background: var(--bg); padding: 0.4rem 0.5rem; border-radius: 4px; font-size: 0.75rem; }
  .llm-response-text { color: var(--text); white-space: pre-wrap; word-break: break-all; max-height: 400px; overflow-y: auto; background: var(--bg); padding: 0.5rem; border-radius: 4px; font-size: 0.75rem; border-left: 3px solid var(--purple); }
  .llm-tok-bar { display: flex; gap: 0.5rem; font-size: 0.72rem; color: var(--dim); padding: 0.3rem 0.75rem; border-top: 1px dashed var(--border); }
  .llm-tok-bar span { color: var(--accent); font-weight: 600; }

  .badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 3px; font-size: 0.8rem; font-weight: 600; }
  .badge-pass { background: #14532d; color: #86efac; } .badge-fail { background: #7f1d1d; color: #fca5a5; } .badge-running { background: #1e3a5f; color: #93c5fd; }
</style>
</head>
<body>
<nav>
  <div class="brand">DQG</div>
  <a href="/dashboard" class="active">Dashboard</a>
  <a href="/runs">Runs</a>
  <a href="/settings">Settings</a>
  <a href="/smoke">Smoke Test</a>
</nav>
<div class="container">
  <h1>Dashboard</h1>
  <div class="status-bar">
    <div class="status-card"><div class="label">LiteLLM Proxy</div><div class="value" id="proxySt"><span class="dot dot-yellow"></span> Checking...</div></div>
    <div class="status-card"><div class="label">Web Server</div><div class="value" id="webSt"><span class="dot dot-green"></span> Running</div></div>
    <div class="status-card"><div class="label">Active Pipeline</div><div class="value" id="pipeSt">Boşta</div></div>
    <div class="status-card"><div class="label">Son Puan</div><div class="value" id="lastSc">-</div></div>
    <div class="status-card"><div class="label">Süre</div><div class="value" id="durVal">-</div></div>
  </div>
  <div id="cancelBox" style="display:none;margin-bottom:1.5rem;">
    <button id="cancelBtn" onclick="cancelPipeline()" style="background:var(--red);color:#fff;border:none;border-radius:6px;padding:0.6rem 1.2rem;font-size:0.9rem;font-weight:600;cursor:pointer;transition:opacity 0.15s;">
      Pipeline'ı Durdur
    </button>
    <span id="cancelMsg" style="margin-left:0.75rem;font-size:0.85rem;color:var(--dim);"></span>
  </div>

  <div class="progress-section"><h2>Pipeline Stages</h2><div class="stage-list" id="stageList"><div class="stage-row"><div class="stage-name" style="color:var(--dim)">No pipeline running. Submit a review to see stages.</div></div></div></div>
  <div class="log-section">
    <h2>Live Logs <div class="log-controls"><select id="logFilter" onchange="filterLogs()"><option value="all">All</option><option value="active">Active Pipeline</option><option value="llm">LLM Calls</option><option value="info">Info+</option><option value="warning">Warn+</option><option value="error">Error</option></select><button onclick="document.getElementById('logBox').innerHTML='';allLogs=[];">Clear</button></div></h2>
    <div id="logBox"></div>
  </div>
</div>
<script>
var STAGES=['ingest','domain_context','cross_reference','deep_analysis','critic_a_multi','critic_a_judge','critic_b_multi','critic_b_judge','dedup','validate','revise','score','meta_judge','report'];
var SLABELS={ingest:'Document Ingestion',domain_context:'Domain Context',cross_reference:'Cross-Reference',deep_analysis:'Deep Analysis',critic_a_multi:'Critic A',critic_a_judge:'Critic A Judge',critic_b_multi:'Critic B',critic_b_judge:'Critic B Judge',dedup:'Deduplication',validate:'Validation',revise:'Revision',score:'Scoring',meta_judge:'Meta-Judge',report:'Report'};
var stgs={},curRun=null,allLogs=[],logIdCounter=0;
function ft(ts){var d=new Date(ts*1000);return d.toLocaleTimeString('en-US',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});}
function lc(l){return l==='error'||l==='critical'?'error':l==='warning'||l==='warn'?'warning':l==='debug'?'debug':'info';}
function lr(l){return l==='error'||l==='critical'?3:l==='warning'||l==='warn'?2:l==='info'?1:0;}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function fmtDuration(ms){if(ms==null)return'';if(ms<1000)return ms+'ms';return(ms/1000).toFixed(1)+'s';}

function renderLLMCard(m){
  var id='llm_'+(++logIdCounter);
  var d=document.createElement('div');
  d.className='llm-card';
  d.dataset.logtype='llm';

  var reqHTML='';
  if(m.request_summary&&m.request_summary.length){
    reqHTML=m.request_summary.map(function(msg){
      return '<div class="llm-msg"><span class="llm-msg-role">'+esc(msg.role)+'：</span></div><div class="llm-msg-text">'+esc(msg.preview)+'</div>';
    }).join('');
  }

  var respHTML='';
  if(m.response_preview){
    respHTML='<div class="llm-response-text">'+esc(m.response_preview)+'</div>';
    if(m.response_length>m.response_preview.length-3){
      respHTML+='<div style="font-size:0.7rem;color:var(--dim);margin-top:0.2rem;">Full response: '+m.response_length+' chars</div>';
    }
  }

  var stageLabel=esc(m.stage||'');
  var modelGroup=esc(m.model_group||'');
  var modelUsed=esc(m.model_used||'');
  var dur=fmtDuration(m.duration_ms);
  var totalTok=m.tokens_total||0;

  d.innerHTML=
    '<div class="llm-header" onclick="toggleLLMBody(\\''+id+'\\')">'+
      '<span class="llm-chevron" id="chv_'+id+'">&#9654;</span>'+
      '<span class="llm-badge">LLM</span>'+
      '<span class="llm-model">'+stageLabel+'</span>'+
      '<span class="llm-model-real">'+modelGroup+' → '+modelUsed+'</span>'+
      '<div class="llm-meta">'+
        '<span class="dur">'+dur+'</span>'+
        '<span class="tok">'+totalTok+' tok</span>'+
      '</div>'+
    '</div>'+
    '<div class="llm-body" id="body_'+id+'">'+
      '<div class="llm-section">'+
        '<div class="llm-section-label" onclick="toggleLLMSection(\\''+id+'_req\\',event)"><span id="chv_'+id+'_req">&#9654;</span> Request ('+(m.request_summary?m.request_summary.length:0)+' messages)</div>'+
        '<div class="llm-section-content" id="sec_'+id+'_req">'+reqHTML+'</div>'+
      '</div>'+
      '<div class="llm-section">'+
        '<div class="llm-section-label" onclick="toggleLLMSection(\\''+id+'_resp\\',event)"><span id="chv_'+id+'_resp">&#9654;</span> Response</div>'+
        '<div class="llm-section-content" id="sec_'+id+'_resp">'+respHTML+'</div>'+
      '</div>'+
      '<div class="llm-tok-bar">'+
        'Prompt: <span>'+(m.tokens_prompt||0)+'</span> &nbsp; Completion: <span>'+(m.tokens_completion||0)+'</span> &nbsp; Total: <span>'+totalTok+'</span>'+
      '</div>'+
    '</div>';

  return d;
}

function toggleLLMBody(id){
  var body=document.getElementById('body_'+id);
  var chv=document.getElementById('chv_'+id);
  if(body.classList.contains('open')){body.classList.remove('open');chv.classList.remove('open');}
  else{body.classList.add('open');chv.classList.add('open');}
}

function toggleLLMSection(id,event){
  event.stopPropagation();
  var sec=document.getElementById('sec_'+id);
  var chv=document.getElementById('chv_'+id);
  if(sec.classList.contains('open')){sec.classList.remove('open');chv.classList.remove('open');}
  else{sec.classList.add('open');chv.classList.add('open');}
}

function rl(m){
  var f=document.getElementById('logFilter').value;
  var mRun=m.run_id||null;
  if(f==='active'){
    if(mRun&&curRun&&mRun!==curRun)return;
    if(!mRun&&curRun)return;
  }
  if(m.type==='llm_call'){
    if(f!=='all'&&f!=='active'&&f!=='llm')return;
    var c=document.getElementById('logBox');
    var el=renderLLMCard(m);
    if(mRun)el.dataset.runid=mRun;
    c.appendChild(el);
    if(c.children.length>300)c.removeChild(c.firstChild);
    c.scrollTop=c.scrollHeight;
    return;
  }
  if(f==='llm')return;
  if(f!=='all'&&f!=='active'&&lr(m.level)<lr(f))return;
  var c=document.getElementById('logBox'),d=document.createElement('div');
  d.className='ll';d.dataset.logtype='log';
  if(mRun)d.dataset.runid=mRun;
  var runBadge=(mRun&&mRun===curRun)?'<span class="run-badge">'+mRun.substring(0,8)+'</span>':'';
  d.innerHTML='<span class="lt">'+ft(m.timestamp)+'</span><span class="lv '+lc(m.level)+'">'+m.level.toUpperCase()+'</span>'+runBadge+'<span class="lm">'+esc(m.message)+'</span>'+(m.source&&m.source!=='system'?'<span class="ls">['+m.source+']</span>':'');
  c.appendChild(d);if(c.children.length>500)c.removeChild(c.firstChild);c.scrollTop=c.scrollHeight;
}

function filterLogs(){
  document.getElementById('logBox').innerHTML='';
  allLogs.forEach(function(m){rl(m);});
}

function us(){
  var l=document.getElementById('stageList');
  if(!curRun){l.innerHTML='<div class="stage-row"><div class="stage-name" style="color:var(--dim)">Çalışan pipeline yok.</div></div>';return;}
  var h='';
  STAGES.forEach(function(s){
    var i=stgs[s];
    if(!i)return;
    var c=i.status==='done'?'done':i.status==='error'?'error':i.status==='running'?'active':i.status==='cancelled'?'error':'';
    var ic=i.status==='done'?'\u2713':i.status==='error'?'\u2717':i.status==='running'?'\u25b6':i.status==='cancelled'?'\u25a0':'\u25cb';
    var dur=i.duration_ms!=null?'<div class="stage-duration">'+fmtDuration(i.duration_ms)+'</div>':'';
    h+='<div class="stage-row '+c+'"><div class="stage-icon">'+ic+'</div><div class="stage-name">'+(SLABELS[s]||s)+'</div>'+dur+'<div class="stage-status">'+i.status+'</div><div class="stage-detail">'+(i.detail||'')+'</div></div>';
  });
  l.innerHTML=h;
}

function showCancelBtn(runId){
  var box=document.getElementById('cancelBox');
  var btn=document.getElementById('cancelBtn');
  box.style.display='block';
  btn.disabled=false;
  btn.dataset.runId=runId;
  document.getElementById('cancelMsg').textContent='';
}
function hideCancelBtn(){
  document.getElementById('cancelBox').style.display='none';
}
async function cancelPipeline(){
  var btn=document.getElementById('cancelBtn');
  var runId=btn.dataset.runId;
  if(!runId)return;
  btn.disabled=true;
  document.getElementById('cancelMsg').innerHTML='<span class="spinner" style="width:12px;height:12px;border-width:2px;"></span>İptal ediliyor...';
  try{
    var resp=await fetch('/api/pipeline/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({run_id:runId})});
    var data=await resp.json();
    if(!resp.ok)throw new Error(data.detail||'İptal başarısız');
    document.getElementById('cancelMsg').textContent='Pipeline iptal ediliyor...';
    setTimeout(hideCancelBtn,3000);
  }catch(e){
    document.getElementById('cancelMsg').innerHTML='<span style="color:var(--red)">Hata: '+e.message+'</span>';
    btn.disabled=false;
  }
}

var pipeStartMs=null;
var pipeTimerId=null;
function startPipeTimer(tsMs){
  pipeStartMs=tsMs||Date.now();
  if(pipeTimerId)clearInterval(pipeTimerId);
  pipeTimerId=setInterval(function(){
    var elapsed=Date.now()-pipeStartMs;
    document.getElementById('durVal').innerHTML='<span style="color:var(--purple);font-weight:700">'+fmtDuration(elapsed)+'</span>';
  },500);
}
function stopPipeTimer(finalMs){
  if(pipeTimerId){clearInterval(pipeTimerId);pipeTimerId=null;}
  pipeStartMs=null;
  if(finalMs!=null){
    var durStr=finalMs<60000?fmtDuration(finalMs):Math.floor(finalMs/60000)+'dk '+Math.round((finalMs%60000)/1000)+'sn';
    document.getElementById('durVal').innerHTML='<span style="color:var(--purple);font-weight:700">'+durStr+'</span>';
  }
}

var es=new EventSource('/api/events');
es.onmessage=function(e){
  try{
    var m=JSON.parse(e.data);
    if(m.type==='log'){
      allLogs.push(m);rl(m);
    }else if(m.type==='llm_call'){
      allLogs.push(m);rl(m);
    }else if(m.type==='pipeline_stage'){
      if(m.run_id&&m.run_id!==curRun){curRun=m.run_id;stgs={};document.getElementById('logBox').innerHTML='';allLogs=[];document.getElementById('logFilter').value='active';document.getElementById('pipeSt').innerHTML='<span class="badge badge-running">'+m.run_id+'</span>';showCancelBtn(m.run_id);startPipeTimer(m.timestamp?m.timestamp*1000:null);}
      stgs[m.stage]={status:m.status,detail:m.detail||'',duration_ms:m.duration_ms};
      us();
    }else if(m.type==='pipeline_done'){
      hideCancelBtn();
      stopPipeTimer(m.duration_ms);
      if(m.score!=null){var c=m.score>=8?'var(--green)':'var(--red)';document.getElementById('lastSc').innerHTML='<span style="color:'+c+';font-weight:700">'+m.score+'/10</span>';}
      if(m.run_id===curRun){var st=m.score==null?'<span class="badge badge-fail">HATA</span>':m.score>=8?'<span class="badge badge-pass">GEÇTİ</span>':'<span class="badge badge-fail">KALDI</span>';document.getElementById('pipeSt').innerHTML=st;}

    }else if(m.type==='setup_step'){
      allLogs.push({level:'info',message:'[Setup '+m.step_number+'/'+m.total_steps+'] '+m.step,timestamp:m.timestamp,source:'setup'});rl(allLogs[allLogs.length-1]);
    }else if(m.type==='setup_done'){
      var l=m.success?'info':'error';
      allLogs.push({level:l,message:m.success?'Setup completed':'Setup failed: '+(m.errors||[]).join(', '),timestamp:m.timestamp,source:'setup'});rl(allLogs[allLogs.length-1]);
    }
  }catch(x){}
};
es.onerror=function(){document.getElementById('webSt').innerHTML='<span class="dot dot-red"></span> Reconnecting...';};
function ck(){fetch('/api/status').then(function(r){return r.json();}).then(function(d){document.getElementById('proxySt').innerHTML=d.proxy&&d.proxy.healthy?'<span class="dot dot-green"></span> Healthy':'<span class="dot dot-red"></span> Down';}).catch(function(){document.getElementById('proxySt').innerHTML='<span class="dot dot-red"></span> Error';});}
ck();setInterval(ck,15000);
</script>
</body>
</html>"""


def _smoke_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Smoke Test - Doc Quality Gate</title>
<style>
  :root { --bg: #0f172a; --surface: #1e293b; --border: #334155; --text: #e2e8f0; --dim: #94a3b8; --accent: #3b82f6; --green: #22c55e; --red: #ef4444; --yellow: #eab308; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  nav { background: var(--surface); border-bottom: 1px solid var(--border); padding: 0.75rem 1.5rem; display: flex; gap: 1.5rem; align-items: center; }
  nav .brand { font-weight: 700; font-size: 1.1rem; color: var(--accent); }
  nav a { color: var(--dim); text-decoration: none; font-size: 0.9rem; }
  nav a:hover, nav a.active { color: var(--text); }
  .container { max-width: 960px; margin: 2rem auto; padding: 0 1.5rem; }
  h1 { font-size: 1.6rem; margin-bottom: 1.5rem; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem; }
  button { background: var(--accent); color: #fff; border: none; border-radius: 6px; padding: 0.7rem 1.5rem; font-size: 0.95rem; font-weight: 600; cursor: pointer; transition: opacity 0.15s; }
  button:hover { opacity: 0.9; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.6s linear infinite; margin-right: 0.5rem; vertical-align: middle; }
  @keyframes spin { to { transform: rotate(360deg); } }
  table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
  th, td { padding: 0.6rem 0.75rem; border-bottom: 1px solid var(--border); text-align: left; font-size: 0.9rem; }
  th { color: var(--dim); font-weight: 500; text-transform: uppercase; font-size: 0.75rem; letter-spacing: 0.05em; }
  .ok { color: var(--green); }
  .fail { color: var(--red); }
  .pending { color: var(--dim); }
</style>
</head>
<body>
<nav>
  <div class="brand">DQG</div>
  <a href="/dashboard">Dashboard</a>
  <a href="/runs">Runs</a>
  <a href="/settings">Settings</a>
  <a href="/smoke" class="active">Smoke Test</a>
</nav>
<div class="container">
  <h1>Smoke Test</h1>
  <div class="card">
    <p style="color:var(--dim);margin-bottom:1rem;">Verify LiteLLM proxy connectivity, model availability, and Promptfoo integration.</p>
    <button id="runBtn" onclick="runSmoke()">Run Smoke Test</button>
    <div id="statusMsg" style="margin-top:1rem;font-size:0.9rem;"></div>
  </div>
  <div id="resultsCard" class="card" style="display:none;">
    <table>
      <thead><tr><th>Check</th><th>Status</th><th>Details</th></tr></thead>
      <tbody id="resultsBody"></tbody>
    </table>
  </div>
</div>
<script>
async function runSmoke() {
  var btn = document.getElementById('runBtn');
  var status = document.getElementById('statusMsg');
  var card = document.getElementById('resultsCard');
  var tbody = document.getElementById('resultsBody');
  btn.disabled = true;
  status.innerHTML = '<span class="spinner"></span>Running checks...';
  tbody.innerHTML = '';
  card.style.display = 'none';
  try {
    var resp = await fetch('/api/smoke');
    var data = await resp.json();
    var rows = '';
    Object.entries(data).forEach(function(entry) {
      var k = entry[0], v = entry[1];
      var ok = v.status === 'ok' || v.available === true;
      var cls = ok ? 'ok' : 'fail';
      var icon = ok ? '\\u2713' : '\\u2717';
      var detail = '';
      if (v.error) detail = v.error;
      else if (v.model) detail = v.model;
      else if (v.version) detail = 'v' + v.version;
      rows += '<tr><td><code>' + k + '</code></td><td class="' + cls + '">' + icon + ' ' + (ok ? 'OK' : 'FAIL') + '</td><td style="color:var(--dim);">' + detail + '</td></tr>';
    });
    tbody.innerHTML = rows;
    card.style.display = 'block';
    status.textContent = 'Done.';
  } catch(err) {
    status.innerHTML = '<span class="fail">Error: ' + err.message + '</span>';
  } finally {
    btn.disabled = false;
  }
}
</script>
</body>
</html>"""
