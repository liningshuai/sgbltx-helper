"""有限状态控制器：主城封地产业资源气泡收取。

点击任意一个产业资源气泡会一次领完全部产出。本任务每轮最多点一个经验证、
未被"入侵"标记排除的气泡；之后只读等待奖励弹窗，逐张关闭，并确认回到
不再有资源气泡的主城。不拖动地图，不点其他标记。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from .classification import Classification, classify
from .contracts import (
    CandidateAction, InspectionResult, ReasonCode, RunMode, RunStatus, TaskRequest, TaskResult,
)
from .evidence import RunWorkspace, utc_now_iso
from .maa_adapter import Backend, MaaError, MaaStopUnconfirmed, MaaStopped, MaaTimeout
from .pages import Matcher, PageCatalog

SCREEN_W, SCREEN_H = 1080, 1920


@dataclass(frozen=True)
class BubbleScan:
    candidate: tuple[int, int] | None
    template: str | None
    score: float | None
    verified_matchers: int
    invasion_rejected: int
    exclusion_verified: bool = True


def _matcher(raw: dict[str, Any], default_roi: list[int] | tuple[int, ...] = (0, 0, 0, 0)) -> Matcher:
    roi = raw.get("roi", default_roi)
    return Matcher(
        template=str(raw["template"]),
        threshold=float(raw.get("threshold", 0.97)),
        roi=tuple(int(v) for v in roi),
        verified=bool(raw.get("verified", False)),
    )


def _clamped_roi(x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(SCREEN_W, x + w), min(SCREEN_H, y + h)
    return (x0, y0, max(1, x1 - x0), max(1, y1 - y0))


def _red_banner_pixels(image: np.ndarray, region: tuple[int, int, int, int]) -> int:
    """统计区域内饱和红像素（BGR 图）；红底“入侵”横幅不论摆动到什么角度都会大量命中。"""
    x, y, w, h = region
    patch = image[y:y + h, x:x + w].astype(np.int16)
    if patch.ndim != 3 or patch.shape[2] < 3:
        return 0
    blue, green, red = patch[..., 0], patch[..., 1], patch[..., 2]
    return int(((red > 150) & (green < 80) & (blue < 90)).sum())


def find_bubble(
    backend: Backend, image: np.ndarray, config: dict[str, Any],
    deadline: float | None = None, should_stop: Callable[[], bool] | None = None,
) -> BubbleScan:
    """在主城地图区域找一个可点的资源气泡；下方带"入侵"字样的候选一律丢弃。"""
    search_roi = config.get("bubble_search_roi", [0, 0, 0, 0])
    matchers = [
        _matcher(raw, search_roi) for raw in config.get("resource_bubbles", [])
        if isinstance(raw, dict) and raw.get("verified")
    ]
    label_raw = config.get("invasion_label")
    label_verified = isinstance(label_raw, dict) and bool(label_raw.get("verified"))
    label_templates: list[str] = []
    if label_verified:
        label_templates = [str(t) for t in (label_raw.get("templates") or [label_raw.get("template")]) if t]
    label_threshold = float(label_raw.get("threshold", 0.8)) if label_verified else 0.0
    red_min_pixels = label_raw.get("red_min_pixels") if label_verified else None
    offset = label_raw.get("offset_roi", [-90, 20, 180, 110]) if label_verified else None
    best: tuple[float, tuple[int, int], str] | None = None
    rejected = 0
    for m in matchers:
        outcome = backend.recognize(image, m.template, m.roi, m.threshold, deadline, should_stop)
        hits: list[tuple[tuple[int, int, int, int], float]] = []
        for item in (outcome.raw or {}).get("filtered") or []:
            box = item.get("box")
            if box and len(box) == 4:
                hits.append((tuple(int(v) for v in box), float(item.get("score") or 0.0)))
        if not hits and outcome.hit and outcome.box is not None:
            hits.append((outcome.box, float(outcome.score or 0.0)))
        for (x, y, w, h), score in hits:
            center = (x + w // 2, y + h // 2)
            if not label_verified or not label_templates:
                # 没有已验证的"入侵"排除素材时不授权任何候选，也不计作已排除。
                continue
            dx, dy, ow, oh = (int(v) for v in offset)
            region = _clamped_roi(center[0] + dx, center[1] + dy, ow, oh)
            if isinstance(red_min_pixels, int) and _red_banner_pixels(image, region) >= red_min_pixels:
                rejected += 1
                continue
            if any(
                backend.recognize(image, template, region, label_threshold, deadline, should_stop).hit
                for template in label_templates
            ):
                rejected += 1
                continue
            if best is None or score > best[0]:
                best = (score, center, m.template)
    return BubbleScan(
        candidate=best[1] if best else None,
        template=best[2] if best else None,
        score=best[0] if best else None,
        verified_matchers=len(matchers),
        invasion_rejected=rejected,
        exclusion_verified=label_verified and bool(label_templates),
    )


def _blocker_reason(cls: Classification) -> ReasonCode:
    code = cls.blockers[0].reason_code if cls.blockers else None
    return ReasonCode.ACCOUNT_CONFLICT if code == "account_conflict" else ReasonCode.UNKNOWN_PAGE


def _partial_reward(cls: Classification) -> bool:
    pages = set(cls.ambiguous_pages)
    return bool(pages) and "reward_popup" in pages and pages.issubset({"reward_popup", "main_city"})


def run_fief_industry(
    *, request: TaskRequest, backend: Backend, catalog: PageCatalog,
    config: dict[str, Any], workspace: RunWorkspace, run_id: str, started_at: str,
    initial_image: np.ndarray, before_path: Any, seq: int, deadline: float,
    stop: Any,
) -> TaskResult | InspectionResult:
    """只执行由当前屏幕授权的一步，并在每步后重新识别。"""
    controls = config.get("controls", {})
    max_reward_popups = int(config.get("max_reward_popups", 4))
    action_counts = {"claim_action_count": 0, "dismiss_action_count": 0}
    claim_request_submitted = False
    claim_verified = False
    reward_popup_count = 0
    cleanup_verified = False
    final_page_id: str | None = None
    current_image = initial_image
    last_after_path: Any | None = None
    scan: BubbleScan | None = None
    input_error: str | None = None
    input_error_message: str | None = None

    def log(node: str, event: str, detail: dict[str, Any] | None = None) -> None:
        nonlocal seq
        workspace.log_event(seq, node, event, detail or {})
        seq += 1

    def page_id_of(cls: Classification) -> str | None:
        return cls.page.page_id if cls.page else None

    def finish(status: RunStatus, reason: ReasonCode, message: str = "") -> TaskResult:
        evidence_paths = [str(before_path)]
        if last_after_path is not None:
            evidence_paths.append(str(last_after_path))
        evidence_paths.extend([str(workspace.events_path), str(workspace.result_path)])
        detail = {
            **action_counts,
            "claim_request_submitted": claim_request_submitted,
            "claim_verified": claim_verified,
            "reward_popup_count": reward_popup_count,
            "cleanup_only": cleanup_only,
            "cleanup_verified": cleanup_verified,
            "final_page_id": final_page_id,
            "bubble_template": scan.template if scan else None,
            "bubble_score": scan.score if scan else None,
            "invasion_rejected_count": scan.invasion_rejected if scan else 0,
            "visible_area_only": True,
            "before": str(before_path),
            "after": str(last_after_path) if last_after_path is not None else None,
            "message": message,
            "input_error": input_error,
            "input_error_message": input_error_message,
        }
        result = TaskResult(
            run_id=run_id, task_id=request.task_id, account_alias=request.profile.account_alias,
            device_serial=request.profile.serial, status=status, reason_code=reason,
            started_at=started_at, finished_at=utc_now_iso(),
            action_count=sum(action_counts.values()), evidence_paths=evidence_paths,
            mode=request.mode, detail=detail,
        )
        workspace.write_json(workspace.result_path, result.to_dict())
        log("finish", "run_finished", {"status": str(status), "reason": str(reason)})
        return result

    def attach_stop_context(exc: MaaStopUnconfirmed) -> None:
        exc.run_context = {
            **action_counts,
            "action_count": sum(action_counts.values()),
            "claim_request_submitted": claim_request_submitted,
            "claim_verified": claim_verified,
            "cleanup_only": cleanup_only,
            "cleanup_verified": cleanup_verified,
            "final_page_id": final_page_id,
            "action_count_is_upper_bound": True,
            "input_outcome_uncertain": True,
            "before": str(before_path),
            "after": str(last_after_path) if last_after_path is not None else None,
        }

    def submit_click(name: str, x: int, y: int, counter: str, detail: dict[str, Any],
                     on_submit: Callable[[], None] | None = None) -> bool:
        nonlocal input_error, input_error_message
        input_error = None
        input_error_message = None
        if sum(action_counts.values()) >= request.max_actions:
            input_error = "budget"
            log(name, "action_budget_exhausted", {"max_actions": request.max_actions})
            return False
        if stop.should_stop() or time.monotonic() >= deadline:
            input_error = "stopped" if stop.should_stop() else "timeout"
            log(name, "input_prevented", {"stop_requested": stop.should_stop()})
            return False
        if on_submit is not None:
            on_submit()
        action_counts[counter] += 1
        log(name, "input_submitted", {"x": x, "y": y, **detail})
        try:
            succeeded = bool(backend.click(x, y, deadline, stop.should_stop))
        except MaaStopUnconfirmed as exc:
            attach_stop_context(exc)
            raise
        except MaaStopped as exc:
            input_error, input_error_message = "stopped", str(exc)
            return False
        except MaaTimeout as exc:
            input_error, input_error_message = "timeout", str(exc)
            return False
        except MaaError as exc:
            input_error, input_error_message = "sdk", str(exc)
            return False
        if not succeeded:
            input_error = "sdk"
        return succeeded

    def input_failure(default_reason: ReasonCode, message: str) -> TaskResult:
        if input_error == "stopped" or stop.should_stop():
            return finish(RunStatus.CANCELLED, ReasonCode.USER_CANCELLED, message)
        if input_error == "timeout" or time.monotonic() >= deadline:
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT, message)
        if input_error == "budget":
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.ACTION_BUDGET_EXHAUSTED, message)
        if input_error == "sdk":
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN, message)
        return finish(RunStatus.NEEDS_ATTENTION, default_reason, message)

    def screenshot() -> Classification:
        nonlocal current_image, last_after_path, final_page_id
        image = backend.screencap(deadline, stop.should_stop)
        cls_after = classify(backend, image, catalog, deadline, stop.should_stop)
        workspace.save_image(f"step-{seq:04d}", image)
        last_after_path = workspace.save_image("after", image)
        current_image = image
        final_page_id = page_id_of(cls_after)
        log("verify", "fief_transition_observed", {
            "page_id": final_page_id, "ambiguous": list(cls_after.ambiguous_pages),
            "blockers": [p.page_id for p in cls_after.blockers],
        })
        return cls_after

    def pause(message: str) -> None:
        remaining = min(end_of_window, deadline) - time.monotonic()
        stop.wait(min(0.25, max(0.0, remaining)))
        if stop.should_stop():
            raise MaaStopped(message)
        if time.monotonic() >= deadline:
            raise MaaTimeout(message)

    def wait_after_claim() -> Classification:
        """领取后只读等待奖励弹窗出现；动画半显示帧和尚未变化的主城都只等待。"""
        nonlocal end_of_window
        end_of_window = min(deadline, time.monotonic() + float(config.get("transition_wait_seconds", 8)))
        while True:
            cls_after = screenshot()
            page_id = page_id_of(cls_after)
            # 半显示奖励、尚未变化的主城、以及点击后的白光/镜头平移等未知过渡帧都只读等待。
            waiting = not cls_after.blockers and (
                _partial_reward(cls_after)
                or (page_id in {"main_city", None} and not cls_after.ambiguous_pages)
            )
            if waiting and time.monotonic() < end_of_window:
                log("verify", "claim_reward_transition_pending", {"page_id": page_id})
                pause("等待产业奖励弹窗时收到停止请求或超过整轮截止时间")
                continue
            return cls_after

    def wait_after_dismiss() -> tuple[Classification, bool]:
        """关闭后只读等待前景退出；只有观察到退出证据后出现的弹窗才算下一张。"""
        nonlocal end_of_window
        end_of_window = min(deadline, time.monotonic() + float(config.get("transition_wait_seconds", 8)))
        exit_seen = False
        while True:
            cls_after = screenshot()
            page_id = page_id_of(cls_after)
            if cls_after.blockers:
                return cls_after, exit_seen
            if page_id == "main_city" and not cls_after.ambiguous_pages:
                return cls_after, True
            if page_id == "reward_popup" and exit_seen:
                return cls_after, True
            reward_cue = any(o.hit for o in cls_after.observations if o.name.startswith("reward_popup:identity:"))
            if not reward_cue and not cls_after.ambiguous_pages:
                exit_seen = True
            if time.monotonic() >= end_of_window:
                return cls_after, exit_seen
            log("verify", "reward_transition_pending", {"page_id": page_id, "exit_seen": exit_seen})
            pause("等待产业奖励关闭过渡时收到停止请求或超过整轮截止时间")

    def guarded(call: Callable[[], Any]) -> Any:
        try:
            return call()
        except MaaStopUnconfirmed as exc:
            attach_stop_context(exc)
            raise
        except MaaStopped as exc:
            return finish(RunStatus.CANCELLED, ReasonCode.USER_CANCELLED, f"动作后收到停止；已记录输入数：{exc}")
        except MaaTimeout as exc:
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT, f"共享截止时间内未确认动作后状态；已记录输入数：{exc}")
        except MaaError as exc:
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN, f"动作后识别/截图失败；已记录输入数：{exc}")

    end_of_window = deadline
    cls = classify(backend, current_image, catalog, deadline, stop.should_stop)
    final_page_id = page_id_of(cls)
    cleanup_only = final_page_id == "reward_popup" and not cls.blockers
    if final_page_id == "main_city" and not cls.blockers:
        scan = find_bubble(backend, current_image, config, deadline, stop.should_stop)
    log("classify", "fief_initial_state", {
        "page_id": final_page_id, "blockers": [p.page_id for p in cls.blockers],
        "ambiguous": list(cls.ambiguous_pages),
        "bubble": scan.candidate if scan else None, "bubble_template": scan.template if scan else None,
        "verified_bubble_matchers": scan.verified_matchers if scan else None,
        "invasion_rejected": scan.invasion_rejected if scan else 0,
    })

    if request.mode is RunMode.DRY_RUN:
        candidates: list[CandidateAction] = []
        notes = ["封地产业 dry-run 只分析当前画面；没有领取或关闭输入。"]
        if scan is not None:
            if scan.verified_matchers == 0:
                notes.append("资源气泡素材待首次实机采样验证；不能据此判断有无产出。")
            elif not scan.exclusion_verified:
                notes.append("“入侵”排除素材未验证；不能据此判断有无产出。")
            elif scan.candidate is None:
                notes.append("当前视野内未识别到可点的资源气泡（仅代表当前视野）。")
            else:
                candidates.append(CandidateAction(
                    action_id="collect_bubble",
                    description=f"点击资源气泡 {scan.template} 中心 {scan.candidate}，一次领取全部产业产出",
                    verified=True,
                ))
            if scan.invasion_rejected:
                notes.append(f"已排除 {scan.invasion_rejected} 个带“入侵”字样的标记。")
        elif final_page_id == "reward_popup":
            raw = controls.get("reward_close", {})
            verified = bool(raw.get("verified")) and bool(cls.page and cls.page.verified)
            candidates.append(CandidateAction(
                action_id="reward_close", description=str(raw.get("description", "reward_close")),
                verified=verified, blocked_reason=None if verified else "产业奖励弹窗素材待验证",
            ))
        inspection = InspectionResult(
            run_id=run_id, task_id=request.task_id, account_alias=request.profile.account_alias,
            device_serial=request.profile.serial, observed_at=utc_now_iso(), page_id=final_page_id,
            observations=list(cls.observations), candidate_actions=candidates,
            blockers=[f"命中阻断页 {p.page_id}" for p in cls.blockers],
            evidence_paths=[str(before_path), str(workspace.events_path), str(workspace.inspection_path)],
            notes=notes,
        )
        workspace.write_json(workspace.inspection_path, inspection.to_dict())
        log("inspect", "fief_dry_run", {"page_id": final_page_id})
        return inspection

    if cls.blockers:
        return finish(RunStatus.NEEDS_ATTENTION, _blocker_reason(cls), "当前画面有阻断提示；未发出输入")
    if cls.ambiguous_pages or final_page_id not in {"main_city", "reward_popup"}:
        return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.UNKNOWN_PAGE, "页面身份未知或有歧义；未发出输入")

    if final_page_id == "main_city":
        assert scan is not None
        if not cls.page or not cls.page.verified:
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.ASSETS_UNVERIFIED, "主城页面身份尚未验证")
        if scan.verified_matchers == 0 or not scan.exclusion_verified:
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.ASSETS_UNVERIFIED,
                          "资源气泡或“入侵”排除素材待验证；未发出输入")
        if scan.candidate is None:
            return finish(RunStatus.NOTHING_TO_CLAIM, ReasonCode.NOTHING_TO_CLAIM,
                          "当前视野内没有可点的资源气泡；未发出输入")

        fresh = guarded(lambda: (screenshot(), None)[0])
        if isinstance(fresh, TaskResult):
            return fresh
        if fresh.blockers:
            return finish(RunStatus.NEEDS_ATTENTION, _blocker_reason(fresh), "点击前新鲜画面出现阻断提示；未发出输入")
        if fresh.ambiguous_pages or page_id_of(fresh) != "main_city":
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.UNKNOWN_PAGE, "点击前新鲜画面不再是主城；未发出输入")
        fresh_scan = guarded(lambda: find_bubble(backend, current_image, config, deadline, stop.should_stop))
        if isinstance(fresh_scan, TaskResult):
            return fresh_scan
        scan = fresh_scan
        log("classify", "fief_fresh_bubble_scan", {
            "bubble": scan.candidate, "bubble_template": scan.template, "invasion_rejected": scan.invasion_rejected,
        })
        if scan.candidate is None:
            return finish(RunStatus.NOTHING_TO_CLAIM, ReasonCode.NOTHING_TO_CLAIM,
                          "点击前新鲜画面视野内已没有可点的资源气泡；未发出输入")

        def mark_claim_submitted() -> None:
            nonlocal claim_request_submitted
            claim_request_submitted = True

        x, y = scan.candidate
        if not submit_click("collect_bubble", x, y, "claim_action_count",
                            {"template": scan.template, "score": scan.score}, mark_claim_submitted):
            return input_failure(ReasonCode.OUTCOME_UNCERTAIN, "气泡点击未能安全提交或结果不确定；不会重试")
        outcome = guarded(wait_after_claim)
        if isinstance(outcome, TaskResult):
            return outcome
        cls = outcome
        if cls.blockers:
            return finish(RunStatus.NEEDS_ATTENTION, _blocker_reason(cls), "领取后出现阻断提示；不再输入")
        if page_id_of(cls) != "reward_popup":
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                          "点击气泡后未出现产业奖励弹窗；不能确认领取，不会重试")
        reward_assets_verified = bool(cls.page and cls.page.verified) and bool(
            isinstance(controls.get("reward_close"), dict) and controls["reward_close"].get("verified"))
        claim_verified = reward_assets_verified
        log("verify", "fief_reward_observed", {"reward_assets_verified": reward_assets_verified})

    while True:
        page_id = page_id_of(cls)
        if page_id == "main_city":
            cleanup_verified = True
            if cleanup_only:
                return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                              "已关闭运行前的奖励弹窗并返回主城；未提交新领取，不确认此前领取结果")
            scan_after = guarded(lambda: find_bubble(backend, current_image, config, deadline, stop.should_stop))
            if isinstance(scan_after, TaskResult):
                return scan_after
            if scan_after.candidate is not None:
                return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.POSTCONDITION_NOT_MET,
                              "关闭奖励后主城上仍有资源气泡；不会再次领取")
            return finish(RunStatus.COMPLETED, ReasonCode.CLAIM_CONFIRMED,
                          "产业奖励已出现并关闭，主城视野内不再有资源气泡")
        if page_id != "reward_popup":
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.UNKNOWN_PAGE, "奖励关闭后页面身份未知；不再输入")
        raw = controls.get("reward_close")
        if not cls.page or not cls.page.verified or not isinstance(raw, dict) or not raw.get("verified"):
            return finish(
                RunStatus.NEEDS_ATTENTION,
                ReasonCode.OUTCOME_UNCERTAIN if claim_request_submitted else ReasonCode.ASSETS_UNVERIFIED,
                "产业奖励弹窗素材尚未验证；保留当前画面，不点击",
            )
        if reward_popup_count >= max_reward_popups:
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.BUDGET_EXHAUSTED,
                          f"本轮奖励弹窗数量达到安全上限 {max_reward_popups}；保留当前画面，不再点击")
        m = _matcher(raw)
        found = guarded(lambda: backend.recognize(current_image, m.template, m.roi, m.threshold, deadline, stop.should_stop))
        if isinstance(found, TaskResult):
            return found
        if not found.hit or found.center is None:
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN, "奖励关闭提示未命中；不点击")
        reward_popup_count += 1
        x, y = found.center
        if not submit_click("reward_close", x, y, "dismiss_action_count",
                            {"template": m.template, "popup_index": reward_popup_count}):
            return input_failure(ReasonCode.OUTCOME_UNCERTAIN, "奖励关闭未能安全提交")
        outcome = guarded(wait_after_dismiss)
        if isinstance(outcome, TaskResult):
            return outcome
        cls, exit_seen = outcome
        if cls.blockers:
            return finish(RunStatus.NEEDS_ATTENTION, _blocker_reason(cls), "关闭奖励后出现阻断提示；不再输入")
        if page_id_of(cls) == "reward_popup" and not exit_seen:
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.POSTCONDITION_NOT_MET,
                          "关闭后仍是同一奖励画面；无法确认出现了下一张弹窗，不重复点击")
