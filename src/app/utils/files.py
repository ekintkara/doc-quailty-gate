from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_run_dir(base_dir: str) -> tuple[str, Path]:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = ts
    run_dir = Path(base_dir) / run_id
    ensure_dir(run_dir)
    return run_id, run_dir


def write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_json(path: Path, data: dict | list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def find_run_dir(base_dir: str, run_id: str) -> Optional[Path]:
    run_path = Path(base_dir) / run_id
    if run_path.exists():
        return run_path
    return None


def find_latest_run(base_dir: str) -> Optional[Path]:
    runs = Path(base_dir)
    if not runs.exists():
        return None
    dirs = sorted(runs.iterdir(), reverse=True)
    for d in dirs:
        if d.is_dir():
            return d
    return None
