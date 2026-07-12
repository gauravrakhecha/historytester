"""
modlog.py — atomic updater for ../modular.json (the realtime workflow index).

Every specialist agent calls this after finishing each file so the completion index
updates in (near) real time. A cross-process spin-lock (exclusive-create lock file)
makes concurrent writes from parallel agents safe — no clobbering, no corruption.

CLI
---
  python .workflow/modlog.py file  <path> <status> [note]   # update files[path]
  python .workflow/modlog.py agent <name> <status>          # update agents[name].status
  python .workflow/modlog.py phase <name> <status>          # update phases[name].status
  python .workflow/modlog.py feature <key> <status>         # feature_parity_checklist[key].status
  python .workflow/modlog.py speed <id> <status>            # speed_objectives[id].status
  python .workflow/modlog.py log   <agent> <event...>       # append loop_log entry

status in: pending | in_progress | done | verified | blocked
Every call also appends a loop_log line and refreshes updated_at.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "modular.json"
LOCK = ROOT / ".workflow" / "modular.lock"


def _acquire(timeout: float = 15.0) -> int:
    start = time.time()
    while True:
        try:
            return os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if time.time() - start > timeout:
                # Stale lock fallback: steal it rather than deadlock the build.
                try:
                    os.remove(LOCK)
                except OSError:
                    pass
            time.sleep(0.05)


def _release(fd: int) -> None:
    try:
        os.close(fd)
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def update(kind: str, key: str, status: str | None, note: str | None, agent: str | None) -> None:
    fd = _acquire()
    try:
        data = json.loads(INDEX.read_text(encoding="utf-8"))
        ts = _now()
        data["updated_at"] = ts
        event = {"ts": ts, "agent": agent or "agent", "event": f"{kind}:{key} -> {status}"}

        if kind == "file":
            entry = data.setdefault("files", {}).setdefault(key, {})
            if status:
                entry["status"] = status
                entry["verified"] = status == "verified"
            if note:
                entry["notes"] = note
            entry["updated_at"] = ts
            if note:
                event["event"] += f" ({note})"
        elif kind == "agent":
            data.setdefault("agents", {}).setdefault(key, {})["status"] = status
        elif kind == "phase":
            data.setdefault("phases", {}).setdefault(key, {})["status"] = status
        elif kind == "feature":
            data.setdefault("feature_parity_checklist", {}).setdefault(key, {})["status"] = status
        elif kind == "speed":
            for obj in data.get("speed_objectives", []):
                if obj.get("id") == key:
                    obj["status"] = status
        elif kind == "log":
            event = {"ts": ts, "agent": key, "event": note or ""}
        else:
            raise SystemExit(f"unknown kind: {kind}")

        data.setdefault("loop_log", []).append(event)
        tmp = INDEX.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, INDEX)  # atomic swap
    finally:
        _release(fd)


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    kind = argv[0]
    if kind == "log":
        agent = argv[1]
        note = " ".join(argv[2:])
        update("log", agent, None, note, agent)
        return
    key = argv[1]
    status = argv[2] if len(argv) > 2 else None
    note = " ".join(argv[3:]) if len(argv) > 3 else None
    update(kind, key, status, note, os.environ.get("MODLOG_AGENT"))


if __name__ == "__main__":
    main(sys.argv[1:])
