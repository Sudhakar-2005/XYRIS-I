"""Automation persistence + versioning (Phase 9 / spec §22-23, built with the
runtime because the runtime needs run-history storage).

Layout (all under config/automations/, gitignored like permissions.json):
    <id>.json            {"id", "current_version", "versions": [{version,
                          saved_at, workflow}...], "statistics", "idempotency"}
    runs/<id>__<run>.json  one file per run — same pattern as memory/sessions

Writes are atomic (temp + os.replace) under a thread lock — the repo has no
cross-process locking, so we at least never leave a torn file.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from .schema import Workflow, new_id, now

MAX_IDEMPOTENCY_KEYS = 200
RUN_SUFFIX = ".json"


def _default_base() -> Path:
    try:
        from memory.memory_manager import BASE_DIR  # type: ignore
        return Path(BASE_DIR) / "config" / "automations"
    except Exception:
        return Path(__file__).resolve().parents[2] / "config" / "automations"


class AutomationStore:
    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base = Path(base_dir) if base_dir else _default_base()
        self.runs_dir = self.base / "runs"
        self._lock = threading.RLock()

    # ------------------------------------------------------------- helpers

    def _path(self, wf_id: str) -> Path:
        safe = "".join(c for c in str(wf_id) if c.isalnum() or c in "-_")
        if not safe:
            raise ValueError("invalid workflow id")
        return self.base / f"{safe}.json"

    def _run_path(self, wf_id: str, run_id: str) -> Path:
        safe_w = "".join(c for c in str(wf_id) if c.isalnum() or c in "-_")
        safe_r = "".join(c for c in str(run_id) if c.isalnum() or c in "-_")
        if not safe_w or not safe_r:
            raise ValueError("invalid id")
        return self.runs_dir / f"{safe_w}__{safe_r}{RUN_SUFFIX}"

    @staticmethod
    def _atomic_write(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        data = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    @staticmethod
    def _read(path: Path) -> dict | None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    # ---------------------------------------------------------- workflows

    def save(self, workflow: Workflow, *, bump_version: bool = True,
             force: bool = False) -> Workflow:
        """Persist; every content change becomes a new version entry."""
        with self._lock:
            path = self._path(workflow.id)
            doc = self._read(path) or {
                "id": workflow.id, "current_version": 0,
                "versions": [], "statistics": {}, "idempotency": {},
                "created_at": now(),
            }
            versions: list = doc.get("versions") or []
            current = None
            for entry in versions:
                if int(entry.get("version", 0)) == int(workflow.version):
                    current = entry.get("workflow") or {}
                    break

            if bump_version:
                if current is not None and _same(current, workflow.to_dict()) and not force:
                    return workflow
                if current is not None:
                    workflow.version = int(workflow.version) + 1
                elif versions and int(workflow.version) <= max(
                        int(v.get("version", 0)) for v in versions):
                    workflow.version = max(int(v.get("version", 0))
                                           for v in versions) + 1
                workflow.updated_at = now()
            workflow.metadata = workflow.metadata or {}
            doc["current_version"] = workflow.version
            versions = [v for v in versions
                        if int(v.get("version", 0)) != int(workflow.version)]
            versions.append({"version": workflow.version,
                             "saved_at": now(),
                             "workflow": workflow.to_dict()})
            versions.sort(key=lambda v: int(v.get("version", 0)))
            doc["versions"] = versions
            doc["statistics"] = workflow.statistics or doc.get("statistics") or {}
            self._atomic_write(path, doc)
            return workflow

    def load(self, wf_id: str) -> Workflow | None:
        with self._lock:
            doc = self._read(self._path(wf_id))
        if not doc:
            return None
        versions = doc.get("versions") or []
        if not versions:
            return None
        target = int(doc.get("current_version") or versions[-1]["version"])
        for entry in versions:
            if int(entry.get("version", 0)) == target:
                try:
                    return Workflow.from_dict(entry.get("workflow") or {})
                except Exception:
                    return None
        return None

    def exists(self, wf_id: str) -> bool:
        return self._path(wf_id).is_file()

    def list(self, status: str | None = None) -> list[dict]:
        out: list[dict] = []
        if not self.base.is_dir():
            return out
        for path in sorted(self.base.glob("*.json")):
            doc = self._read(path)
            if not doc or not doc.get("versions"):
                continue
            versions = doc["versions"]
            latest = versions[-1].get("workflow") or {}
            if status and str(latest.get("status", "")) != status:
                continue
            out.append({
                "id": latest.get("id") or doc.get("id"),
                "name": latest.get("name", ""),
                "description": latest.get("description", ""),
                "status": latest.get("status", ""),
                "version": doc.get("current_version", 1),
                "trigger": (latest.get("trigger") or {}).get("type", "manual"),
                "updated_at": latest.get("updated_at", 0),
                "created_at": latest.get("created_at", 0),
                "last_run": latest.get("last_run"),
                "statistics": doc.get("statistics") or {},
                "risk": _safe_risk(latest),
            })
        out.sort(key=lambda d: d.get("updated_at") or 0, reverse=True)
        return out

    def delete(self, wf_id: str) -> bool:
        with self._lock:
            path = self._path(wf_id)
            existed = path.is_file()
            if existed:
                try:
                    path.unlink()
                except OSError:
                    return False
            if self.runs_dir.is_dir():
                for r in self.runs_dir.glob(f"{wf_id}__*{RUN_SUFFIX}"):
                    try:
                        r.unlink()
                    except OSError:
                        pass
            return existed

    # ----------------------------------------------------------- versions

    def versions(self, wf_id: str) -> list[dict]:
        with self._lock:
            doc = self._read(self._path(wf_id))
        if not doc:
            return []
        return [{"version": int(v.get("version", 0)),
                 "saved_at": v.get("saved_at", 0),
                 "name": (v.get("workflow") or {}).get("name", ""),
                 "status": (v.get("workflow") or {}).get("status", ""),
                 "current": int(v.get("version", 0)) == int(doc.get("current_version", 0))}
                for v in sorted(doc.get("versions") or [],
                                key=lambda x: int(x.get("version", 0)))]

    def get_version(self, wf_id: str, version: int) -> Workflow | None:
        with self._lock:
            doc = self._read(self._path(wf_id))
        if not doc:
            return None
        for entry in doc.get("versions") or []:
            if int(entry.get("version", 0)) == int(version):
                try:
                    return Workflow.from_dict(entry.get("workflow") or {})
                except Exception:
                    return None
        return None

    def restore(self, wf_id: str, version: int) -> Workflow | None:
        """Restore = re-save the old content as version N+1 (history kept)."""
        old = self.get_version(wf_id, version)
        if old is None:
            return None
        current = self.load(wf_id)
        old.id = wf_id
        if current:
            old.created_at = current.created_at
            old.version = current.version
            old.statistics = dict(current.statistics or {})
        old.metadata = dict(old.metadata or {})
        old.metadata["restored_from_version"] = int(version)
        return self.save(old, bump_version=True)

    # --------------------------------------------------------------- runs

    def record_run(self, run: Any) -> None:
        d = run.to_dict() if hasattr(run, "to_dict") else dict(run)
        wf_id = str(d.get("workflow_id", ""))
        run_id = str(d.get("run_id", ""))
        if not wf_id or not run_id:
            return
        with self._lock:
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            self._atomic_write(self._run_path(wf_id, run_id), d)

    def get_run(self, wf_id: str, run_id: str) -> dict | None:
        with self._lock:
            return self._read(self._run_path(wf_id, run_id))

    def list_runs(self, wf_id: str, limit: int = 50) -> list[dict]:
        out: list[dict] = []
        if not self.runs_dir.is_dir():
            return out
        for path in self.runs_dir.glob(f"{wf_id}__*{RUN_SUFFIX}"):
            doc = self._read(path)
            if doc:
                out.append({k: doc.get(k) for k in
                            ("run_id", "workflow_id", "version", "status", "mode",
                             "trigger", "started_at", "finished_at", "stats",
                             "error")})
        out.sort(key=lambda d: d.get("started_at") or 0, reverse=True)
        return out[:max(1, int(limit))]

    def all_runs(self, limit: int = 100) -> list[dict]:
        out: list[dict] = []
        if not self.runs_dir.is_dir():
            return out
        for path in self.runs_dir.glob(f"*{RUN_SUFFIX}"):
            doc = self._read(path)
            if doc:
                out.append({k: doc.get(k) for k in
                            ("run_id", "workflow_id", "version", "status", "mode",
                             "trigger", "started_at", "finished_at", "stats",
                             "error")})
        out.sort(key=lambda d: d.get("started_at") or 0, reverse=True)
        return out[:max(1, int(limit))]

    # ------------------------------------------------------- idempotency

    def idempotency_done(self, wf_id: str, key: str) -> bool:
        with self._lock:
            doc = self._read(self._path(wf_id)) or {}
            return str(key) in (doc.get("idempotency") or {})

    def mark_idempotency(self, wf_id: str, key: str, run_id: str) -> None:
        with self._lock:
            path = self._path(wf_id)
            doc = self._read(path)
            if not doc:
                return
            table: dict = dict(doc.get("idempotency") or {})
            table[str(key)] = {"run_id": run_id, "at": now()}
            while len(table) > MAX_IDEMPOTENCY_KEYS:
                oldest = min(table, key=lambda k: (table[k] or {}).get("at", 0))
                table.pop(oldest, None)
            doc["idempotency"] = table
            self._atomic_write(path, doc)

    # --------------------------------------------------------- statistics

    def update_statistics(self, wf_id: str, run: Any) -> dict:
        with self._lock:
            path = self._path(wf_id)
            doc = self._read(path) or {}
            stats = dict(doc.get("statistics") or {})
            stats["runs"] = int(stats.get("runs", 0)) + 1
            status = getattr(run, "status", "") or (run.get("status") if isinstance(run, dict) else "")
            if str(status) == "COMPLETED":
                stats["completed"] = int(stats.get("completed", 0)) + 1
            elif str(status) == "FAILED":
                stats["failed"] = int(stats.get("failed", 0)) + 1
            elif str(status) == "CANCELLED":
                stats["cancelled"] = int(stats.get("cancelled", 0)) + 1
            started = getattr(run, "started_at", 0) or (
                run.get("started_at") if isinstance(run, dict) else 0) or 0
            stats["last_run"] = {"run_id": getattr(run, "run_id", None) or
                                 (run.get("run_id") if isinstance(run, dict) else None),
                                 "status": str(status),
                                 "started_at": started}
            doc["statistics"] = stats
            versions = doc.get("versions") or []
            if versions:
                latest = versions[-1].get("workflow") or {}
                latest["last_run"] = stats["last_run"]
                latest["statistics"] = stats
                versions[-1]["workflow"] = latest
                doc["versions"] = versions
            self._atomic_write(path, doc)
            return stats

    def set_status(self, wf_id: str, status: str) -> Workflow | None:
        wf = self.load(wf_id)
        if wf is None:
            return None
        if wf.status == status:
            return wf
        wf.status = status
        return self.save(wf, bump_version=True)


def _safe_risk(wf_dict: dict) -> str:
    best = "safe"
    order = ("safe", "caution", "dangerous", "forbidden")
    for n in wf_dict.get("nodes") or []:
        r = str((n or {}).get("risk", ""))
        if r in order and order.index(r) > order.index(best):
            best = r
    return best


def _same(a: dict, b: dict) -> bool:
    keys = ("name", "description", "status", "trigger", "nodes", "edges",
            "variables", "permissions")
    for k in keys:
        if json.dumps(a.get(k), sort_keys=True, default=str) != \
                json.dumps(b.get(k), sort_keys=True, default=str):
            return False
    return True
