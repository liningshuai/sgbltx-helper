"""页面规格与只读分类。

``resource/pages.json`` 是页面身份的**唯一来源**：它声明每个页面用什么模板
识别、哪些是「可领」/「已领或为空」特征、以及是否已被实测验证。

关键约束（设计说明 4/8/9）：未验证的页面状态不执行动作。因此每个 matcher
和每个候选动作都带 ``verified`` 标记，只有实测采集并正反样本验证过的条目
才允许进入 execute 路径。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import ContractError

PAGE_ROLE_TARGET = "target"
PAGE_ROLE_ENTRY = "entry"
PAGE_ROLE_BLOCKER = "blocker"


@dataclass(frozen=True)
class Matcher:
    """一个模板匹配条件。"""

    template: str
    threshold: float = 0.85
    roi: tuple[int, int, int, int] = (0, 0, 0, 0)
    verified: bool = False
    # 归一化模板匹配不受整体变暗影响；需要排除遮罩（弹窗、菜单）下的同一界面时，
    # 规定命中区域的最低平均亮度。默认不检查。
    min_mean: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "template": self.template,
            "threshold": self.threshold,
            "roi": list(self.roi),
            "verified": self.verified,
        }
        if self.min_mean is not None:
            data["min_mean"] = self.min_mean
        return data


@dataclass(frozen=True)
class CandidateActionSpec:
    action_id: str
    description: str
    verified: bool = False
    reason_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "description": self.description,
            "verified": self.verified,
            "reason_code": self.reason_code,
        }


@dataclass
class PageSpec:
    page_id: str
    role: str = PAGE_ROLE_TARGET
    reason_code: str | None = None
    description: str = ""
    verified: bool = False
    identity: list[Matcher] = field(default_factory=list)
    claimable: list[Matcher] = field(default_factory=list)
    already_claimed: list[Matcher] = field(default_factory=list)
    allowed_actions: list[CandidateActionSpec] = field(default_factory=list)

    @property
    def is_blocker(self) -> bool:
        return self.role == PAGE_ROLE_BLOCKER


@dataclass
class PageCatalog:
    """全部页面规格。"""

    schema_version: int
    resolution: tuple[int, int]
    pages: list[PageSpec]
    captured_at: str | None = None
    game: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    entry_node: str = "ClaimPrimary"
    target_page_id: str = ""

    def by_id(self, page_id: str) -> PageSpec | None:
        for p in self.pages:
            if p.page_id == page_id:
                return p
        return None

    @property
    def blockers(self) -> list[PageSpec]:
        return [p for p in self.pages if p.is_blocker]

    def all_matchers(self) -> list[tuple[str, str, Matcher]]:
        """(page_id, kind, matcher) 三元组，kind ∈ identity/claimable/already_claimed。"""
        out: list[tuple[str, str, Matcher]] = []
        for p in self.pages:
            for m in p.identity:
                out.append((p.page_id, "identity", m))
            for m in p.claimable:
                out.append((p.page_id, "claimable", m))
            for m in p.already_claimed:
                out.append((p.page_id, "already_claimed", m))
        return out

    def unverified_assets(self) -> list[str]:
        """列出尚未实测验证的模板名，供 execute 前置拒绝使用。"""
        return sorted({m.template for _, _, m in self.all_matchers() if not m.verified})


def _parse_matcher(raw: Any, where: str) -> Matcher:
    if not isinstance(raw, dict):
        raise ContractError(f"{where} 必须是对象")
    template = str(raw.get("template") or "").strip()
    if not template:
        raise ContractError(f"{where} 缺少 template")
    roi_raw = raw.get("roi") or [0, 0, 0, 0]
    if not isinstance(roi_raw, (list, tuple)) or len(roi_raw) != 4:
        raise ContractError(f"{where}.roi 必须是 [x, y, w, h]")
    min_mean = raw.get("min_mean")
    if min_mean is not None and (isinstance(min_mean, bool) or not isinstance(min_mean, (int, float))):
        raise ContractError(f"{where}.min_mean 必须是数字")
    return Matcher(
        template=template,
        threshold=float(raw.get("threshold", 0.85)),
        roi=(int(roi_raw[0]), int(roi_raw[1]), int(roi_raw[2]), int(roi_raw[3])),
        verified=bool(raw.get("verified", False)),
        min_mean=float(min_mean) if min_mean is not None else None,
    )


def load_page_catalog(path: Path) -> PageCatalog:
    """读取 resource/pages.json。"""
    path = Path(path)
    if not path.is_file():
        raise ContractError(f"页面规格文件不存在：{path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"页面规格不是合法 JSON：{path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ContractError("页面规格根节点必须是对象")

    res = raw.get("resolution") or [0, 0]
    pages: list[PageSpec] = []
    for item in raw.get("pages") or []:
        if not isinstance(item, dict):
            raise ContractError("pages 的元素必须是对象")
        page_id = str(item.get("page_id") or "").strip()
        if not page_id:
            raise ContractError("pages 元素缺少 page_id")
        spec = PageSpec(
            page_id=page_id,
            role=str(item.get("role") or PAGE_ROLE_TARGET),
            reason_code=item.get("reason_code"),
            description=str(item.get("description") or ""),
            verified=bool(item.get("verified", False)),
            identity=[_parse_matcher(m, f"pages[{page_id}].identity") for m in item.get("identity") or []],
            claimable=[_parse_matcher(m, f"pages[{page_id}].claimable") for m in item.get("claimable") or []],
            already_claimed=[
                _parse_matcher(m, f"pages[{page_id}].already_claimed") for m in item.get("already_claimed") or []
            ],
            allowed_actions=[
                CandidateActionSpec(
                    action_id=str(a.get("action_id") or ""),
                    description=str(a.get("description") or ""),
                    verified=bool(a.get("verified", False)),
                    reason_code=a.get("reason_code"),
                )
                for a in item.get("allowed_actions") or []
                if isinstance(a, dict)
            ],
        )
        pages.append(spec)

    return PageCatalog(
        schema_version=int(raw.get("schema_version", 1)),
        resolution=(int(res[0]), int(res[1])),
        pages=pages,
        captured_at=raw.get("captured_at"),
        game=raw.get("game") or {},
        notes=list(raw.get("notes") or []),
        entry_node=str(raw.get("entry_node") or "ClaimPrimary"),
        target_page_id=str(raw.get("target_page_id") or ""),
    )
