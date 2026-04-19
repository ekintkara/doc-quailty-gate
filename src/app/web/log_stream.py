from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from typing import Optional

import structlog

logger = structlog.get_logger("log_stream")


class LogBroadcaster:
    _instance: Optional[LogBroadcaster] = None

    def __init__(self, max_history: int = 500):
        self._subscribers: dict[str, asyncio.Queue] = {}
        self._history: deque[dict] = deque(maxlen=max_history)
        self._stage_timings: dict[str, float] = {}
        self._pipeline_starts: dict[str, float] = {}
        self._active_run_id: Optional[str] = None
        self._setup_state: dict = {
            "status": "idle",
            "current_step": "",
            "step_number": 0,
            "total_steps": 7,
            "errors": [],
            "started_at": None,
            "finished_at": None,
        }

    @classmethod
    def get(cls) -> LogBroadcaster:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def active_run_id(self) -> Optional[str]:
        return self._active_run_id

    def set_active_run(self, run_id: Optional[str]) -> None:
        self._active_run_id = run_id

    def subscribe(self) -> tuple[str, asyncio.Queue]:
        client_id = str(uuid.uuid4())[:8]
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[client_id] = queue
        for msg in self._history:
            queue.put_nowait(msg)
        return client_id, queue

    def unsubscribe(self, client_id: str) -> None:
        self._subscribers.pop(client_id, None)

    def publish(self, message: dict) -> None:
        self._history.append(message)
        dead = []
        for cid, queue in self._subscribers.items():
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                dead.append(cid)
        for cid in dead:
            self._subscribers.pop(cid, None)

    def push_log(self, level: str, message: str, source: str = "system", run_id: Optional[str] = None, **extra) -> None:
        entry = {
            "type": "log",
            "level": level,
            "message": message,
            "source": source,
            "timestamp": time.time(),
            "run_id": run_id or self._active_run_id,
            **extra,
        }
        self.publish(entry)

    def push_setup_step(self, step: str, step_number: int, total_steps: int = 7, status: str = "running") -> None:
        self._setup_state["current_step"] = step
        self._setup_state["step_number"] = step_number
        self._setup_state["total_steps"] = total_steps
        self._setup_state["status"] = "running"
        entry = {
            "type": "setup_step",
            "step": step,
            "step_number": step_number,
            "total_steps": total_steps,
            "status": status,
            "timestamp": time.time(),
        }
        self.publish(entry)

    def push_setup_done(self, success: bool, errors: Optional[list] = None) -> None:
        self._setup_state["status"] = "completed" if success else "failed"
        self._setup_state["finished_at"] = time.time()
        self._setup_state["errors"] = errors or []
        entry = {
            "type": "setup_done",
            "success": success,
            "errors": errors or [],
            "timestamp": time.time(),
        }
        self.publish(entry)

    def push_pipeline_stage(self, run_id: str, stage: str, status: str = "running", detail: str = "") -> None:
        now = time.time()
        duration_ms = None
        if status == "running":
            self._stage_timings[f"{run_id}:{stage}"] = now
            if f"{run_id}:pipeline" not in self._pipeline_starts:
                self._pipeline_starts[run_id] = now
                self._active_run_id = run_id
        elif status in ("done", "error"):
            start = self._stage_timings.pop(f"{run_id}:{stage}", None)
            if start:
                duration_ms = round((now - start) * 1000)

        entry = {
            "type": "pipeline_stage",
            "run_id": run_id,
            "stage": stage,
            "status": status,
            "detail": detail,
            "duration_ms": duration_ms,
            "timestamp": now,
        }
        self.publish(entry)

    def push_pipeline_done(
        self, run_id: str, score: Optional[float] = None, passed: Optional[bool] = None, turkish_summary: str = ""
    ) -> None:
        now = time.time()
        duration_ms = None
        start = self._pipeline_starts.pop(run_id, None)
        if start:
            duration_ms = round((now - start) * 1000)
        if self._active_run_id == run_id:
            self._active_run_id = None
        entry = {
            "type": "pipeline_done",
            "run_id": run_id,
            "score": score,
            "passed": passed,
            "duration_ms": duration_ms,
            "turkish_summary": turkish_summary,
            "timestamp": now,
        }
        self.publish(entry)

    def push_llm_call(
        self,
        *,
        stage: str,
        model_group: str,
        model_used: str,
        messages: list[dict],
        response_content: str,
        usage: dict,
        duration_ms: float,
        run_id: Optional[str] = None,
    ) -> None:
        MAX_MSG_PREVIEW = 300
        MAX_RESP_PREVIEW = 500

        request_summary = []
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            preview = content[:MAX_MSG_PREVIEW] + ("..." if len(content) > MAX_MSG_PREVIEW else "")
            request_summary.append({"role": role, "preview": preview})

        resp_preview = response_content[:MAX_RESP_PREVIEW] + ("..." if len(response_content) > MAX_RESP_PREVIEW else "")

        entry = {
            "type": "llm_call",
            "stage": stage,
            "model_group": model_group,
            "model_used": model_used,
            "request_summary": request_summary,
            "response_preview": resp_preview,
            "response_length": len(response_content),
            "tokens_prompt": usage.get("prompt_tokens", 0),
            "tokens_completion": usage.get("completion_tokens", 0),
            "tokens_total": usage.get("total_tokens", 0),
            "duration_ms": round(duration_ms),
            "timestamp": time.time(),
            "run_id": run_id or self._active_run_id,
        }
        self.publish(entry)

    @property
    def setup_state(self) -> dict:
        return dict(self._setup_state)


broadcaster = LogBroadcaster.get()


class WebSocketLogProcessor:
    def __init__(self):
        self.broadcaster = LogBroadcaster.get()

    def __call__(self, logger, method_name, event_dict):
        try:
            level = event_dict.get("level", method_name)
            message = event_dict.get("event", "")
            event_name = event_dict.pop("event", "")
            source = "system"
            run_id = event_dict.get("run_id")

            if "run_id" in event_dict and "stage" in str(event_dict.get("event", "")):
                source = "pipeline"
            elif "stage" in str(event_dict.get("event", "")):
                source = "pipeline"

            extra = {}
            for key in ["run_id", "stage", "step", "score", "passed", "error", "reason"]:
                if key in event_dict:
                    extra[key] = event_dict[key]

            self.broadcaster.push_log(
                level=level,
                message=str(event_name) if event_name else str(message),
                source=source,
                run_id=run_id,
                **extra,
            )
        except Exception:
            pass
        return event_dict
