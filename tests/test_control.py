"""A6/A7/A9：输入隔离、停止、进程互斥、CLI 契约。

其中并发互斥测试使用**真实子进程**，而不是在同进程里模拟，
以确保操作系统级文件锁真的生效。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import textwrap
import time
from pathlib import Path

import pytest

import conftest
from conftest import FakeBackend, snapshot_real_runs
from sgbltx_helper.contracts import ConcurrentRunError, ReasonCode, RunMode, RunStatus
from sgbltx_helper.locking import RunLock, StopUnconfirmedError, lock_file_name
from sgbltx_helper.maa_adapter import MaaBackend, MaaError, MaaStopUnconfirmed, MaaStopped, ReadOnlyBackend, TaskRunSummary
from sgbltx_helper.runner import StopController, run
from test_runner import IDENTITY, CLAIM, make_request

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------- 干运行输入隔离
class TestReadOnlyIsolation:
    def test_readonly_backend_blocks_click(self) -> None:
        inner = FakeBackend(script=[{}])
        guard = ReadOnlyBackend(inner)
        with pytest.raises(AssertionError, match="干运行禁止调用输入方法"):
            guard.click(1, 2)
        assert guard.blocked_calls == ["click"]

    def test_readonly_backend_blocks_swipe(self) -> None:
        guard = ReadOnlyBackend(FakeBackend())
        with pytest.raises(AssertionError, match="干运行禁止调用输入方法"):
            guard.swipe(100, 800, 100, 400, 500)
        assert guard.blocked_calls == ["swipe"]

    def test_readonly_backend_blocks_run_task(self) -> None:
        inner = FakeBackend(script=[{}])
        guard = ReadOnlyBackend(inner)
        with pytest.raises(AssertionError, match="干运行禁止调用输入方法"):
            guard.run_task("ClaimPrimary", 0.0, lambda: False)
        assert inner.input_calls == []

    def test_readonly_backend_blocks_start_app(self) -> None:
        guard = ReadOnlyBackend(FakeBackend())
        with pytest.raises(AssertionError, match="干运行禁止调用输入方法"):
            guard.start_app("com.sgbltx.goodgame")

    def test_readonly_backend_allows_reads(self) -> None:
        inner = FakeBackend(script=[{IDENTITY: True}])
        guard = ReadOnlyBackend(inner)
        guard.connect()
        image = guard.screencap()
        outcome = guard.recognize(image, IDENTITY)
        assert outcome.hit is True
        assert guard.raw_resolution() == (1080, 1920)

    def test_dry_run_never_calls_input_even_with_claimable_present(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}])
        payload = run(
            make_request(project.root, RunMode.DRY_RUN),
            backend,
            project.root / "artifacts" / "locks",
            project.root,
            StopController(),
        )
        assert backend.input_calls == []
        assert getattr(payload, "claim_attempted", None) is False

    def test_dry_run_wires_the_readonly_guard(self, project) -> None:  # type: ignore[no-untyped-def]
        """结构性保证：干运行必须真的套上只读护栏，而不只是「碰巧没点」。

        这条边界测试用来堵住「把护栏拆掉但当前流程恰好不触发输入」这种回归。
        """
        backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}])
        payload = run(
            make_request(project.root, RunMode.DRY_RUN),
            backend,
            project.root / "artifacts" / "locks",
            project.root,
            StopController(),
        )
        events = _read_events(project.root, payload.run_id)
        started = [e for e in events if e["event"] == "run_started"]
        assert started, "缺少 run_started 事件"
        assert started[0]["detail"]["input_guard"] == "ReadOnlyBackend"
        assert started[0]["detail"]["input_guard_active"] is True

    def test_execute_does_not_use_the_readonly_guard(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(
            script=[{IDENTITY: True, CLAIM: True}, {IDENTITY: True, "mark_claimed.png": True}]
        )
        payload = run(
            make_request(project.root, RunMode.EXECUTE),
            backend,
            project.root / "artifacts" / "locks",
            project.root,
            StopController(),
        )
        events = _read_events(project.root, payload.run_id)
        started = [e for e in events if e["event"] == "run_started"]
        assert started[0]["detail"]["input_guard_active"] is False


class TestPipelineResourceBoundary:
    def test_sdk_loaded_default_repeat_is_rejected_before_post_task(self, tmp_path: Path) -> None:
        """用真实 Maa Resource 加载默认 repeat 配置；不能把任务提交给 Tasker。"""
        from PIL import Image

        resource = tmp_path / "resource"
        pipeline_dir = resource / "pipeline"
        image_dir = resource / "image"
        pipeline_dir.mkdir(parents=True)
        image_dir.mkdir()
        Image.new("RGB", (16, 16), "white").save(image_dir / "page_identity.png")
        (pipeline_dir / "claim_primary.json").write_text(
            json.dumps({"ClaimPrimary": {
                "recognition": "TemplateMatch", "template": "page_identity.png", "action": "Click"
            }}), encoding="utf-8"
        )
        (resource / "default_pipeline.json").write_text(
            json.dumps({"Default": {"repeat": 31}}), encoding="utf-8"
        )

        backend = MaaBackend(adb_path="", address="", screenshot_raw_size=False)
        try:
            backend.load_resource(resource, task_id="claim_primary")
            assert backend.resource is not None and backend.resource.loaded
            post_calls: list[str] = []
            backend.tasker.post_task = lambda entry: post_calls.append(entry)  # type: ignore[method-assign]
            with pytest.raises(MaaError, match="default_pipeline"):
                backend.run_task("ClaimPrimary", time.monotonic() + 2, lambda: False)
            assert post_calls == []
        finally:
            backend.close()

    @pytest.mark.parametrize("relative_path", ["pipeline/extra.jsonc", "pipeline/nested/extra.jsonc"])
    def test_sdk_loaded_extra_jsonc_is_rejected_before_post_task(
        self, tmp_path: Path, relative_path: str,
    ) -> None:
        """MaaFw accepts JSONC pipeline resources, so reject them before task submission."""
        from PIL import Image

        resource = tmp_path / "resource"
        pipeline_dir = resource / "pipeline"
        image_dir = resource / "image"
        pipeline_dir.mkdir(parents=True)
        image_dir.mkdir()
        Image.new("RGB", (16, 16), "white").save(image_dir / "page_identity.png")
        (pipeline_dir / "claim_primary.json").write_text(
            json.dumps({"ClaimPrimary": {
                "recognition": "TemplateMatch", "template": "page_identity.png", "action": "Click"
            }}), encoding="utf-8"
        )
        extra = resource / relative_path
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text('{"Extra": {"repeat": 31}}', encoding="utf-8")

        backend = MaaBackend(adb_path="", address="", screenshot_raw_size=False)
        try:
            backend.load_resource(resource, task_id="claim_primary")
            assert backend.resource is not None and backend.resource.loaded
            post_calls: list[str] = []

            class CompletedJob:
                done = True
                succeeded = True
                job_id = 1

                @staticmethod
                def get() -> None:
                    return None

            def post_task(entry: str) -> CompletedJob:
                post_calls.append(entry)
                return CompletedJob()

            backend.tasker.post_task = post_task  # type: ignore[method-assign]
            with pytest.raises(MaaError, match="extra|nested|Pipeline"):
                backend.run_task("ClaimPrimary", time.monotonic() + 2, lambda: False)
            assert post_calls == []
        finally:
            backend.close()


class TestMaaStopConfirmation:
    @staticmethod
    def _stop_after_task_submission() -> object:
        calls = {"count": 0}

        def should_stop() -> bool:
            calls["count"] += 1
            return calls["count"] >= 2

        return should_stop

    def _backend(self, mode: str, root: Path) -> tuple[MaaBackend, dict[str, object]]:
        state: dict[str, object] = {"task_done": False, "running": True, "post_stop_calls": 0}

        class Job:
            job_id = 17

            def __init__(self, *, done: bool, succeeded: bool, detail=None, task_job: bool = False) -> None:  # type: ignore[no-untyped-def]
                self._done = done
                self.succeeded = succeeded
                self._detail = detail
                self._task_job = task_job

            @property
            def done(self) -> bool:
                return self._done or (self._task_job and bool(state["task_done"]))

            def get(self):  # type: ignore[no-untyped-def]
                return self._detail

        class Tasker:
            @property
            def running(self) -> bool:
                return bool(state["running"])

            def post_task(self, entry: str) -> Job:
                return Job(done=False, succeeded=False, detail=None, task_job=True)

            def post_stop(self) -> Job:
                state["post_stop_calls"] = int(state["post_stop_calls"]) + 1
                if mode == "request_error":
                    raise RuntimeError("post_stop failed")
                if mode in {"confirmed", "stop_job_failed"}:
                    state["task_done"] = True
                    state["running"] = False
                return Job(
                    done=(mode != "stop_job_pending"),
                    succeeded=(mode != "stop_job_failed"),
                )

        backend = MaaBackend(adb_path="", address="", screenshot_raw_size=False)
        backend.tasker = Tasker()
        backend._loaded = True
        backend._resource_root = root
        backend._task_id = "claim_primary"
        return backend, state

    @staticmethod
    def _resource(root: Path) -> None:
        (root / "pipeline").mkdir(parents=True)
        (root / "pipeline" / "claim_primary.json").write_text(
            '{"ClaimPrimary":{"action":"DoNothing"}}', encoding="utf-8"
        )

    def test_user_stop_returns_cancelled_only_after_stop_and_task_jobs_finish(self, tmp_path: Path) -> None:
        self._resource(tmp_path)
        backend, state = self._backend("confirmed", tmp_path)
        summary = backend.run_task("ClaimPrimary", time.monotonic() + 1, self._stop_after_task_submission())  # type: ignore[arg-type]
        assert summary.stopped is True
        assert state["task_done"] is True
        assert state["running"] is False
        assert state["post_stop_calls"] == 1

    def test_post_stop_request_failure_is_unconfirmed(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import sgbltx_helper.maa_adapter as maa_adapter

        monkeypatch.setattr(maa_adapter, "STOP_CONFIRMATION_SECONDS", 0.06, raising=False)
        self._resource(tmp_path)
        backend, state = self._backend("request_error", tmp_path)
        with pytest.raises(MaaStopUnconfirmed, match="post_stop failed"):
            backend.run_task("ClaimPrimary", time.monotonic() + 1, self._stop_after_task_submission())  # type: ignore[arg-type]
        assert state["task_done"] is False

    def test_stop_job_completion_without_task_completion_is_unconfirmed(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import sgbltx_helper.maa_adapter as maa_adapter

        monkeypatch.setattr(maa_adapter, "STOP_CONFIRMATION_SECONDS", 0.06, raising=False)
        self._resource(tmp_path)
        backend, state = self._backend("task_pending", tmp_path)
        with pytest.raises(MaaStopUnconfirmed, match="task job"):
            backend.run_task("ClaimPrimary", time.monotonic() + 1, self._stop_after_task_submission())  # type: ignore[arg-type]
        assert state["task_done"] is False
        assert state["post_stop_calls"] == 1

    def test_failed_stop_job_is_not_accepted_when_task_job_finished(self, tmp_path: Path) -> None:
        self._resource(tmp_path)
        backend, state = self._backend("stop_job_failed", tmp_path)
        with pytest.raises(MaaStopUnconfirmed, match="stop job failed"):
            backend.run_task("ClaimPrimary", time.monotonic() + 1, self._stop_after_task_submission())  # type: ignore[arg-type]
        assert state["task_done"] is True

    def test_controller_click_waits_for_its_own_job_after_stop(self) -> None:
        class ClickJob:
            def __init__(self, done_at: float) -> None:
                self.done_at = done_at

            @property
            def done(self) -> bool:
                return time.monotonic() >= self.done_at

            @property
            def succeeded(self) -> bool:
                return self.done

        class Controller:
            def __init__(self, job: ClickJob) -> None:
                self.job = job
                self.posted: list[tuple[int, int]] = []

            def post_click(self, x: int, y: int) -> ClickJob:
                self.posted.append((x, y))
                return self.job

        job = ClickJob(time.monotonic() + 0.08)
        controller = Controller(job)
        backend = MaaBackend(adb_path="", address="", screenshot_raw_size=False)
        backend.controller = controller  # type: ignore[assignment]
        stopped = threading.Event()
        timer = threading.Timer(0.01, stopped.set)
        timer.start()
        with pytest.raises(MaaStopped):
            backend.click(30, 40, time.monotonic() + 2, stopped.is_set)
        timer.join()
        assert controller.posted == [(30, 40)]
        assert job.done is True
        assert backend.controller is controller

    def test_controller_click_unconfirmed_job_raises_stop_unconfirmed(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import sgbltx_helper.maa_adapter as maa_adapter

        monkeypatch.setattr(maa_adapter, "STOP_CONFIRMATION_SECONDS", 0.06, raising=False)

        class PendingJob:
            @property
            def done(self) -> bool:
                return False

            @property
            def succeeded(self) -> bool:
                return False

        class Controller:
            def __init__(self) -> None:
                self.job = PendingJob()
                self.posted = 0

            def post_click(self, x: int, y: int) -> PendingJob:
                self.posted += 1
                return self.job

        controller = Controller()
        backend = MaaBackend(adb_path="", address="", screenshot_raw_size=False)
        backend.controller = controller  # type: ignore[assignment]
        stopped = threading.Event()
        timer = threading.Timer(0.01, stopped.set)
        timer.start()
        with pytest.raises(MaaStopUnconfirmed, match="Controller click job"):
            backend.click(30, 40, time.monotonic() + 2, stopped.is_set)
        timer.join()
        assert controller.posted == 1
        assert controller.job.done is False
        assert backend.controller is controller

    def test_controller_swipe_uses_installed_job_api_and_records_gesture(self) -> None:
        class SwipeJob:
            done = True
            succeeded = True

        class Controller:
            def __init__(self) -> None:
                self.gesture = None

            def post_swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int) -> SwipeJob:
                self.gesture = (x1, y1, x2, y2, duration)
                return SwipeJob()

        controller = Controller()
        backend = MaaBackend(adb_path="", address="", screenshot_raw_size=False)
        backend.controller = controller  # type: ignore[assignment]
        assert backend.swipe(540, 1500, 540, 700, 500, time.monotonic() + 2, lambda: False)
        assert controller.gesture == (540, 1500, 540, 700, 500)

    def test_controller_swipe_unconfirmed_after_stop_raises_without_faking_completion(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import sgbltx_helper.maa_adapter as maa_adapter

        monkeypatch.setattr(maa_adapter, "STOP_CONFIRMATION_SECONDS", 0.06, raising=False)

        class PendingJob:
            done = False
            succeeded = False

        class Controller:
            def __init__(self) -> None:
                self.job = PendingJob()
                self.posted = 0

            def post_swipe(self, *args) -> PendingJob:
                self.posted += 1
                return self.job

        controller = Controller()
        backend = MaaBackend(adb_path="", address="", screenshot_raw_size=False)
        backend.controller = controller  # type: ignore[assignment]
        stopped = threading.Event()
        timer = threading.Timer(0.01, stopped.set)
        timer.start()
        with pytest.raises(MaaStopUnconfirmed, match="Controller swipe job"):
            backend.swipe(540, 1500, 540, 700, 500, time.monotonic() + 2, stopped.is_set)
        timer.join()
        assert controller.posted == 1
        assert controller.job.done is False


def _read_events(root: Path, run_id: str) -> list[dict]:
    path = root / "artifacts" / "runs" / run_id / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ------------------------------------------------------- 进程互斥（真实子进程）
class TestProcessMutex:
    def test_stop_marker_written_during_lock_acquire_blocks_second_run_and_releases_lock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        lock_dir = tmp_path / "locks"
        holder = RunLock(lock_dir, "emulator-5554", "main")
        holder.acquire("first-run")
        reached_native_lock = threading.Event()
        continue_native_lock = threading.Event()
        original_try_lock = RunLock._try_lock

        def pause_before_native_lock(lock: RunLock, fh: object) -> bool:
            if lock is waiting:
                reached_native_lock.set()
                assert continue_native_lock.wait(timeout=5)
            return original_try_lock(lock, fh)

        monkeypatch.setattr(RunLock, "_try_lock", pause_before_native_lock)
        waiting = RunLock(lock_dir, "emulator-5554", "alt")
        outcome: list[BaseException | str] = []

        def acquire_waiting() -> None:
            try:
                waiting.acquire("second-run")
            except BaseException as exc:  # captured for assertion on test thread
                outcome.append(exc)
            else:
                outcome.append("acquired")

        thread = threading.Thread(target=acquire_waiting)
        thread.start()
        try:
            assert reached_native_lock.wait(timeout=5)
            holder.mark_stop_unconfirmed("first-run", "stop not confirmed")
            holder.release()
            continue_native_lock.set()
            thread.join(timeout=5)
            assert not thread.is_alive()
            assert len(outcome) == 1 and isinstance(outcome[0], StopUnconfirmedError)
            assert waiting.acquired is False
            assert waiting._fh is None

            RunLock.clear_stop_unconfirmed(lock_dir, "emulator-5554")
            recovered = RunLock(lock_dir, "emulator-5554", "main")
            recovered.acquire("recovery-lock-check")
            recovered.release()
        finally:
            continue_native_lock.set()
            if thread.is_alive():
                thread.join(timeout=5)
            holder.release()
            waiting.release()

    def test_second_real_process_is_rejected(self, tmp_path: Path) -> None:
        """父进程持锁，真实子进程尝试加锁必须失败。"""
        lock_dir = tmp_path / "locks"
        holder = RunLock(lock_dir, "emulator-5554", "main")
        holder.acquire("parent-run")
        try:
            script = textwrap.dedent(
                """
                import sys
                from pathlib import Path
                from sgbltx_helper.locking import RunLock
                from sgbltx_helper.contracts import ConcurrentRunError

                lock = RunLock(Path(sys.argv[1]), "emulator-5554", "main")
                try:
                    lock.acquire("child-run")
                except ConcurrentRunError:
                    print("REJECTED")
                    sys.exit(0)
                lock.release()
                print("ACQUIRED")
                sys.exit(1)
                """
            )
            proc = subprocess.run(
                [sys.executable, "-c", script, str(lock_dir)],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(PROJECT_ROOT),
            )
            assert proc.returncode == 0, f"子进程应被拒绝：{proc.stdout} {proc.stderr}"
            assert "REJECTED" in proc.stdout
        finally:
            holder.release()

    def test_lock_released_after_release_allows_reacquire(self, tmp_path: Path) -> None:
        lock_dir = tmp_path / "locks"
        first = RunLock(lock_dir, "emulator-5554", "main")
        first.acquire("run-1")
        first.release()
        second = RunLock(lock_dir, "emulator-5554", "main")
        second.acquire("run-2")
        second.release()

    def test_same_device_different_aliases_are_mutually_exclusive(self, tmp_path: Path) -> None:
        lock_dir = tmp_path / "locks"
        a = RunLock(lock_dir, "emulator-5554", "main")
        b = RunLock(lock_dir, "emulator-5554", "alt")
        a.acquire("run-a")
        try:
            with pytest.raises(ConcurrentRunError):
                b.acquire("run-b")
        finally:
            a.release()

    def test_different_devices_can_run_in_parallel(self, tmp_path: Path) -> None:
        lock_dir = tmp_path / "locks"
        a = RunLock(lock_dir, "emulator-5554", "main")
        b = RunLock(lock_dir, "emulator-5556", "alt")
        a.acquire("run-a")
        try:
            b.acquire("run-b")
            b.release()
        finally:
            a.release()

    def test_lock_file_name_is_stable_and_filesystem_safe(self) -> None:
        name = lock_file_name("emulator-5554", "main")
        assert name == lock_file_name("emulator-5554", "main")
        assert name.endswith(".lock")
        assert "/" not in name and ":" not in name and "\\" not in name

    def test_crashed_process_does_not_leave_stale_lock(self, tmp_path: Path) -> None:
        """子进程持锁后被强杀，父进程应能重新加锁（内核释放锁）。

        实测：Windows 上进程终止到文件锁释放有约 60ms 的窗口，因此这里做**有界**
        轮询（最多 5 秒），而不是假设「立刻」可用。产品行为是 fail-closed：
        窗口期内会被拒绝并打印持有者信息。
        """
        lock_dir = tmp_path / "locks"
        script = textwrap.dedent(
            """
            import sys, time
            from pathlib import Path
            from sgbltx_helper.locking import RunLock
            lock = RunLock(Path(sys.argv[1]), "emulator-5554", "main")
            lock.acquire("child-run")
            print("LOCKED", flush=True)
            time.sleep(60)
            """
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script, str(lock_dir)],
            stdout=subprocess.PIPE,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        try:
            assert proc.stdout is not None
            assert proc.stdout.readline().strip() == "LOCKED"

            blocked = RunLock(lock_dir, "emulator-5554", "main")
            with pytest.raises(ConcurrentRunError):
                blocked.acquire("parent-run")

            proc.kill()
            proc.wait(timeout=30)

            deadline = time.monotonic() + 5.0
            acquired = False
            while time.monotonic() < deadline:
                recovered = RunLock(lock_dir, "emulator-5554", "main")
                try:
                    recovered.acquire("parent-run-2")
                except ConcurrentRunError:
                    time.sleep(0.05)
                    continue
                recovered.release()
                acquired = True
                break
            assert acquired, "进程被强杀后 5 秒内仍未释放锁，说明存在陈旧锁"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=30)


# ------------------------------------------------------- CLI 契约
class TestCliContract:
    def _run_cli(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        # cwd 即 CLI 默认项目根；必须是临时目录，否则 runner 的保留期清理会删除真实 artifacts/runs
        return subprocess.run(
            [sys.executable, "-m", "sgbltx_helper", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd),
            timeout=120,
        )

    def test_run_without_mode_is_refused(self, tmp_path: Path) -> None:
        profile = tmp_path / "local.json"
        profile.write_text(json.dumps(_minimal_config()), encoding="utf-8")
        proc = self._run_cli(["run", "--profile", str(profile)], tmp_path)
        assert proc.returncode == 4
        assert "--dry-run" in proc.stderr or "--execute" in proc.stderr

    def test_dry_run_and_execute_together_refused(self, tmp_path: Path) -> None:
        profile = tmp_path / "local.json"
        profile.write_text(json.dumps(_minimal_config()), encoding="utf-8")
        proc = self._run_cli(
            ["run", "--profile", str(profile), "--dry-run", "--execute"], tmp_path
        )
        assert proc.returncode == 4

    def test_missing_profile_is_usage_error(self, tmp_path: Path) -> None:
        proc = self._run_cli(["run", "--profile", str(tmp_path / "nope.json"), "--dry-run"], tmp_path)
        assert proc.returncode == 4
        assert "不存在" in proc.stderr

    def test_empty_serial_is_refused(self, tmp_path: Path) -> None:
        """档案未指定设备时必须拒绝，且不发出任何输入。"""
        cfg = _minimal_config()
        cfg["profile"]["device"]["serial"] = ""
        profile = tmp_path / "local.json"
        profile.write_text(json.dumps(cfg), encoding="utf-8")
        real_runs_before = snapshot_real_runs()
        proc = self._run_cli(["run", "--profile", str(profile), "--dry-run"], tmp_path)
        assert proc.returncode == 2
        # 有设备时应报 device_not_specified；无设备时 no_device 同样合格（都拒绝执行）
        assert "device_not_specified" in proc.stdout or "no_device" in proc.stdout
        # 运行工件只能落在临时项目根；真实 runs 不得新增或被保留期清理
        assert snapshot_real_runs() == real_runs_before
        assert any((tmp_path / "artifacts" / "runs").iterdir())

    def test_devices_command_lists_without_selecting(self, tmp_path: Path) -> None:
        proc = self._run_cli(["devices"], tmp_path)
        assert proc.returncode == 0
        assert "不会默认选择第一个设备" in proc.stdout


def _minimal_config() -> dict:
    return {
        "schema_version": 1,
        "task_id": "claim_primary",
        "profile": {
            "profile_id": "test",
            "account_alias": "main",
            "device": {
                "adb_path": "C:/leidian/LDPlayer14/adb.exe",
                "serial": "emulator-5554",
                "package_name": "com.sgbltx.goodgame",
                "expected_resolution": [1080, 1920],
            },
        },
        "budget": {"timeout_seconds": 120, "max_recoveries": 2, "max_actions": 20},
        "resource": {"root": "resource"},
        "artifacts": {"root": "artifacts", "retention_days": 7},
    }
