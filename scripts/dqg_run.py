#!/usr/bin/env python3
"""DQG runner - works from any directory on any OS.

Uses only stdlib. Called by the /dqg opencode command.

Subcommands:
  auto-review  Full auto: start services, run review async, poll, print results
  start        Launch review as detached background process
  status       Check if the latest review is complete
  report       Print the latest report
  check-proxy  Check if LiteLLM proxy is running
  locate       Print the DQG project root path
"""

import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

DQG_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = DQG_ROOT / "src"
RUNS_DIR = DQG_ROOT / "outputs" / "runs"
_MARKER_FILE = DQG_ROOT / "outputs" / ".active_review"
_ENV_FILE = DQG_ROOT / ".env"


def _load_env():
    if not _ENV_FILE.exists():
        return
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def _venv_python():
    if os.name == "nt":
        return DQG_ROOT / ".venv" / "Scripts" / "python.exe"
    return DQG_ROOT / ".venv" / "bin" / "python"


def _check_url(url):
    try:
        return urlopen(url, timeout=3).status == 200
    except Exception:
        return False


def _check_proxy():
    return _check_url("http://localhost:4000/health/liveliness")


def _check_web():
    return _check_url("http://localhost:8080/api/status")


def _latest_run_dir():
    if not RUNS_DIR.exists():
        return None
    runs = sorted([d for d in RUNS_DIR.iterdir() if d.is_dir()], key=lambda p: p.stat().st_mtime)
    return runs[-1] if runs else None


def _read_marker():
    if not _MARKER_FILE.exists():
        return {}
    result = {}
    for line in _MARKER_FILE.read_text(encoding="utf-8").strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def _write_marker(**kwargs):
    _MARKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    _MARKER_FILE.write_text("\n".join(f"{k}={v}" for k, v in kwargs.items()), encoding="utf-8")


def _clear_marker():
    if _MARKER_FILE.exists():
        _MARKER_FILE.unlink()


def _find_run_id_in_log(log_path):
    if not log_path.exists():
        return None
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    m = re.search(r"run_id=(\S+)", text)
    return m.group(1) if m else None


def _wait_for(check_fn, label, max_attempts=30, interval=2.0):
    for i in range(max_attempts):
        if check_fn():
            print(f"{label}_READY")
            return True
        time.sleep(interval)
    print(f"{label}_TIMEOUT")
    return False


def _api_post(url, data, timeout=10):
    try:
        body = json.dumps(data).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"})
        r = urlopen(req, timeout=timeout)
        return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def _api_get(url, timeout=10):
    try:
        r = urlopen(url, timeout=timeout)
        return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def _nt_startup():
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return si


def _nt_flags():
    return subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS


def _start_proxy():
    _load_env()
    litellm_config = DQG_ROOT / "config" / "litellm" / "config.yaml"
    if not litellm_config.exists():
        litellm_config = DQG_ROOT / "config" / "litellm_config.yaml"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if os.name == "nt":
        litellm_exe = str(DQG_ROOT / ".venv" / "Scripts" / "litellm.exe")
        subprocess.Popen(
            [litellm_exe, "--config", str(litellm_config), "--port", "4000"],
            cwd=str(DQG_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=_nt_startup(),
            creationflags=_nt_flags(),
        )
    else:
        venv_py = str(_venv_python())
        subprocess.Popen(
            [venv_py, "-m", "litellm", "--config", str(litellm_config), "--port", "4000"],
            cwd=str(DQG_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def _start_web_server():
    _load_env()
    venv_py = str(_venv_python())
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_DIR)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if os.name == "nt":
        subprocess.Popen(
            [str(venv_py), "-m", "app.cli", "web", "--port", "8080"],
            cwd=str(DQG_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=_nt_startup(),
            creationflags=_nt_flags(),
        )
    else:
        subprocess.Popen(
            [venv_py, "-m", "app.cli", "web", "--port", "8080"],
            cwd=str(DQG_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def cmd_auto_review(args):
    _load_env()
    launch_args = argparse.Namespace(
        doc_path=args.doc_path,
        project=args.project,
        type=args.type,
        context_path=getattr(args, "context_path", None),
    )
    review_id = None
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            cmd_launch(launch_args)
        except SystemExit:
            pass
    output = buf.getvalue()
    print(output, end="")
    for line in output.splitlines():
        if line.startswith("REVIEW_STARTED"):
            m = re.search(r"review_id=(\S+)", line)
            if m:
                review_id = m.group(1)
    if not review_id:
        sys.exit(1)

    poll_args = argparse.Namespace(
        review_id=review_id,
        max_attempts=120,
    )
    cmd_poll(poll_args)


def cmd_launch(args):
    _load_env()
    venv_py = _venv_python()
    if not venv_py.exists():
        print(f"ERROR: Virtual environment not found at {venv_py}")
        print("Run the setup script first to create the venv.")
        sys.exit(1)

    doc_path = str(Path(args.doc_path).resolve())
    project_path = str(Path(args.project).resolve()) if args.project else None
    doc_type = args.type
    context_path = getattr(args, "context_path", None)
    if context_path:
        context_path = str(Path(context_path).resolve())

    print(f"DOC_PATH: {doc_path}")
    if project_path:
        print(f"PROJECT_PATH: {project_path}")
    if context_path:
        print(f"CONTEXT_PATH: {context_path}")

    if _check_proxy():
        print("PROXY_OK")
    else:
        print("PROXY_DOWN - starting LiteLLM proxy...")
        _start_proxy()
        if not _wait_for(_check_proxy, "PROXY", max_attempts=30, interval=2.0):
            print("FATAL: LiteLLM proxy could not start. Check .env for ZAI_API_KEY.")
            sys.exit(1)

    if _check_web():
        print("WEB_OK")
    else:
        print("WEB_DOWN - starting DQG web server...")
        _start_web_server()
        if not _wait_for(_check_web, "WEB", max_attempts=15, interval=2.0):
            print("FATAL: DQG web server could not start.")
            sys.exit(1)

    payload = {"file_path": doc_path, "project_path": project_path or "."}
    if doc_type:
        payload["doc_type"] = doc_type
    if context_path:
        payload["context_path"] = context_path

    result = _api_post("http://localhost:8080/api/review/start", payload, timeout=10)
    if not result or "error" in result:
        print(f"FATAL: Could not start review: {result}")
        sys.exit(1)

    review_id = result.get("review_id")
    if not review_id:
        print(f"FATAL: No review_id in response: {result}")
        sys.exit(1)

    print(f"REVIEW_STARTED review_id={review_id}")
    print(f"Use: python {__file__} poll {review_id}")


def cmd_poll(args):
    review_id = args.review_id
    max_attempts = args.max_attempts

    status = "unknown"
    for attempt in range(max_attempts):
        status_data = _api_get(f"http://localhost:8080/api/review/status/{review_id}", timeout=10)
        if not status_data or status_data.get("error"):
            print(f"POLL_RETRY attempt={attempt + 1}/{max_attempts}")
            time.sleep(10)
            continue

        status = status_data.get("status", "unknown")
        if status == "complete":
            print("REVIEW_COMPLETE")
            rr = status_data.get("result", {})
            score = rr.get("overall_score", "?")
            passed = rr.get("passed", "?")
            action = rr.get("recommended_next_action", "?")
            print(f"SCORE: {score}/10 | {'PASS' if passed else 'FAIL'} | Action: {action}")

            for key, label in [("cross_ref_issues", "CROSS_REF_ISSUES"), ("quality_issues", "QUALITY_ISSUES")]:
                items = rr.get(key, [])
                if items:
                    print(f"\n{label} ({len(items)}):")
                    for item in items[:10]:
                        print(f"  - [{item.get('severity', '?')}] {item.get('description', str(item))}")

            dims = rr.get("dimension_scores", {})
            if dims:
                print(f"\nDIMENSION_SCORES:")
                for dim, val in dims.items():
                    print(f"  {dim}: {val}")

            print(f"\nREVIEW_ID: {review_id}")
            return

        if status == "failed":
            print(f"REVIEW_FAILED: {status_data.get('error', 'unknown error')}")
            sys.exit(1)

        print(f"STATUS: {status} (attempt {attempt + 1}/{max_attempts})")
        time.sleep(10)

    print(f"POLL_INCOMPLETE status={status} - run again with same command to continue polling")


def cmd_start(args):
    venv_py = _venv_python()
    if not venv_py.exists():
        print(f"ERROR: Virtual environment not found at {venv_py}")
        sys.exit(1)
    if not _check_proxy():
        print("ERROR: LiteLLM proxy is not running at http://localhost:4000")
        sys.exit(1)

    doc_path = str(Path(args.doc_path).resolve())
    project_path = str(Path(args.project).resolve())
    cmd = [str(venv_py), "-m", "app.cli", "review", doc_path, "--project", project_path]
    if args.type:
        cmd.extend(["-t", args.type])
    context_path = getattr(args, "context_path", None)
    if context_path:
        cmd.extend(["--cp", str(Path(context_path).resolve())])

    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_DIR)
    log_path = DQG_ROOT / "outputs" / "review.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    kwargs = {
        "cwd": str(DQG_ROOT),
        "env": env,
        "stdout": open(str(log_path), "w", encoding="utf-8"),
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **kwargs)
    _write_marker(
        pid=str(proc.pid),
        doc_path=doc_path,
        project_path=project_path,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        status="RUNNING",
    )
    time.sleep(2)

    if proc.poll() is not None:
        _write_marker(
            pid=str(proc.pid),
            doc_path=doc_path,
            project_path=project_path,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            status="FAILED",
        )
        print(f"ERROR: Review process exited immediately with code {proc.returncode}")
        sys.exit(1)

    print("REVIEW_STARTED")
    print(f"PID: {proc.pid}")
    run_id = _find_run_id_in_log(log_path)
    if run_id:
        print(f"Run ID: {run_id}")


def cmd_status(args):
    marker = _read_marker()
    if not marker:
        print("NO_ACTIVE_REVIEW")
        return

    pid = int(marker.get("pid", 0))
    alive = False
    if pid:
        try:
            if os.name == "nt":
                proc = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, timeout=5
                )
                alive = str(pid) in proc.stdout
            else:
                os.kill(pid, 0)
                alive = True
        except Exception:
            alive = False

    run_dir = _latest_run_dir()
    has_results = (
        run_dir and (run_dir / "scorecard.json").exists() and (run_dir / "report.md").exists() if run_dir else False
    )

    if has_results:
        _clear_marker()
        print("COMPLETE")
        print(f"Run: {run_dir.name}")
        try:
            data = json.loads((run_dir / "scorecard.json").read_text(encoding="utf-8"))
            print(f"Score: {data.get('overall_score', '?')}/10 | {'PASS' if data.get('passed') else 'FAIL'}")
        except Exception:
            pass
    elif not alive:
        _clear_marker()
        print("FAILED")
    else:
        print("RUNNING")
        print(f"PID: {pid}")


def cmd_review(args):
    venv_py = _venv_python()
    doc_path = str(Path(args.doc_path).resolve())
    project_path = str(Path(args.project).resolve())
    cmd = [str(venv_py), "-m", "app.cli", "review", doc_path, "--project", project_path]
    if args.type:
        cmd.extend(["-t", args.type])
    context_path = getattr(args, "context_path", None)
    if context_path:
        cmd.extend(["--cp", str(Path(context_path).resolve())])
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_DIR)
    result = subprocess.run(cmd, cwd=str(DQG_ROOT), env=env)
    sys.exit(result.returncode)


def cmd_report(args):
    run_dir = _latest_run_dir()
    if not run_dir or not (run_dir / "report.md").exists():
        print("No report found.")
        sys.exit(1)
    print((run_dir / "report.md").read_text(encoding="utf-8"))


def cmd_locate(args):
    print(DQG_ROOT)


def cmd_check_proxy(args):
    print("PROXY_OK" if _check_proxy() else "PROXY_DOWN")


def main():
    parser = argparse.ArgumentParser(description="DQG Runner")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("launch", help="Start services + launch async review (returns immediately)")
    p.add_argument("doc_path")
    p.add_argument("--project", "-p", default=None)
    p.add_argument("--type", "-t", default=None)
    p.add_argument("--cp", dest="context_path", default=None, help="Path to domain context directory")
    p.set_defaults(func=cmd_launch)

    p = sub.add_parser("poll", help="Poll for review results")
    p.add_argument("review_id")
    p.add_argument("--max-attempts", "-n", type=int, default=6, help="Max poll attempts (default 6, ~1 min)")
    p.set_defaults(func=cmd_poll)

    p = sub.add_parser("auto-review", help="Launch + poll in one command (may timeout)")
    p.add_argument("doc_path")
    p.add_argument("--project", "-p", default=None)
    p.add_argument("--type", "-t", default=None)
    p.add_argument("--cp", dest="context_path", default=None, help="Path to domain context directory")
    p.set_defaults(func=cmd_auto_review)

    p = sub.add_parser("start")
    p.add_argument("doc_path")
    p.add_argument("--project", "-p", required=True)
    p.add_argument("--type", "-t", default=None)
    p.add_argument("--cp", dest="context_path", default=None, help="Path to domain context directory")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("review")
    p.add_argument("doc_path")
    p.add_argument("--project", "-p", required=True)
    p.add_argument("--type", "-t", default=None)
    p.add_argument("--cp", dest="context_path", default=None, help="Path to domain context directory")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("report")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("locate")
    p.set_defaults(func=cmd_locate)

    p = sub.add_parser("check-proxy")
    p.set_defaults(func=cmd_check_proxy)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
