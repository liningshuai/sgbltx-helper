"""Temporary two-instance browser acceptance server; it never constructs Maa."""

from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from sgbltx_helper.dashboard.server import create_server


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="sgbltx-dashboard-r1-r2-"))
    artifacts = root / "artifacts"
    settings = root / "settings.json"
    finish_first = root / "finish-first.fake"
    restart = root / "restart.fake"
    finish_all = root / "finish-all.fake"
    count_file = root / "executor-count.json"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    generation = 1
    dropped = 0
    guard = threading.Lock()

    def fake_executor(task_id, profile_id, mode, stop):
        count = json.loads(count_file.read_text(encoding="utf-8"))["count"] if count_file.exists() else 0
        count_file.write_text(json.dumps({"count": count + 1}), encoding="utf-8")
        if generation == 1:
            while not finish_first.exists():
                time.sleep(0.02)
            return {"status": "cancelled", "action_count": 0, "detail": {}}
        return {
            "status": "needs_attention", "reason_code": "stop_unconfirmed", "action_count": 1,
            "detail": {
                "action_count_is_upper_bound": True, "input_outcome_uncertain": True,
                "navigation_action_count": 1,
            },
        }

    def create_generation():
        server, token, manager = create_server(
            Path(__file__).resolve().parents[1], port=port, open_browser=False,
            executor=fake_executor, artifacts_root=artifacts, settings_path=settings,
        )
        original_post = server.RequestHandlerClass.do_POST
        original_json = server.RequestHandlerClass._json

        def drop_first_job_response(handler, status, value):
            nonlocal dropped
            with guard:
                should_drop = generation == 1 and urlsplit(handler.path).path == "/api/jobs" and status == 202 and dropped < 2
                if should_drop:
                    dropped += 1
            if should_drop:
                print(f"DROPPED_FIRST_RESPONSE={dropped}", flush=True)
                if dropped == 2:
                    finish_first.touch()
                    restart.touch()
                handler.close_connection = True
                try:
                    handler.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                handler.connection.close()
                return
            original_json(handler, status, value)

        server.RequestHandlerClass._json = drop_first_job_response
        server.RequestHandlerClass.do_POST = original_post
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        return server, manager, thread

    server, manager, thread = create_generation()
    print(f"URL=http://127.0.0.1:{port}/", flush=True)
    print(f"SESSION_DIR={root}", flush=True)
    print("FAKE_ONLY=true", flush=True)
    try:
        while not restart.exists():
            time.sleep(0.05)
        finish_first.touch()
        while manager.snapshot()["active"]:
            time.sleep(0.02)
        server.shutdown()
        server.server_close()
        thread.join(2)
        generation = 2
        server, manager, thread = create_generation()
        state = manager.snapshot()
        print("SECOND_INSTANCE_READY=true", flush=True)
        print(f"EXECUTOR_COUNT_FILE={count_file}", flush=True)
        while not finish_all.exists():
            time.sleep(0.05)
    except KeyboardInterrupt:
        finish_first.touch()
        manager.request_shutdown_stop()
        while manager.snapshot()["active"]:
            time.sleep(0.05)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
    print(f"FAKE_EXECUTOR_COUNT={json.loads(count_file.read_text(encoding='utf-8'))['count'] if count_file.exists() else 0}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
