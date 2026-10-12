"""命令行入口（设计说明第 7 节的交付契约）。

    python -m sgbltx_helper devices
    python -m sgbltx_helper inspect --profile config/local.json
    python -m sgbltx_helper run --profile config/local.json --task claim_primary --dry-run
    python -m sgbltx_helper run --profile config/local.json --task claim_primary --execute

没有显式选择运行模式时拒绝执行，绝不默认 execute。
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .contracts import (
    ContractError,
    InspectionResult,
    LoadedConfig,
    ReasonCode,
    RunMode,
    RunStatus,
    TaskResult,
    load_config,
)
from .device import discover_devices
from .maa_adapter import MaaBackend
from .runner import StopController, run

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NEEDS_ATTENTION = 2
EXIT_CANCELLED = 3
EXIT_USAGE = 4

STATUS_EXIT = {
    RunStatus.COMPLETED: EXIT_OK,
    RunStatus.NOTHING_TO_CLAIM: EXIT_OK,
    RunStatus.FAILED: EXIT_FAILED,
    RunStatus.NEEDS_ATTENTION: EXIT_NEEDS_ATTENTION,
    RunStatus.CANCELLED: EXIT_CANCELLED,
}


def _project_root(explicit: str | None) -> Path:
    return Path(explicit).resolve() if explicit else Path.cwd().resolve()


def _read_profile(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise ContractError(f"配置文件不存在：{p}。请先复制 config/example.json 为 config/local.json 并填写设备序列号。")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"配置文件不是合法 JSON：{p}: {exc}") from exc
    return raw


def _make_backend(cfg: LoadedConfig, project_root: Path) -> MaaBackend:
    prof = cfg.request.profile
    raw = cfg.raw
    maa_cfg = raw.get("maa") or {}
    log_dir = Path(project_root) / cfg.request.artifacts_root / "maa_log"
    return MaaBackend(
        adb_path=prof.adb_path,
        address=prof.serial,
        screencap_methods=maa_cfg.get("screencap_methods"),
        input_methods=maa_cfg.get("input_methods"),
        log_dir=log_dir,
        stdout_level=maa_cfg.get("stdout_level"),
        screenshot_raw_size=bool(maa_cfg.get("screenshot_raw_size", True)),
    )


def _print_summary(payload: TaskResult | InspectionResult) -> None:
    if isinstance(payload, TaskResult):
        print("---- 运行结果 (TaskResult) ----")
        print(f"run_id        : {payload.run_id}")
        print(f"task_id       : {payload.task_id}")
        print(f"account_alias : {payload.account_alias}")
        print(f"device_serial : {payload.device_serial}")
        print(f"mode          : {payload.mode}")
        print(f"started_at    : {payload.started_at}")
        print(f"finished_at   : {payload.finished_at}")
        print(f"action_count  : {payload.action_count}")
        print(f"status        : {payload.status}")
        print(f"reason_code   : {payload.reason_code}")
        if payload.detail.get("message"):
            print(f"message       : {payload.detail['message']}")
        print("evidence      :")
        for path in payload.evidence_paths:
            print(f"  - {path}")
    else:
        print("---- 干运行结果 (InspectionResult，未领取任何奖励) ----")
        print(f"run_id        : {payload.run_id}")
        print(f"task_id       : {payload.task_id}")
        print(f"account_alias : {payload.account_alias}")
        print(f"device_serial : {payload.device_serial}")
        print(f"observed_at   : {payload.observed_at}")
        print(f"page_id       : {payload.page_id}")
        print(f"claim_attempted: {payload.claim_attempted}")
        print("observations  :")
        for obs in payload.observations:
            flag = "HIT " if obs.hit else "miss"
            score = f"{obs.score:.3f}" if obs.score is not None else "n/a"
            print(f"  [{flag}] {obs.name}  score={score}  box={obs.box}")
        if payload.candidate_actions:
            print("candidate_actions:")
            for act in payload.candidate_actions:
                blocked = f"  ← {act.blocked_reason}" if act.blocked_reason else ""
                print(f"  - {act.action_id} (verified={act.verified}) {act.description}{blocked}")
        if payload.blockers:
            print("blockers      :")
            for b in payload.blockers:
                print(f"  ! {b}")
        for note in payload.notes:
            print(f"note          : {note}")
        print("evidence      :")
        for path in payload.evidence_paths:
            print(f"  - {path}")


# ----------------------------------------------------------------- 子命令
def cmd_devices(args: argparse.Namespace) -> int:
    devices = discover_devices(args.adb)
    print(f"发现 {len(devices)} 个 ADB 设备：")
    for d in devices:
        print(f"  - {d.describe()}")
        print(f"      screencap_methods={d.screencap_methods} input_methods={d.input_methods}")
    if not devices:
        print("（没有可用设备。请确认模拟器已启动、ADB 调试已开启；本命令只列出，不会自动选择设备。）")
    print("提示：本项目不会默认选择第一个设备，请把目标设备的地址填入 config/local.json 的 profile.device.serial。")
    return EXIT_OK


def cmd_inspect(args: argparse.Namespace) -> int:
    project_root = _project_root(args.project_root)
    cfg = load_config(_read_profile(args.profile), RunMode.DRY_RUN)
    if args.project_root is None:
        pass  # 默认使用当前目录
    backend = _make_backend(cfg, project_root)
    lock_dir = project_root / cfg.request.artifacts_root / "locks"
    stop = StopController()
    _install_sigint(stop)
    payload = run(
        cfg.request, backend, lock_dir, project_root, stop=stop, include_candidates=False
    )
    _print_summary(payload)
    if isinstance(payload, TaskResult):
        # inspect 是只读命令；连接/配置失败时同样以 TaskResult 汇报，便于统一审查
        return STATUS_EXIT.get(payload.status, EXIT_FAILED)
    if payload.blockers:
        return EXIT_NEEDS_ATTENTION
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    if args.dry_run and args.execute:
        print("错误：--dry-run 与 --execute 不能同时使用。", file=sys.stderr)
        return EXIT_USAGE
    if not args.dry_run and not args.execute:
        print(
            "错误：必须显式选择运行模式：--dry-run（只读分析）或 --execute（真实执行）。\n"
            "本项目不会默认 execute，以免在未确认的情况下发出输入。",
            file=sys.stderr,
        )
        return EXIT_USAGE

    mode = RunMode.DRY_RUN if args.dry_run else RunMode.EXECUTE
    project_root = _project_root(args.project_root)
    raw = _read_profile(args.profile)
    if args.task:
        raw["task_id"] = args.task
    cfg = load_config(raw, mode)

    backend = _make_backend(cfg, project_root)
    lock_dir = project_root / cfg.request.artifacts_root / "locks"
    stop = StopController()
    _install_sigint(stop)

    print(f"设备档案 : {cfg.request.profile.describe()}")
    print(f"任务      : {cfg.request.task_id}")
    print(f"运行模式  : {mode}   预算: {cfg.request.timeout_seconds}s / "
          f"恢复 {cfg.request.max_recoveries} 次 / 动作上限 {cfg.request.max_actions}")
    if mode is RunMode.EXECUTE:
        print("范围      : 仅纯领取；不含购买、元宝消费、抽奖、招募、兑换、派兵、战斗。元宝预算 0。")
        print("提示      : Ctrl+C 可停止；停止后不会发出新的输入，但已发送的动作可能已完成，无法回滚。")

    payload = run(cfg.request, backend, lock_dir, project_root, stop=stop)
    _print_summary(payload)
    if isinstance(payload, TaskResult):
        return STATUS_EXIT.get(payload.status, EXIT_FAILED)
    return EXIT_OK


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Start the loopback-only dashboard; this command never launches a task."""
    from .dashboard.server import serve

    return serve(_project_root(args.project_root), port=args.port, open_browser=not args.no_browser)


def _install_sigint(stop: StopController) -> None:
    def handler(signum: int, frame: Any) -> None:  # noqa: ARG001
        if stop.should_stop():
            print("\n再次收到 Ctrl+C：立即退出。", file=sys.stderr)
            raise SystemExit(EXIT_CANCELLED)
        print("\n收到 Ctrl+C：正在停止，不会再发出新的输入……", file=sys.stderr)
        stop.request_stop()

    try:
        signal.signal(signal.SIGINT, handler)
    except ValueError:  # pragma: no cover - 非主线程
        pass


# ------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sgbltx_helper",
        description="三国兵临天下 本地纯领取闭环（第一阶段）。只做零消费领取，不涉及任何消费操作。",
    )
    parser.add_argument("--version", action="version", version=f"sgbltx-helper {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_dev = sub.add_parser("devices", help="只列出 ADB 设备，不做任何选择与输入")
    p_dev.add_argument("--adb", default=None, help="指定 adb.exe 路径（可选）")
    p_dev.set_defaults(func=cmd_devices)

    p_ins = sub.add_parser("inspect", help="只连接、截图并记录当前页面状态；不启动游戏、不输入")
    p_ins.add_argument("--profile", required=True, help="配置文件路径，例如 config/local.json")
    p_ins.add_argument("--project-root", default=None, help="项目根目录（默认当前目录）")
    p_ins.set_defaults(func=cmd_inspect)

    p_run = sub.add_parser("run", help="按显式模式运行任务")
    p_run.add_argument("--profile", required=True, help="配置文件路径，例如 config/local.json")
    p_run.add_argument("--task", default=None, help="任务 ID（默认取配置文件中的 task_id）")
    p_run.add_argument("--project-root", default=None, help="项目根目录（默认当前目录）")
    group = p_run.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="只读分析当前页并列出候选动作，零输入")
    group.add_argument("--execute", action="store_true", help="真实执行经验证的导航与领取操作")
    p_run.set_defaults(func=cmd_run)

    p_dashboard = sub.add_parser("dashboard", help="启动仅监听本机的任务管理网页")
    p_dashboard.add_argument("--project-root", default=None, help="项目根目录（默认当前目录）")
    p_dashboard.add_argument("--port", default=8782, type=int, help="本机网页端口（默认 8782）")
    p_dashboard.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    p_dashboard.set_defaults(func=cmd_dashboard)

    return parser


def _force_utf8_console() -> None:
    """让中文输出在 Windows 控制台（PowerShell / cmd）里正确显示。

    实测：不设置时，输出被按 GBK 解码成乱码。这里同时把控制台代码页切到
    65001 并重设 Python 的 stdout/stderr 编码。
    """
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:  # noqa: BLE001
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse 用法错误（互斥参数同时给出等）统一映射到 EXIT_USAGE，
        # 让「没有显式选择模式就拒绝」有稳定的退出码。
        if exc.code in (0, None):
            raise
        return EXIT_USAGE
    try:
        return int(args.func(args))
    except ContractError as exc:
        print(f"配置/契约错误：{exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        return EXIT_CANCELLED
