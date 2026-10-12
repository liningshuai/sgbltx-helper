"""只读页面分类。

输入：一张截图 + 页面规格 + 识别后端。
输出：命中/未命中的结构化观察。**本模块不发出任何输入**，因此可以安全地
同时服务于 inspect / dry-run / execute 的前置与后置判定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .contracts import Observation
from .maa_adapter import Backend
from .pages import Matcher, PageCatalog, PageSpec


@dataclass
class Classification:
    """一次分类的结果。"""

    page: PageSpec | None = None
    observations: list[Observation] = field(default_factory=list)
    claimable: list[Observation] = field(default_factory=list)
    already_claimed: list[Observation] = field(default_factory=list)
    blockers: list[PageSpec] = field(default_factory=list)
    ambiguous_pages: list[str] = field(default_factory=list)

    @property
    def has_claimable(self) -> bool:
        return any(o.hit for o in self.claimable)

    @property
    def has_already_claimed_marker(self) -> bool:
        return any(o.hit for o in self.already_claimed)

    def claimable_box(self) -> tuple[int, int, int, int] | None:
        for o in self.claimable:
            if o.hit and o.box is not None:
                return o.box
        return None


def _evaluate(
    backend: Backend, image: np.ndarray, page_id: str, kind: str, matcher: Matcher,
    deadline: float | None = None, should_stop: Callable[[], bool] | None = None,
) -> Observation:
    outcome = backend.recognize(image, matcher.template, matcher.roi, matcher.threshold, deadline, should_stop)
    return Observation(
        name=f"{page_id}:{kind}:{matcher.template}",
        kind=kind,
        hit=bool(outcome.hit),
        score=outcome.score,
        box=outcome.box,
    )


def _all_identity_hit(observations: list[Observation], page_id: str) -> bool:
    identity = [o for o in observations if o.name.startswith(f"{page_id}:identity:")]
    return bool(identity) and all(o.hit for o in identity)


def classify(
    backend: Backend, image: np.ndarray, catalog: PageCatalog,
    deadline: float | None = None, should_stop: Callable[[], bool] | None = None,
) -> Classification:
    """对一张截图做页面分类。

    身份判定用 AND：一个页面的全部 identity 模板都命中才算识别成功，
    避免单一模板误命中导致误判。
    """
    result = Classification()

    for page in catalog.pages:
        for matcher in page.identity:
            result.observations.append(_evaluate(backend, image, page.page_id, "identity", matcher, deadline, should_stop))

    # 阻断页优先：登录页 / 验证码 / 顶号提示一旦成立，立刻判定，不再往下走
    for page in catalog.blockers:
        if _all_identity_hit(result.observations, page.page_id):
            result.blockers.append(page)

    matched = [
        p for p in catalog.pages if not p.is_blocker and _all_identity_hit(result.observations, p.page_id)
    ]
    matched_targets = [p for p in matched if p.role == "target"]
    matched_entries = [p for p in matched if p.role != "target"]

    # Partial foreground targets and blockers suppress a matched page
    # underneath it. A completely identified blocker may resolve partial cues
    # from another known blocker variant; the blocker still prevents all
    # background actions except its explicitly registered safe handling.
    partial_targets = []
    for page in catalog.pages:
        if not page.is_blocker and page.role != "target":
            continue
        identity = [o for o in result.observations if o.name.startswith(f"{page.page_id}:identity:")]
        if any(o.hit for o in identity) and not _all_identity_hit(result.observations, page.page_id):
            partial_targets.append(page.page_id)
    page_by_id = {page.page_id: page for page in catalog.pages}
    partial_foreground_targets = [
        page_id for page_id in partial_targets
        if page_by_id.get(page_id) is not None and page_by_id[page_id].role == "target"
    ]

    # A fully identified foreground target (for example, a reward overlay) takes
    # precedence over an entry page that remains visible underneath it.
    if len(matched_targets) > 1:
        result.ambiguous_pages = [p.page_id for p in matched_targets]
    elif len(result.blockers) > 1:
        result.ambiguous_pages = [p.page_id for p in result.blockers]
    elif result.blockers and partial_foreground_targets:
        # A fully identified blocker dominates its own partial variant cues,
        # but a partial foreground target (such as a reward popup) remains an
        # ambiguity even when another blocker is present.
        result.ambiguous_pages = list(dict.fromkeys([p.page_id for p in matched_entries] + partial_targets))
    elif partial_targets and not result.blockers:
        result.ambiguous_pages = list(dict.fromkeys([p.page_id for p in matched] + partial_targets))
    elif len(matched_targets) == 1:
        result.page = matched_targets[0]
    else:
        if len(matched_entries) > 1:
            result.ambiguous_pages = [p.page_id for p in matched_entries]
        elif len(matched_entries) == 1:
            result.page = matched_entries[0]

    # 只有在确定页面身份后才评估可领/已领特征——避免在陌生页面上解释按钮语义
    if result.page is not None and not result.blockers:
        for matcher in result.page.claimable:
            result.claimable.append(_evaluate(backend, image, result.page.page_id, "claimable", matcher, deadline, should_stop))
        for matcher in result.page.already_claimed:
            result.already_claimed.append(
                _evaluate(backend, image, result.page.page_id, "already_claimed", matcher, deadline, should_stop)
            )

    return result
