from __future__ import annotations

import threading
import time
import json
import importlib.resources
import tomllib
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from sgbltx_helper.dashboard.jobs import JobManager
from sgbltx_helper.dashboard.registry import get_profile, list_profiles, list_tasks
from sgbltx_helper.dashboard.settings import SettingsStore
from sgbltx_helper.dashboard.evidence import list_runs, read_run, resolve_image
from sgbltx_helper.dashboard.server import create_server
from sgbltx_helper.cli import EXIT_OK, main


SOURCE_PROJECT = Path(__file__).resolve().parents[1]
PROJECT = SOURCE_PROJECT


@pytest.fixture(autouse=True)
def isolated_dashboard_profiles(tmp_path, monkeypatch):
    """Use synthetic profiles; never require or modify the user's local files."""
    project = tmp_path / "dashboard-project"
    config = project / "config"
    config.mkdir(parents=True)
    example = (SOURCE_PROJECT / "config" / "example.json").read_text(encoding="utf-8")
    for filename, profile_id, alias in (
        ("local.json", "local-main", "test_main"),
        ("local-test.json", "local-test-mail", "test_mail"),
    ):
        raw = json.loads(example)
        raw["profile"]["profile_id"] = profile_id
        raw["profile"]["account_alias"] = alias
        raw["profile"]["device"]["adb_path"] = "C:/test-only/adb.exe"
        raw["profile"]["device"]["serial"] = "test-device-not-real"
        (config / filename).write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "PROJECT", project)


def _wait_for(manager: JobManager, predicate, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = manager.snapshot()
        if predicate(snapshot):
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"job did not reach expected state: {manager.snapshot()}")


def test_registry_exposes_tasks_and_two_fixed_profiles(tmp_path):
    tasks = list_tasks()
    assert {task["task_id"] for task in tasks} == {"mail_bulk_claim", "claim_primary", "daily_rewards_claim", "fief_industry_collect"}
    assert next(task for task in tasks if task["task_id"] == "mail_bulk_claim")["enabled_by_default"] is True
    assert next(task for task in tasks if task["task_id"] == "claim_primary")["enabled_by_default"] is False
    assert next(task for task in tasks if task["task_id"] == "daily_rewards_claim")["enabled_by_default"] is False
    fief = next(task for task in tasks if task["task_id"] == "fief_industry_collect")
    assert fief["enabled_by_default"] is False
    assert "待首次实机采样" in fief["acceptance"]

    profiles = list_profiles(tmp_path)
    assert {profile["profile_id"] for profile in profiles} == {"local-main", "local-test-mail"}
    assert all(profile["available"] is False for profile in profiles)
    assert get_profile(tmp_path, "local-test-mail")["available"] is False


def test_settings_defaults_persist_and_reject_unregistered_tasks(tmp_path):
    path = tmp_path / "settings.json"
    settings = SettingsStore(path)
    assert settings.load()["modules"] == {"mail_bulk_claim": True, "claim_primary": False, "daily_rewards_claim": False, "fief_industry_collect": False}
    saved = settings.set_enabled("claim_primary", True)
    assert saved["modules"]["claim_primary"] is True
    assert SettingsStore(path).load()["modules"]["claim_primary"] is True
    legacy = tmp_path / "legacy-settings.json"
    legacy.write_text(json.dumps({"modules": {"mail_bulk_claim": True, "claim_primary": False}}), encoding="utf-8")
    assert SettingsStore(legacy).load()["modules"]["daily_rewards_claim"] is False
    assert not list(tmp_path.glob("*.tmp"))
    with pytest.raises(ValueError, match="unknown task"):
        settings.set_enabled("arbitrary_command", True)


def test_opening_manager_never_executes(tmp_path):
    calls = []
    settings = SettingsStore(tmp_path / "settings.json")
    manager = JobManager(tmp_path, settings, lambda *args: calls.append(args))
    manager.snapshot()
    assert calls == []


def test_disabled_module_cannot_start_before_profile_resolution(tmp_path):
    settings = SettingsStore(tmp_path / "settings.json")
    settings.set_enabled("claim_primary", False)
    manager = JobManager(tmp_path, settings, lambda *args: {})
    with pytest.raises(ValueError, match="disabled"):
        manager.start("request-1", "claim_primary", "missing-profile", "dry-run")


def test_fief_module_is_disabled_by_default_and_cannot_start(tmp_path):
    manager = JobManager(tmp_path, SettingsStore(tmp_path / "settings.json"), lambda *args: {})
    with pytest.raises(ValueError, match="disabled"):
        manager.start("request-fief", "fief_industry_collect", "local-test-mail", "dry-run")


def test_job_mode_and_registry_are_server_side_allowlisted(tmp_path):
    settings = SettingsStore(tmp_path / "settings.json")
    manager = JobManager(tmp_path, settings, lambda *args: {})
    with pytest.raises(ValueError, match="unknown task"):
        manager.start("r1", "arbitrary", "local-main", "dry-run")
    with pytest.raises(ValueError, match="mode"):
        manager.start("r2", "mail_bulk_claim", "local-test-mail", "shell")
    with pytest.raises(ValueError, match="profile"):
        manager.start("r3", "mail_bulk_claim", "arbitrary", "dry-run")


def test_request_id_is_idempotent_and_rejects_parameter_reuse(tmp_path):
    settings = SettingsStore(tmp_path / "settings.json")
    started = threading.Event()
    release = threading.Event()

    def executor(task_id, profile_id, mode, stop):
        started.set()
        release.wait(2)
        return {"status": "nothing_to_claim", "action_count": 0}

    manager = JobManager(PROJECT, settings, executor)
    first = manager.start("same-request", "mail_bulk_claim", "local-test-mail", "dry-run")
    assert started.wait(1)
    duplicate = manager.start("same-request", "mail_bulk_claim", "local-test-mail", "dry-run")
    assert duplicate["job_id"] == first["job_id"]
    with pytest.raises(ValueError, match="request_id"):
        manager.start("same-request", "mail_bulk_claim", "local-test-mail", "execute")
    release.set()
    _wait_for(manager, lambda s: s["job"] and s["job"]["status"] == "finished")


def test_only_one_active_job_and_stop_targets_its_controller(tmp_path):
    settings = SettingsStore(tmp_path / "settings.json")
    started = threading.Event()
    release = threading.Event()
    controllers = []

    def executor(task_id, profile_id, mode, stop):
        controllers.append(stop)
        started.set()
        release.wait(2)
        return {"status": "cancelled" if stop.should_stop() else "nothing_to_claim", "action_count": 0}

    manager = JobManager(PROJECT, settings, executor)
    first = manager.start("r1", "mail_bulk_claim", "local-test-mail", "dry-run")
    assert started.wait(1)
    with pytest.raises(ValueError, match="active"):
        manager.start("r2", "mail_bulk_claim", "local-test-mail", "dry-run")
    stopped = manager.request_stop(first["job_id"])
    assert stopped["status"] == "stop_requested"
    assert controllers[0].should_stop()
    release.set()
    final = _wait_for(manager, lambda s: s["job"] and s["job"]["status"] == "finished")
    assert final["job"]["result"]["status"] == "cancelled"


@pytest.mark.parametrize("task_id,nodes,final_event,expected_stage,expected_counts", [
    ("mail_bulk_claim", ["more", "mail"], ("bulk_read", "control_recognition_timeout"),
     "一键阅读按钮识别超时", {"navigation_action_count": 2, "claim_action_count": None,
                           "dismiss_action_count": None, "scroll_action_count": None,
                           "batch_round_count": 0, "confirmed_batch_round_count": 0, "uncertain_batch_round_count": 0,
                           "pending_batch_round_count": 0}),
    ("daily_rewards_claim", ["open_daily_tasks", "open_daily_rewards", "dismiss_known_announcement", "claim_daily_all", "dismiss_daily_reward"],
     ("close_daily_tasks", "input_submitted"), "正在退出日常任务页",
     {"navigation_action_count": 2, "claim_action_count": 1, "dismiss_action_count": 3, "scroll_action_count": None,
      "batch_round_count": 1, "confirmed_batch_round_count": 1, "uncertain_batch_round_count": 0,
      "pending_batch_round_count": 0}),
    ("daily_rewards_claim", [], ("dismiss_known_announcement", "input_submitted"),
     "正在跳过已识别的攻城令公告", {"navigation_action_count": None, "claim_action_count": None,
                                "dismiss_action_count": 1, "scroll_action_count": None,
                                "batch_round_count": 0, "confirmed_batch_round_count": 0, "uncertain_batch_round_count": 0,
                                "pending_batch_round_count": 0}),
])
def test_active_job_snapshot_tracks_real_event_stage_and_classified_input_counts(
    tmp_path, task_id, nodes, final_event, expected_stage, expected_counts,
):
    import shutil

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(PROJECT / "config" / "local-test.json", config_dir / "local-test.json")
    settings = SettingsStore(config_dir / "dashboard.json")
    started = threading.Event()
    release = threading.Event()

    def executor(task_id, profile_id, mode, stop):
        run_dir = tmp_path / "artifacts" / "runs" / "run-20261006T120000Z-654321"
        run_dir.mkdir(parents=True)
        events = [{"node": "start", "event": "run_started", "detail": {
            "profile": "profile=local-test-mail alias=test_mail", "dashboard_job_id": stop.dashboard_job_id,
        }}]
        for index, node in enumerate(nodes):
            events.append({"node": node, "event": "input_submitted", "detail": {}})
            if node == "claim_daily_all":
                events.append({"node": "daily_batch_button", "event": "daily_batch_round_rechecked",
                               "detail": {"round_index": index + 1, "state": "gray",
                                          "reward_popup_closed": True, "reward_content_verified": True,
                                          "round_result": "confirmed"}})
        events.append({"node": final_event[0], "event": final_event[1], "detail": {}})
        (run_dir / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        started.set()
        release.wait(2)
        return {"status": "needs_attention", "action_count": 2, "detail": {"navigation_action_count": 2, "claim_action_count": 0, "dismiss_action_count": 0}}

    manager = JobManager(tmp_path, settings, executor)
    settings.set_enabled(task_id, True)
    manager.start("r1", task_id, "local-test-mail", "execute")
    assert started.wait(1)
    active = manager.snapshot()["job"]
    assert active["run_id"] == "run-20261006T120000Z-654321"
    assert active["stage"] == expected_stage
    assert active["progress"] == {
        "action_count": len(nodes) + int(final_event[1] == "input_submitted"),
        **expected_counts,
        "action_upper_bound": 20,
    }
    release.set()
    _wait_for(manager, lambda s: not s["active"])


def test_active_progress_ignores_unrelated_same_profile_artifact(tmp_path):
    import shutil
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(PROJECT / "config" / "local-test.json", config_dir / "local-test.json")
    started = threading.Event()
    release = threading.Event()
    def executor(*args):
        run_dir = tmp_path / "artifacts" / "runs" / "run-20261006T120001Z-123456"
        run_dir.mkdir(parents=True)
        (run_dir / "events.jsonl").write_text(json.dumps({
            "node": "start", "event": "run_started", "detail": {"profile": "profile=local-test-mail alias=test_mail"},
        }) + "\n" + json.dumps({"node": "mail", "event": "input_submitted", "detail": {}}) + "\n", encoding="utf-8")
        started.set()
        release.wait(2)
        return {"status": "cancelled", "action_count": 0}
    manager = JobManager(tmp_path, SettingsStore(config_dir / "settings.json"), executor)
    try:
        manager.start("request-own", "mail_bulk_claim", "local-test-mail", "execute")
        assert started.wait(1)
        active = manager.snapshot()["job"]
        assert "run_id" not in active
        assert active["progress"]["action_count"] is None
    finally:
        release.set()


def test_progress_counts_preserve_unknown_zero_and_action_upper_bound(tmp_path):
    settings = SettingsStore(tmp_path / "settings.json")
    started = threading.Event()
    release = threading.Event()
    def executor(*args):
        started.set()
        release.wait(2)
        return {"status": "cancelled", "action_count": 0, "detail": {}}
    manager = JobManager(PROJECT, settings, executor)
    try:
        manager.start("request-counts", "mail_bulk_claim", "local-test-mail", "dry-run")
        assert started.wait(1)
        active = manager.snapshot()["job"]
        assert active["progress"] == {
            "action_count": None, "navigation_action_count": None,
            "claim_action_count": None, "dismiss_action_count": None,
            "scroll_action_count": None,
            "batch_round_count": None, "confirmed_batch_round_count": None,
            "uncertain_batch_round_count": None,
            "pending_batch_round_count": None,
            "action_upper_bound": 20,
        }
        release.set()
        finished = _wait_for(manager, lambda snapshot: not snapshot["active"])["job"]
        assert finished["progress"]["action_count"] == 0
        assert finished["progress"]["navigation_action_count"] is None
    finally:
        release.set()


@pytest.mark.parametrize("budget_field", ["timeout_seconds", "max_recoveries", "max_actions"])
def test_non_finite_json_budget_isolated_over_http_without_executor_call(tmp_path, budget_field):
    config = tmp_path / "config"
    config.mkdir()
    shutil.copy(PROJECT / "config" / "local-test.json", config / "local-test.json")
    bad_config = json.loads((PROJECT / "config" / "local.json").read_text(encoding="utf-8"))
    bad_config_text = json.dumps(bad_config, ensure_ascii=False)
    expected = bad_config["budget"][budget_field]
    bad_config_text = bad_config_text.replace(
        f'"{budget_field}": {expected}', f'"{budget_field}": 1e999', 1
    )
    (config / "local.json").write_text(bad_config_text, encoding="utf-8")
    calls = []
    server, token, manager = create_server(
        tmp_path, port=0, open_browser=False,
        executor=lambda *args: calls.append(args) or {"status": "completed", "action_count": 0},
        artifacts_root=tmp_path / "artifacts", settings_path=tmp_path / "dashboard.json",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, _, body = _request(base, "/api/state")
        assert status == 200
        state = json.loads(body)
        profiles = {profile["profile_id"]: profile for profile in state["profiles"]}
        assert profiles["local-main"]["available"] is False
        assert profiles["local-test-mail"]["available"] is True
        payload = {
            "request_id": "non-finite-budget",
            "task_id": "mail_bulk_claim",
            "profile_id": "local-main",
            "mode": "dry-run",
            "instance_id": state["instance_id"],
        }
        status, _, _ = _request(base, "/api/jobs", "POST", payload, token)
        assert status in {400, 409}
        assert calls == []
    finally:
        manager.request_shutdown_stop()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_executor_failure_releases_job_slot_and_is_visible(tmp_path):
    settings = SettingsStore(tmp_path / "settings.json")
    manager = JobManager(PROJECT, settings, lambda *args: (_ for _ in ()).throw(RuntimeError("backend broke")))
    started = manager.start("r1", "mail_bulk_claim", "local-test-mail", "dry-run")
    failed = _wait_for(manager, lambda s: s["job"] and s["job"]["status"] == "failed")
    assert failed["job"]["job_id"] == started["job_id"]
    assert "backend broke" in failed["job"]["error"]
    second = manager.start("r2", "mail_bulk_claim", "local-test-mail", "dry-run")
    assert second["job_id"] != started["job_id"]


def test_evidence_reads_existing_results_and_ignores_incomplete_event_tail(tmp_path):
    run_dir = tmp_path / "runs" / "run-20261006T120000Z-123456"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(json.dumps({"status": "nothing_to_claim", "action_count": 3}), encoding="utf-8")
    (run_dir / "before.png").write_bytes(b"png")
    (run_dir / "events.jsonl").write_text('{"phase":"navigate"}\n{"phase":', encoding="utf-8")
    assert list_runs(tmp_path)[0]["run_id"] == run_dir.name
    details = read_run(tmp_path, run_dir.name)
    assert details["result"]["status"] == "nothing_to_claim"
    assert details["events"] == [{"phase": "navigate"}]
    assert details["images"] == ["before.png"]
    assert resolve_image(tmp_path, run_dir.name, "before.png").read_bytes() == b"png"


def test_list_runs_skips_junction_to_outside_registered_runs_root(tmp_path):
    runs_root = tmp_path / "artifacts" / "runs"
    runs_root.mkdir(parents=True)
    good = runs_root / "run-20261006T120000Z-654321"
    good.mkdir()
    (good / "result.json").write_text(json.dumps({"status": "completed", "task_id": "claim_primary"}), encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "result.json").write_text(json.dumps({"status": "completed", "task_id": "secret"}), encoding="utf-8")
    junction = runs_root / "run-20261006T120002Z-654322"
    if sys.platform == "win32":
        created = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True, text=True)
        if created.returncode:
            pytest.skip(f"junction creation unavailable: {created.stderr}")
    else:
        junction.symlink_to(outside, target_is_directory=True)
    runs = list_runs(tmp_path / "artifacts")
    assert [run["run_id"] for run in runs] == [good.name]
    assert runs[0]["task_id"] == "claim_primary"
    with pytest.raises(ValueError, match="run not found"):
        read_run(tmp_path / "artifacts", junction.name)


def test_evidence_files_cannot_symlink_outside_a_safe_run_directory(tmp_path):
    runs_root = tmp_path / "artifacts" / "runs"
    run_dir = runs_root / "run-20261006T120003Z-654323"
    run_dir.mkdir(parents=True)
    outside = tmp_path / "outside-result.json"
    outside.write_text(json.dumps({"status": "completed", "task_id": "secret"}), encoding="utf-8")
    outside_image = tmp_path / "outside.png"
    outside_image.write_bytes(b"outside")
    result_link = run_dir / "result.json"
    image_link = run_dir / "before.png"
    if sys.platform == "win32":
        linked = subprocess.run(["cmd", "/c", "mklink", str(result_link), str(outside)], capture_output=True, text=True)
        if linked.returncode:
            pytest.skip("file symlink creation unavailable in this Windows environment")
        linked_image = subprocess.run(["cmd", "/c", "mklink", str(image_link), str(outside_image)], capture_output=True, text=True)
        if linked_image.returncode:
            pytest.skip("image symlink creation unavailable in this Windows environment")
    else:
        result_link.symlink_to(outside)
        image_link.symlink_to(outside_image)
    runs = list_runs(tmp_path / "artifacts")
    assert len(runs) == 1 and runs[0]["status"] == "evidence_error"
    assert runs[0]["images"] == []
    assert "secret" not in json.dumps(runs)
    details = read_run(tmp_path / "artifacts", run_dir.name)
    assert details["result"] is None
    assert any("escapes run directory" in error for error in details["evidence_errors"])
    with pytest.raises(ValueError, match="escapes run directory"):
        resolve_image(tmp_path / "artifacts", run_dir.name, "before.png")


@pytest.mark.parametrize("corruption", ["root_list", "device_list", "budget_list", "bad_json", "bad_encoding"])
def test_malformed_profile_is_reported_unavailable_without_hiding_other_profile(tmp_path, corruption):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    good = json.loads((PROJECT / "config" / "local-test.json").read_text(encoding="utf-8"))
    (config_dir / "local-test.json").write_text(json.dumps(good), encoding="utf-8")
    bad = json.loads((PROJECT / "config" / "local.json").read_text(encoding="utf-8"))
    if corruption == "root_list":
        bad = []
    elif corruption == "device_list":
        bad["profile"]["device"] = []
    elif corruption == "budget_list":
        bad["budget"] = []
    if corruption in {"root_list", "device_list", "budget_list"}:
        (config_dir / "local.json").write_text(json.dumps(bad), encoding="utf-8")
    elif corruption == "bad_json":
        (config_dir / "local.json").write_text("{", encoding="utf-8")
    else:
        (config_dir / "local.json").write_bytes(b"\xff\xfe\x00")
    profiles = {profile["profile_id"]: profile for profile in list_profiles(tmp_path)}
    assert profiles["local-main"]["available"] is False
    assert profiles["local-test-mail"]["available"] is True


@pytest.mark.parametrize("corruption", ["root_list", "device_list", "budget_list", "bad_json", "bad_encoding"])
def test_invalid_selected_profile_type_falls_back_and_state_endpoint_survives_bad_config(tmp_path, corruption):
    config = tmp_path / "config"
    config.mkdir()
    shutil.copy(PROJECT / "config" / "local-test.json", config / "local-test.json")
    shutil.copy(PROJECT / "config" / "local.json", config / "local.json")
    settings_file = tmp_path / "dashboard.json"
    settings_file.write_text(json.dumps({"modules": {"claim_primary": True}, "selected_profile_id": []}), encoding="utf-8")
    assert SettingsStore(settings_file).load()["selected_profile_id"] == "local-test-mail"
    # Keep the healthy profile available and make the other profile structurally invalid.
    bad = json.loads((config / "local.json").read_text(encoding="utf-8"))
    if corruption == "root_list":
        bad = []
    elif corruption == "device_list":
        bad["profile"]["device"] = []
    elif corruption == "budget_list":
        bad["budget"] = []
    if corruption in {"root_list", "device_list", "budget_list"}:
        (config / "local.json").write_text(json.dumps(bad), encoding="utf-8")
    elif corruption == "bad_json":
        (config / "local.json").write_text("{", encoding="utf-8")
    else:
        (config / "local.json").write_bytes(b"\xff\xfe\x00")
    server, thread, token, manager, calls, base = _start_test_server(tmp_path)
    server.dashboard_project_root = tmp_path
    server.dashboard_settings = SettingsStore(settings_file)
    try:
        status, _, body = _request(base, "/api/state")
        assert status == 200
        state = json.loads(body)
        available = {profile["profile_id"]: profile["available"] for profile in state["profiles"]}
        assert available == {"local-main": False, "local-test-mail": True}
        assert state["selected_profile_id"] == "local-test-mail"
        assert state["modules"]["claim_primary"]["enabled"] is True
        assert calls == []
    finally:
        manager.request_shutdown_stop()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_bad_profile_cannot_start_job(tmp_path):
    settings = SettingsStore(tmp_path / "settings.json")
    bad_root = tmp_path / "bad-project"
    (bad_root / "config").mkdir(parents=True)
    raw = json.loads((PROJECT / "config" / "local-test.json").read_text(encoding="utf-8"))
    raw["profile"]["device"] = []
    (bad_root / "config" / "local-test.json").write_text(json.dumps(raw), encoding="utf-8")
    manager = JobManager(bad_root, settings, lambda *args: pytest.fail("executor must not run"))
    with pytest.raises(ValueError, match="unavailable"):
        manager.start("bad-profile", "mail_bulk_claim", "local-test-mail", "dry-run")


@pytest.mark.parametrize("run_id,name", [("../outside", "before.png"), ("run-20261006T120000Z-123456", "..\\secret.txt"), ("run-20261006T120000Z-123456", "result.json")])
def test_evidence_rejects_path_traversal_and_non_image_names(tmp_path, run_id, name):
    with pytest.raises(ValueError):
        resolve_image(tmp_path, run_id, name)


def _start_test_server(tmp_path):
    calls = []
    server, token, manager = create_server(
        PROJECT,
        port=0,
        open_browser=False,
        executor=lambda *args: calls.append(args) or {"status": "nothing_to_claim", "action_count": 0},
        artifacts_root=tmp_path / "runs",
        settings_path=tmp_path / "dashboard.local.json",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    return server, thread, token, manager, calls, base


def _request(base, path, method="GET", payload=None, token=None, origin=None, host=None):
    headers = {}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["X-Dashboard-Token"] = token
    if origin is not None:
        headers["Origin"] = origin
    if host is not None:
        headers["Host"] = host
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=2) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def test_http_get_is_read_only_and_serves_fixed_static_page(tmp_path):
    server, thread, token, manager, calls, base = _start_test_server(tmp_path)
    try:
        status, _, body = _request(base, "/api/state")
        assert status == 200
        state = json.loads(body)
        assert state["device_status"] == "not_checked"
        assert state["modules"]["mail_bulk_claim"]["enabled"] is True
        assert state["modules"]["claim_primary"]["enabled"] is False
        assert state["modules"]["daily_rewards_claim"]["enabled"] is False
        assert state["active_job"] is None
        assert calls == []
        status, _, body = _request(base, "/")
        assert status == 200
        assert token.encode() in body
        assert b"dashboard-token" in body
        assert _request(base, "/app.css")[0] == 200
        status, _, script = _request(base, "/app.js")
        assert status == 200
        assert b"async function startTask" in script
        assert b"nothing_to_claim" in script
        assert _request(base, "/../../config/local-test.json")[0] == 404
    finally:
        manager.request_shutdown_stop()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_port_bind_failure_preserves_original_os_error_and_does_not_close_partial_server(tmp_path):
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    probe.listen(1)
    port = probe.getsockname()[1]
    try:
        with pytest.raises(OSError):
            create_server(PROJECT, port=port, open_browser=False, executor=lambda *args: {})
    finally:
        probe.close()


def test_http_mutations_require_token_same_origin_and_allow_only_registered_task(tmp_path):
    server, thread, token, manager, calls, base = _start_test_server(tmp_path)
    try:
        payload = {"task_id": "claim_primary", "enabled": True}
        status, _, body = _request(base, "/api/settings", "POST", payload)
        assert status == 403
        assert json.loads(body)["error"]["code"] == "token_required"

        status, _, body = _request(base, "/api/settings", "POST", payload, token, "https://evil.example")
        assert status == 403
        assert json.loads(body)["error"]["code"] == "origin_rejected"

        status, _, body = _request(base, "/api/settings", "POST", payload, token, host="evil.example")
        assert status == 403
        assert json.loads(body)["error"]["code"] == "host_rejected"

        status, _, body = _request(base, "/api/settings", "POST", {"task_id": "arbitrary", "enabled": True}, token)
        assert status == 400
        assert json.loads(body)["error"]["code"] == "invalid_request"
        status, _, body = _request(base, "/api/settings", "POST", {"task_id": [], "enabled": True}, token)
        assert status == 400
        assert json.loads(body)["error"]["code"] == "invalid_request"

        status, _, body = _request(base, "/api/settings", "POST", payload, token)
        assert status == 200
        assert json.loads(body)["modules"]["claim_primary"] is True
        assert calls == []
        status, _, body = _request(base, "/api/settings", "POST",
                                  {"task_id": "daily_rewards_claim", "enabled": True}, token)
        assert status == 200
        assert json.loads(body)["modules"]["daily_rewards_claim"] is True
        status, _, body = _request(base, "/api/state")
        assert status == 200
        assert json.loads(body)["active_job"] is None
        assert calls == [], "enabling the daily module must not execute it"
    finally:
        manager.request_shutdown_stop()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_http_job_submission_is_tokenized_idempotent_and_busy_rejects_overlap(tmp_path):
    server, thread, token, manager, calls, base = _start_test_server(tmp_path)
    release = threading.Event()
    started = threading.Event()

    def controlled_executor(*args):
        calls.append(args)
        started.set()
        release.wait(2)
        return {"status": "nothing_to_claim", "action_count": 0}

    manager.executor = controlled_executor
    state = json.loads(_request(base, "/api/state")[2])
    payload = {"request_id": "one", "task_id": "mail_bulk_claim", "profile_id": "local-test-mail", "mode": "dry-run", "instance_id": state["instance_id"]}
    try:
        status, _, _ = _request(base, "/api/jobs", "POST", payload, token)
        assert status == 202
        assert started.wait(1)
        status, _, body = _request(base, "/api/jobs", "POST", payload, token)
        assert status == 202
        duplicate_id = json.loads(body)["job"]["job_id"]
        status, _, body = _request(base, "/api/jobs", "POST", {**payload, "request_id": "two"}, token)
        assert status == 409
        assert json.loads(body)["error"]["code"] == "job_active"
        assert len(calls) == 1
        status, _, body = _request(base, f"/api/jobs/{duplicate_id}/stop", "POST", {}, token)
        assert status == 200
        assert manager.snapshot()["job"]["stop_requested"] is True
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_http_pending_request_from_previous_server_instance_never_reexecutes(tmp_path):
    import http.client

    calls = []
    settings_path = tmp_path / "dashboard.json"
    artifacts_root = tmp_path / "artifacts"

    def start_instance():
        server, token, manager = create_server(
            PROJECT, port=0, open_browser=False,
            executor=lambda *args: calls.append(args) or {"status": "nothing_to_claim", "action_count": 0},
            artifacts_root=artifacts_root, settings_path=settings_path,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, token, manager, thread, f"http://127.0.0.1:{server.server_address[1]}"

    first_server, first_token, first_manager, first_thread, first_base = start_instance()
    second_server = second_thread = second_manager = None
    try:
        status, _, body = _request(first_base, "/api/state")
        assert status == 200
        first_state = json.loads(body)
        payload = {
            "request_id": "persisted-browser-request",
            "task_id": "mail_bulk_claim",
            "profile_id": "local-test-mail",
            "mode": "dry-run",
            "instance_id": first_state["instance_id"],
        }

        # Send the real HTTP request, then drop the client connection without reading its response.
        body_bytes = json.dumps(payload).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", first_server.server_address[1], timeout=2)
        connection.putrequest("POST", "/api/jobs")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(len(body_bytes)))
        connection.putheader("X-Dashboard-Token", first_token)
        connection.endheaders(body_bytes)
        connection.close()
        _wait_for(first_manager, lambda snapshot: snapshot["job"] and snapshot["job"]["status"] == "finished")
        assert len(calls) == 1
    finally:
        first_manager.request_shutdown_stop()
        first_server.shutdown()
        first_server.server_close()
        first_thread.join(2)

    try:
        second_server, second_token, second_manager, second_thread, second_base = start_instance()
        status, _, body = _request(second_base, "/api/state")
        assert status == 200
        second_state = json.loads(body)
        assert second_state["instance_id"] != payload["instance_id"]
        status, _, body = _request(second_base, "/api/jobs", "POST", payload, second_token)
        assert status == 409
        assert json.loads(body)["error"]["code"] == "stale_instance"
        assert second_manager.snapshot()["job"] is None
        assert len(calls) == 1
    finally:
        if second_server is not None:
            second_manager.request_shutdown_stop()
            second_server.shutdown()
            second_server.server_close()
            second_thread.join(2)


def test_http_run_details_and_image_endpoint_use_safe_names(tmp_path):
    runs = tmp_path / "runs"
    directory = runs / "dryrun-20261006T123000Z-123456"
    directory.mkdir(parents=True)
    (directory / "inspection.json").write_text(json.dumps({"page_id": "mail_inbox", "claim_attempted": False}), encoding="utf-8")
    (directory / "after.png").write_bytes(b"image")
    (directory / "events.jsonl").write_text('{"phase":"inspect"}\n', encoding="utf-8")
    server, thread, token, manager, calls, base = _start_test_server(tmp_path)
    # Point the handler's configured evidence root to this fixture directory.
    server.dashboard_artifacts_root = runs
    try:
        status, _, body = _request(base, f"/api/runs/{directory.name}")
        assert status == 200
        details = json.loads(body)
        assert details["inspection"]["page_id"] == "mail_inbox"
        assert details["images"] == ["after.png"]
        status, headers, body = _request(base, f"/api/runs/{directory.name}/images/after.png")
        assert status == 200
        assert headers.get_content_type() == "image/png"
        assert body == b"image"
        assert _request(base, f"/api/runs/{directory.name}/images/../../secret.txt")[0] == 404
        assert _request(base, "/api/runs/../secret")[0] == 404
    finally:
        manager.request_shutdown_stop()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_dashboard_cli_routes_port_project_root_and_no_browser(monkeypatch, tmp_path):
    captured = []
    from sgbltx_helper.dashboard import server as dashboard_server

    monkeypatch.setattr(dashboard_server, "serve", lambda root, port, open_browser: captured.append((root, port, open_browser)) or EXIT_OK)
    result = main(["dashboard", "--project-root", str(tmp_path), "--port", "8766", "--no-browser"])
    assert result == EXIT_OK
    assert captured == [(tmp_path.resolve(), 8766, False)]


def test_dashboard_cli_default_port_is_outside_current_windows_exclusion():
    from sgbltx_helper.cli import build_parser
    args = build_parser().parse_args(["dashboard", "--no-browser"])
    assert args.port == 8782


def test_dashboard_static_files_are_declared_as_package_data():
    package = importlib.resources.files("sgbltx_helper.dashboard")
    assert package.joinpath("static", "index.html").is_file()
    config = tomllib.loads((SOURCE_PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "sgbltx_helper" in config.get("tool", {}).get("setuptools", {}).get("package-data", {})
    assert any("dashboard/static" in item for item in config["tool"]["setuptools"]["package-data"]["sgbltx_helper"])


def test_dashboard_actual_app_js_submission_and_rendering_regressions():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the actual app.js VM regression harness")
    script = SOURCE_PROJECT / "tests" / "dashboard_app_probe.cjs"
    completed = subprocess.run([node, str(script), str(SOURCE_PROJECT / "src" / "sgbltx_helper" / "dashboard" / "static" / "app.js")], capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert '"rapidSubmissionCount":1' in completed.stdout
    assert '"retryRequestIdsSame":true' in completed.stdout
    assert '"progressShowsUnknown":true' in completed.stdout


def test_one_click_launcher_uses_its_own_directory_and_keeps_errors_visible():
    launcher = (SOURCE_PROJECT / "start-dashboard.cmd").read_text(encoding="utf-8")
    assert 'cd /d "%~dp0"' in launcher
    assert '".venv\\Scripts\\python.exe" -m sgbltx_helper dashboard' in launcher
    assert "pause" in launcher
