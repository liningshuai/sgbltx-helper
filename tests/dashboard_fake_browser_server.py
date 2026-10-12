"""Temporary browser-acceptance server. Never constructs a Maa backend."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from sgbltx_helper.dashboard.server import create_server


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="sgbltx-dashboard-browser-"))
    artifacts = root / "artifacts"
    settings = root / "settings.json"
    finish = root / "finish.fake"
    count_file = root / "executor-count.json"
    started = threading.Event()

    def fake_executor(task_id, profile_id, mode, stop):
        count = 0
        if count_file.exists():
            count = json.loads(count_file.read_text(encoding="utf-8"))["count"]
        count += 1
        count_file.write_text(json.dumps({"count": count}), encoding="utf-8")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"run-{timestamp}-{os.getpid()}"
        run_dir = artifacts / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        events = [
            {"node": "start", "event": "run_started", "detail": {"dashboard_job_id": stop.dashboard_job_id, "profile": f"profile={profile_id}"}},
            {"node": "more", "event": "input_submitted", "detail": {"action_count": 1}},
            {"node": "mail", "event": "input_submitted", "detail": {"action_count": 2}},
        ]
        (run_dir / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        started.set()
        while not finish.exists():
            time.sleep(0.02)
        return {
            "status": "cancelled" if stop.should_stop() else "nothing_to_claim",
            "action_count": 2,
            "detail": {"navigation_action_count": 2, "claim_action_count": 0, "dismiss_action_count": 0},
        }

    server, _, manager = create_server(
        Path(__file__).resolve().parents[1], port=0, open_browser=False,
        executor=fake_executor, artifacts_root=artifacts, settings_path=settings,
    )
    handler = server.RequestHandlerClass
    original_post = handler.do_POST

    def delayed_post(request_handler):
        if urlsplit(request_handler.path).path == "/api/jobs":
            time.sleep(1.25)
        original_post(request_handler)

    handler.do_POST = delayed_post
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    address = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"URL={address}", flush=True)
    print(f"SESSION_DIR={root}", flush=True)
    print(f"FAKE_ONLY=true", flush=True)
    try:
        while thread.is_alive():
            time.sleep(0.2)
    except KeyboardInterrupt:
        manager.request_shutdown_stop()
        finish.touch()
        while manager.snapshot()["active"]:
            time.sleep(0.05)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
