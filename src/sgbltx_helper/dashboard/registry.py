"""Fixed dashboard module and local-profile registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..contracts import ContractError, RunMode, load_config

TASKS: tuple[dict[str, Any], ...] = (
    {
        "task_id": "mail_bulk_claim",
        "name": "系统邮件一键领取",
        "enabled_by_default": True,
        "icon": "✉",
        "route": "主城 → 更多 → 系统邮件 → 一键阅读 → 收尾",
        "run_label": "测试运行",
        "run_title": "批量有效奖励仍待实机验收",
        "acceptance": "主城导航已验证；有效奖励批量领取待实机验收",
        "description": "从已识别主城导航到系统邮件；批量奖励仍待实机验收。",
    },
    {
        "task_id": "claim_primary",
        "name": "单封太守奖励",
        "enabled_by_default": False,
        "icon": "◇",
        "route": "需由用户手动打开指定的太守奖励邮件详情",
        "run_label": "运行单封领取",
        "run_title": "仅运行已登记的单封领取任务",
        "acceptance": "单封领取已验证；需手动进入指定邮件详情",
        "description": "用户手动打开指定太守奖励详情后执行。",
    },
    {
        "task_id": "daily_rewards_claim",
        "name": "日常任务奖励",
        "max_batch_rounds": 5,
        "enabled_by_default": False,
        "icon": "☆",
        "acceptance": "部分验收：绿态循环与灰态回城已离线验证；有效奖励与完整实机闭环待验收",
        "description": "沿原生一键领取循环到灰态；不自动做任务、不点击前往、不购买或消耗黄金。",
        "route": "主城 → 任务 → 政务 → 日常任务 → 一键领取循环（最多 5 轮）→ 灰态后返回主城",
        "run_label": "运行一键领取",
        "run_title": "每轮只按新鲜绿态提交；确认灰态后安全回城",
        "behavior_notes": [
            "只依赖明确的日常页身份和顶部绿/灰按钮状态；固定四行、任务名称和完成计数不作为领取门槛。",
            "每轮最多提交一次；必须看到并关闭本轮奖励，再重新识别绿态才开始下一轮；最多 5 轮。",
            "公告跳过仅支持已校准的日常页：确认攻城令公告后点空白一次，再识别；不点击前往。",
            "物品名称识别仅作记录；确认奖励弹窗关闭、顶部灰态及返回主城后才报告完成。",
            "从已识别奖励弹窗开始只做收尾，不启动新循环。网页模块默认关闭，手动开启不会自动运行。",
        ],
    },
    {
        "task_id": "fief_industry_collect",
        "name": "封地产业收取",
        "enabled_by_default": False,
        "icon": "⛁",
        "acceptance": "已验收：实机单次运行完成领取、关闭奖励并回城；复跑零领取",
        "description": "在主城点一个产业资源气泡一次领完全部产出；不拖动地图、不点入侵或其他标记。",
        "route": "主城 → 点击一个产业资源气泡（一次领完）→ 关闭奖励 → 主城",
        "run_label": "运行产业收取",
        "run_title": "只点已验证、未被遮挡、非入侵的资源气泡；遇未登记提示零输入停止",
        "behavior_notes": [
            "只点已验证、未被遮挡、下方没有“入侵”字样的资源气泡，每轮最多一次。",
            "领取后只读等待奖励弹窗动画；每张弹窗确认前景退出后才处理下一张，最多 4 张。",
            "关闭后主城视野内仍有气泡则停止，不再领取。无气泡只代表当前视野。",
        ],
    },
)

_PROFILES: tuple[tuple[str, str, str], ...] = (
    ("local-main", "config/local.json", "高级工具档案"),
    ("local-test-mail", "config/local-test.json", "测试档案"),
)


def list_tasks() -> list[dict[str, Any]]:
    """Return a copy of the server-registered tasks."""
    return [dict(task) for task in TASKS]


def get_task(task_id: str) -> dict[str, Any] | None:
    for task in TASKS:
        if task["task_id"] == task_id:
            return dict(task)
    return None


def list_profiles(project_root: Path) -> list[dict[str, Any]]:
    return [get_profile(project_root, profile_id) for profile_id, _, _ in _PROFILES]


def get_profile(project_root: Path, profile_id: str) -> dict[str, Any] | None:
    entry = next((item for item in _PROFILES if item[0] == profile_id), None)
    if entry is None:
        return None
    registered_id, relative_path, label = entry
    root = Path(project_root).resolve()
    config_path = (root / relative_path).resolve()
    profile: dict[str, Any] = {
        "profile_id": registered_id,
        "label": label,
        "config_path": str(config_path),
        "available": False,
        "account_alias": None,
        "device_serial": None,
        "package_name": None,
        "expected_resolution": None,
        "timeout_seconds": None,
        "max_actions": None,
        "availability_reason": "本机配置文件不存在",
    }
    if root not in config_path.parents or not config_path.is_file():
        return profile
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("配置根节点必须是 JSON 对象")
        raw_profile = raw.get("profile")
        if not isinstance(raw_profile, dict):
            raise ValueError("profile 必须是 JSON 对象")
        device = raw_profile.get("device")
        if not isinstance(device, dict):
            raise ValueError("profile.device 必须是 JSON 对象")
        budget = raw.get("budget")
        if not isinstance(budget, dict):
            raise ValueError("budget 必须是 JSON 对象")
        required_profile = ("profile_id", "account_alias")
        required_device = ("adb_path", "serial", "package_name", "expected_resolution")
        required_budget = ("timeout_seconds", "max_recoveries", "max_actions")
        if any(not raw_profile.get(key) for key in required_profile):
            raise ValueError("profile 缺少必要字段")
        if any(not device.get(key) for key in required_device):
            raise ValueError("profile.device 缺少必要字段")
        if any(key not in budget for key in required_budget):
            raise ValueError("budget 缺少必要字段")
        if raw_profile.get("profile_id") != registered_id:
            raise ValueError("档案 ID 与本地注册 ID 不一致")
        request = load_config(raw, RunMode.DRY_RUN).request
        profile.update(
            available=True,
            account_alias=request.profile.account_alias,
            device_serial=request.profile.serial,
            package_name=request.profile.package_name,
            expected_resolution=list(request.profile.expected_resolution),
            timeout_seconds=request.timeout_seconds,
            max_actions=request.max_actions,
            availability_reason=None,
        )
    except (OSError, UnicodeError, ContractError, ValueError, KeyError, TypeError, AttributeError) as exc:
        profile["availability_reason"] = f"本机配置不可用：{exc}"
    return profile
