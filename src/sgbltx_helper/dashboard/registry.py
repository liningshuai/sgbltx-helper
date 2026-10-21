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
        "run_title": "一键阅读每轮最多一次；逐张关闭奖励后确认列表无待领并回城",
        "acceptance": "已验收：实机一次完成导航、一键阅读、奖励确认与关闭、批后列表与回城；复跑零领取",
        "description": "从已识别主城导航到系统邮件，一键阅读领取全部附件并回城。",
    },
    {
        "task_id": "claim_primary",
        "name": "单封太守奖励",
        "enabled_by_default": False,
        "icon": "◇",
        "route": "需由用户手动打开指定的太守奖励邮件详情",
        "run_label": "运行单封领取",
        "run_title": "仅运行已登记的单封领取任务",
        "acceptance": "已验收：单封领取已实机验证；需手动进入指定邮件详情",
        "description": "用户手动打开指定太守奖励详情后执行。",
    },
    {
        "task_id": "daily_rewards_claim",
        "name": "日常任务奖励",
        "max_batch_rounds": 5,
        "enabled_by_default": False,
        "icon": "☆",
        "acceptance": "已验收：实机绿态领取、关闭奖励、灰态回城一次完成；复跑零领取",
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

# 功能目录：分档参照用户购买的商用辅助“挂机设置”页；已登记任务的状态由其验收文案推导。
_REFERENCE_TIERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("基础功能", ("沙盘演绎", "比武大会", "蓬莱寻宝", "过关斩将", "群雄逐鹿", "见证传奇", "酒馆招募",
                  "珍宝阁", "商店购买", "折扣商店", "限时免单", "国家商店")),
    ("高级功能", ("异邦挑战", "城池拜访", "自动民情", "每日政务", "每日黄巾", "异族入侵", "封地管理",
                  "每日切磋", "魂玉合成", "万象轩")),
    ("钻石功能", ("自动派兵", "城池建设", "产业施政", "自动擂台", "智能跨服", "鸿阁争锋", "遗珍坊", "天命之战",
                  "朝廷密旨", "恒古梦魇", "智能航海", "寻龙分金", "天工坊", "安邦之路", "镇魂塔", "神灵托管",
                  "聊天记录", "三界酒肆", "英雄轮回", "资料库")),
)
_RECOMMENDED: dict[str, str] = {
    "一键挂机": "依次运行已验收的邮件、日常、封地产业任务；之后再加定时。",
    "每日政务": "位于已能进入的“任务”页“政务”页签，导航成本最低；需先实机确认是否为纯领取。",
}
_PLANNED_CORE: dict[str, str] = {"定时运行": "按游戏每日重置时间自动执行一键挂机；重置时间需实机确认。"}
# “日常收益”下已实现的任务（商用辅助把邮件、日常、产业等收益归在同一项）
_DAILY_INCOME_TASKS = ("mail_bulk_claim", "daily_rewards_claim", "fief_industry_collect", "claim_primary")


def _feature(name: str, status: str, note: str = "", task_id: str | None = None) -> dict[str, Any]:
    feature: dict[str, Any] = {"name": name, "status": status, "note": note}
    if task_id:
        feature["task_id"] = task_id
    return feature


def list_catalog() -> list[dict[str, Any]]:
    tasks = {task["task_id"]: task for task in TASKS}
    core = [_feature("一键挂机", "recommended", _RECOMMENDED["一键挂机"])]
    core += [_feature(name, "planned", note) for name, note in _PLANNED_CORE.items()]
    income = [
        _feature(
            tasks[task_id]["name"],
            "accepted" if str(tasks[task_id]["acceptance"]).startswith("已验收") else "in_progress",
            str(tasks[task_id]["acceptance"]),
            task_id,
        )
        for task_id in _DAILY_INCOME_TASKS if task_id in tasks
    ]
    groups = [{"title": "挂机", "subtitle": "串联与定时", "features": core}]
    for title, names in _REFERENCE_TIERS:
        features = list(income) if title == "基础功能" else []
        for name in names:
            if name in _RECOMMENDED:
                features.append(_feature(name, "recommended", _RECOMMENDED[name]))
            else:
                features.append(_feature(name, "planned"))
        groups.append({"title": title, "subtitle": "日常收益已实现" if title == "基础功能" else "", "features": features})
    return groups


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
