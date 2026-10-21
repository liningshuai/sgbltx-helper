"""Loopback-only HTTP application for the local dashboard."""

from __future__ import annotations

import json
import secrets
import sys
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .evidence import list_runs, read_run, resolve_image
from .jobs import Executor, JobManager, default_executor
from .registry import list_catalog, list_profiles, list_tasks
from .settings import SettingsStore

_MAX_BODY = 16 * 1024
_STATIC = {"/", "/index.html", "/app.css", "/app.js"}


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, handler, *, project_root: Path, token: str, executor: Executor | None, artifacts_root: Path | None, settings_path: Path | None):
        super().__init__(address, handler)
        self.dashboard_project_root = Path(project_root).resolve()
        self.dashboard_instance_id = str(uuid.uuid4())
        self.dashboard_token = token
        self.dashboard_settings = SettingsStore(settings_path or self.dashboard_project_root / "config" / "dashboard.local.json")
        self.dashboard_artifacts_root = Path(artifacts_root).resolve() if artifacts_root else self.dashboard_project_root / "artifacts"
        self.dashboard_manager = JobManager(
            self.dashboard_project_root, self.dashboard_settings,
            executor or default_executor(self.dashboard_project_root),
            artifacts_root=self.dashboard_artifacts_root,
        )

    def server_close(self) -> None:
        manager = getattr(self, "dashboard_manager", None)
        if manager is not None:
            manager.request_shutdown_stop()
        super().server_close()


def create_server(
    project_root: Path,
    port: int = 8782,
    open_browser: bool = True,
    executor: Executor | None = None,
    artifacts_root: Path | None = None,
    settings_path: Path | None = None,
) -> tuple[DashboardHTTPServer, str, JobManager]:
    root = Path(project_root).resolve()
    token = secrets.token_urlsafe(32)
    server = DashboardHTTPServer(
        ("127.0.0.1", port), _handler_class(), project_root=root, token=token,
        executor=executor, artifacts_root=artifacts_root, settings_path=settings_path,
    )
    return server, token, server.dashboard_manager


def _handler_class():
    class Handler(BaseHTTPRequestHandler):
        server: DashboardHTTPServer

        def log_message(self, format: str, *args: Any) -> None:
            # Polling is frequent; avoid noisy access logs and never record request headers/tokens.
            return

        def _send(self, status: int, body: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _json(self, status: int, value: Any) -> None:
            body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _error(self, status: int, code: str, message: str) -> None:
            self._json(status, {"error": {"code": code, "message": message}})

        def _request_is_local(self, mutation: bool = False) -> bool:
            host_header = self.headers.get("Host", "")
            try:
                host = urlsplit("//" + host_header)
                if host.username or host.password or host.path or host.query or host.fragment:
                    raise ValueError
                if host.hostname not in {"127.0.0.1", "localhost"} or host.port != self.server.server_address[1]:
                    self._error(403, "host_rejected", "只接受本机回环地址请求")
                    return False
            except ValueError:
                self._error(403, "host_rejected", "Host 请求头不是本机地址")
                return False
            origin = self.headers.get("Origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
                            or parsed.port != self.server.server_address[1] or parsed.username or parsed.password):
                        self._error(403, "origin_rejected", "只接受同源本机请求")
                        return False
                except ValueError:
                    self._error(403, "origin_rejected", "Origin 请求头无效")
                    return False
            elif mutation:
                # Token authentication remains mandatory when browser Origin is absent.
                pass
            if not self.client_address[0].startswith("127."):
                self._error(403, "host_rejected", "只接受本机回环连接")
                return False
            return True

        def _authorized(self) -> bool:
            if not self._request_is_local(mutation=True):
                return False
            if not secrets.compare_digest(self.headers.get("X-Dashboard-Token", ""), self.server.dashboard_token):
                self._error(403, "token_required", "缺少或无效的本地请求令牌")
                return False
            return True

        def _body(self) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._error(400, "invalid_request", "Content-Length 无效")
                return None
            if length < 0 or length > _MAX_BODY:
                self._error(413, "body_too_large", "请求内容超过 16 KiB")
                return None
            try:
                raw = self.rfile.read(length)
                value = json.loads(raw.decode("utf-8")) if length else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error(400, "invalid_request", "请求体必须是 UTF-8 JSON")
                return None
            if not isinstance(value, dict):
                self._error(400, "invalid_request", "请求体必须是 JSON 对象")
                return None
            return value

        def do_GET(self) -> None:
            if not self._request_is_local():
                return
            path = urlsplit(self.path).path
            if path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
                return
            if path == "/api/state":
                settings = self.server.dashboard_settings.load()
                tasks = list_tasks()
                modules = {
                    task["task_id"]: {**task, "enabled": settings["modules"].get(task["task_id"], False)}
                    for task in tasks
                }
                job_state = self.server.dashboard_manager.snapshot()
                self._json(200, {
                    "instance_id": self.server.dashboard_instance_id,
                    "modules": modules,
                    "profiles": list_profiles(self.server.dashboard_project_root),
                    "catalog": list_catalog(),
                    "selected_profile_id": settings["selected_profile_id"],
                    "device_status": "not_checked",
                    "job": job_state["job"],
                    "active_job": job_state["job"] if job_state["active"] else None,
                })
                return
            if path == "/api/runs":
                try:
                    raw_limit = parse_qs(urlsplit(self.path).query).get("limit", ["30"])[0]
                    limit = int(raw_limit)
                    self._json(200, {"runs": list_runs(self.server.dashboard_artifacts_root, limit=limit)})
                except (TypeError, ValueError) as exc:
                    self._error(400, "invalid_request", str(exc))
                return
            if path in _STATIC:
                name = "index.html" if path in {"/", "/index.html"} else path.lstrip("/")
                static_dir = Path(__file__).parent / "static"
                target = static_dir / name
                if not target.is_file():
                    self._error(404, "not_found", "页面资源不存在")
                    return
                content = target.read_bytes()
                content_type = {"index.html": "text/html; charset=utf-8", "app.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8"}[name]
                if name == "index.html":
                    content = content.replace(b"__DASHBOARD_TOKEN__", self.server.dashboard_token.encode("ascii"))
                self._send(200, content, content_type)
                return
            parts = [unquote(part) for part in path.split("/")]
            try:
                if len(parts) == 4 and parts[:2] == ["", "api"] and parts[2] == "runs":
                    self._json(200, read_run(self.server.dashboard_artifacts_root, parts[3]))
                    return
                if (len(parts) == 6 and parts[:2] == ["", "api"] and parts[2] == "runs"
                        and parts[4] == "images"):
                    image = resolve_image(self.server.dashboard_artifacts_root, parts[3], parts[5])
                    self._send(200, image.read_bytes(), "image/png")
                    return
            except ValueError as exc:
                self._error(404, "not_found", str(exc))
                return
            self._error(404, "not_found", "接口或页面不存在")

        def do_POST(self) -> None:
            if not self._authorized():
                return
            body = self._body()
            if body is None:
                return
            path = urlsplit(self.path).path
            try:
                if path == "/api/settings":
                    if "task_id" in body or "enabled" in body:
                        if set(body) != {"task_id", "enabled"}:
                            raise ValueError("设置请求需要 task_id 和 enabled")
                        settings = self.server.dashboard_settings.set_enabled(body["task_id"], body["enabled"])
                    elif set(body) == {"profile_id"}:
                        settings = self.server.dashboard_settings.set_selected_profile(body["profile_id"])
                    else:
                        raise ValueError("设置请求格式无效")
                    self._json(200, settings)
                    return
                if path == "/api/jobs":
                    if set(body) != {"request_id", "task_id", "profile_id", "mode", "instance_id"}:
                        raise ValueError("作业请求字段无效")
                    if body["instance_id"] != self.server.dashboard_instance_id:
                        self._error(409, "stale_instance", "恢复请求来自已重启的服务实例；原任务结果无法确认，未启动新作业。请先显式放弃旧恢复记录。")
                        return
                    job = self.server.dashboard_manager.start(
                        body["request_id"], body["task_id"], body["profile_id"], body["mode"]
                    )
                    self._json(202, {"job": job})
                    return
                parts = [unquote(part) for part in path.split("/")]
                if len(parts) == 5 and parts[:3] == ["", "api", "jobs"] and parts[4] == "stop":
                    job = self.server.dashboard_manager.request_stop(parts[3])
                    self._json(200, {"job": job})
                    return
                self._error(404, "not_found", "接口不存在")
            except (TypeError, ValueError) as exc:
                text = str(exc)
                status = 409 if "active" in text else 400
                code = "job_active" if status == 409 else "invalid_request"
                self._error(status, code, text)

        def do_PUT(self) -> None:
            self._error(405, "method_not_allowed", "仅支持规定的 GET/POST 方法")

        do_DELETE = do_PUT
        do_OPTIONS = do_PUT

    return Handler


def serve(project_root: Path, port: int = 8782, open_browser: bool = True) -> int:
    try:
        server, _, manager = create_server(project_root, port=port, open_browser=open_browser)
    except OSError as exc:
        print(f"本地管理网页无法启动（端口 {port}）：{exc}", file=sys.stderr)
        return 1
    address = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"本地管理网页：{address}")
    print("按 Ctrl+C 停止服务；若作业正在运行，服务会先请求运行器停止。")
    if open_browser:
        webbrowser.open(address)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("收到 Ctrl+C，正在请求活动作业停止。", file=sys.stderr)
        manager.request_shutdown_stop()
        while manager.snapshot()["active"]:
            threading.Event().wait(0.1)
    finally:
        server.server_close()
    return 0
