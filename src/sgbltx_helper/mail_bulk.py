"""有限状态控制器：系统邮件一键读取及领取。

此任务只按当帧识别结果选择下一步，不把一串预定点击提交给 Maa。
每个输入都必须先命中本页授权的素材，且输入后重新截图确认状态。
"""

from __future__ import annotations

import time
import hashlib
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from .classification import Classification, blocker_message, blocker_notes, blocker_reason, classify
from .contracts import (
    CandidateAction, InspectionResult, ReasonCode, RunMode, RunStatus, TaskRequest, TaskResult,
)
from .evidence import RunWorkspace, utc_now_iso
from .maa_adapter import Backend, MaaError, MaaStopUnconfirmed, MaaStopped, MaaTimeout
from .pages import Matcher, PageCatalog


@dataclass(frozen=True)
class BulkObservation:
    page_id: str | None
    claimable: bool = False
    no_pending: bool = False
    reward_items: bool = False
    reward_item_matches: tuple[str, ...] = ()
    reward_signature: str | None = None
    blocker: str | None = None
    ambiguous: bool = False


def choose_bulk_step(
    observation: BulkObservation, claim_request_submitted: bool, claim_verified: bool = False,
) -> str:
    """根据当前已观测状态返回唯一允许的下一步。"""
    if observation.blocker:
        return "stop_blocker"
    if observation.ambiguous or observation.page_id is None:
        return "stop_unknown"

    if observation.page_id == "main_city":
        return "open_more"
    if observation.page_id == "more_menu":
        return "open_mail"
    if observation.page_id == "reward_popup":
        return "dismiss_reward" if claim_request_submitted else "dismiss_initial_reward"
    if observation.page_id == "mail_detail":
        return "close_detail"
    if observation.page_id == "mail_inbox":
        if claim_request_submitted:
            if claim_verified or (observation.no_pending and not observation.claimable):
                return "close_mail"
            return "stop_uncertain"
        if observation.claimable:
            return "claim_all"
        if observation.no_pending:
            return "close_mail"
        return "stop_uncertain"
    return "stop_unknown"


def _matcher(raw: dict[str, Any]) -> Matcher:
    roi = raw.get("roi", [0, 0, 0, 0])
    return Matcher(
        template=str(raw["template"]),
        threshold=float(raw.get("threshold", 0.9)),
        roi=tuple(int(v) for v in roi),
        verified=bool(raw.get("verified", False)),
    )


def match_reward_items(
    backend: Backend,
    image: np.ndarray,
    definitions: Any,
    deadline: float | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[str]:
    """匹配独立的已验证奖励物品素材；面板/关闭标志不能充当物品证据。"""
    if not isinstance(definitions, list):
        return []
    matches: list[str] = []
    for raw in definitions:
        if not isinstance(raw, dict) or not raw.get("verified"):
            continue
        matcher = _matcher(raw)
        result = backend.recognize(
            image, matcher.template, matcher.roi, matcher.threshold, deadline, should_stop,
        )
        if result.hit:
            matches.append(matcher.template)
    return matches


def run_mail_bulk(
    *, request: TaskRequest, backend: Backend, catalog: PageCatalog,
    config: dict[str, Any], workspace: RunWorkspace, run_id: str, started_at: str,
    initial_image: np.ndarray, before_path: Any, seq: int, deadline: float,
    stop: Any,
) -> TaskResult | InspectionResult:
    """只执行由当前屏幕授权的一步，并在每步后重新识别。"""
    controls = config.get("controls", {})
    reward_items_raw = config.get("reward_items")
    empty_state_raw = config.get("empty_state")
    empty_state_matcher = (
        _matcher(empty_state_raw)
        if isinstance(empty_state_raw, dict) and empty_state_raw.get("verified") else None
    )
    action_counts = {"navigation_action_count": 0, "claim_action_count": 0, "dismiss_action_count": 0}
    claim_request_submitted = False
    claim_verified = False
    reward_evidence_complete = True
    unverified_reward_popup_count = 0
    cleanup_only = False
    reward_popup_count = 0
    last_closed_reward_signature: str | None = None
    max_reward_popups = int(config.get("max_reward_popups", 6))
    post_batch_state_verified = False
    cleanup_verified = False
    current_image = initial_image
    final_page_id: str | None = None
    last_after_path: Any | None = None
    no_reward_proven = False
    input_error: str | None = None
    input_error_message: str | None = None

    def log(node: str, event: str, detail: dict[str, Any] | None = None) -> None:
        nonlocal seq
        workspace.log_event(seq, node, event, detail or {})
        seq += 1

    def observe(image: np.ndarray) -> tuple[Classification, BulkObservation, bool]:
        cls = classify(backend, image, catalog, deadline, stop.should_stop)
        reward_item_matches: list[str] = []
        empty_state = False
        if cls.page is not None and cls.page.page_id == "reward_popup":
            reward_item_matches = match_reward_items(
                backend, image, reward_items_raw, deadline, stop.should_stop,
            )
        reward_items = bool(reward_item_matches)
        reward_signature = None
        if cls.page is not None and cls.page.page_id == "reward_popup":
            # Hash only the reward body plus matched item identities. This lets
            # us reject a stale, unchanged popup after a close input while still
            # allowing distinct consecutive rewards in the same run.
            body = np.ascontiguousarray(image[800:1080, 100:980]).tobytes()
            reward_signature = hashlib.sha256(
                body + "|".join(sorted(reward_item_matches)).encode("utf-8")
            ).hexdigest()
        if cls.page is not None and cls.page.page_id == "mail_inbox" and empty_state_matcher:
            outcome = backend.recognize(
                image, empty_state_matcher.template, empty_state_matcher.roi,
                empty_state_matcher.threshold, deadline, stop.should_stop,
            )
            empty_state = bool(outcome.hit)
        page_id = cls.page.page_id if cls.page else None
        observation = BulkObservation(
            page_id=page_id,
            claimable=cls.has_claimable,
            no_pending=empty_state and not cls.has_claimable,
            reward_items=reward_items,
            reward_item_matches=tuple(reward_item_matches),
            reward_signature=reward_signature,
            blocker=cls.blockers[0].page_id if cls.blockers else None,
            ambiguous=bool(cls.ambiguous_pages),
        )
        return cls, observation, reward_items

    def finish(status: RunStatus, reason: ReasonCode, message: str = "") -> TaskResult:
        evidence_paths = [str(before_path)]
        if last_after_path is not None:
            evidence_paths.append(str(last_after_path))
        evidence_paths.extend([str(workspace.events_path), str(workspace.result_path)])
        detail = {
            **action_counts,
            "claim_request_submitted": claim_request_submitted,
            "claim_verified": claim_verified,
            "reward_evidence_complete": reward_evidence_complete,
            "unverified_reward_popup_count": unverified_reward_popup_count,
            "cleanup_only": cleanup_only,
            "reward_popup_count": reward_popup_count,
            "post_batch_state_verified": post_batch_state_verified,
            "cleanup_verified": cleanup_verified,
            "final_page_id": final_page_id,
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
            "reward_evidence_complete": reward_evidence_complete,
            "unverified_reward_popup_count": unverified_reward_popup_count,
            "cleanup_only": cleanup_only,
            "post_batch_state_verified": post_batch_state_verified,
            "cleanup_verified": cleanup_verified,
            "final_page_id": final_page_id,
            "action_count_is_upper_bound": True,
            "input_outcome_uncertain": True,
            "before": str(before_path),
            "after": str(last_after_path) if last_after_path is not None else None,
        }

    try:
        cls, current, _ = observe(current_image)
    except (MaaStopped, MaaTimeout):
        raise
    final_page_id = current.page_id
    # Recover an existing overlay without turning recovery into a new claim.
    # Such a run cannot certify the preceding run's reward outcome.
    cleanup_only = current.page_id == "reward_popup"
    log("classify", "mail_bulk_initial_state", {
        "page_id": current.page_id, "claimable": current.claimable,
        "no_pending": current.no_pending, "blocker": current.blocker,
    })

    if request.mode is RunMode.DRY_RUN:
        step = choose_bulk_step(current, False)
        control_name = {
            "open_more": "more", "open_mail": "mail", "claim_all": "bulk_read",
            "close_mail": "mail_close", "dismiss_reward": "reward_close",
            "dismiss_initial_reward": "reward_close", "close_detail": "detail_close",
        }.get(step)
        candidate = None
        if control_name:
            raw = controls.get(control_name, {})
            verified = bool(raw.get("verified", False))
            candidate = CandidateAction(
                action_id=control_name,
                description=str(raw.get("description", control_name)),
                verified=verified,
                blocked_reason=None if verified else "该动作素材尚未验证",
            )
        inspection = InspectionResult(
            run_id=run_id, task_id=request.task_id, account_alias=request.profile.account_alias,
            device_serial=request.profile.serial, observed_at=utc_now_iso(), page_id=current.page_id,
            observations=list(cls.observations) + list(cls.claimable) + list(cls.already_claimed),
            candidate_actions=[candidate] if candidate else [],
            blockers=blocker_notes(cls),
            evidence_paths=[str(before_path), str(workspace.events_path), str(workspace.inspection_path)],
            notes=["批量邮件 dry-run 只分析当前画面；没有导航、领取或关闭输入。"],
        )
        workspace.write_json(workspace.inspection_path, inspection.to_dict())
        log("inspect", "mail_bulk_dry_run", {"page_id": current.page_id, "next_step": step})
        return inspection

    def screenshot() -> tuple[np.ndarray, Classification, BulkObservation, bool]:
        nonlocal last_after_path
        image = backend.screencap(deadline, stop.should_stop)
        cls_after, obs, items = observe(image)
        workspace.save_image(f"step-{seq:04d}", image)
        if sum(action_counts.values()) > 0:
            last_after_path = workspace.save_image("after", image)
        return image, cls_after, obs, items

    def click_control(name: str, counter: str, on_submit: Callable[[], None] | None = None) -> bool:
        nonlocal current_image, input_error, input_error_message
        input_error = None
        input_error_message = None
        raw = controls.get(name)
        if not isinstance(raw, dict) or not raw.get("verified"):
            input_error = "unverified"
            log(name, "control_unverified", {})
            return False
        m = _matcher(raw)
        try:
            outcome = backend.recognize(current_image, m.template, m.roi, m.threshold, deadline, stop.should_stop)
        except MaaStopUnconfirmed as exc:
            attach_stop_context(exc)
            raise
        except MaaStopped as exc:
            input_error, input_error_message = "stopped", str(exc)
            log(name, "control_recognition_stopped", {"template": m.template, "message": str(exc)})
            return False
        except MaaTimeout as exc:
            input_error, input_error_message = "timeout", str(exc)
            log(name, "control_recognition_timeout", {"template": m.template, "message": str(exc)})
            return False
        except MaaError as exc:
            input_error, input_error_message = "sdk", str(exc)
            log(name, "control_recognition_failed", {"template": m.template, "message": str(exc)})
            return False
        if not outcome.hit or outcome.center is None:
            input_error = "missing"
            log(name, "control_not_found", {"template": m.template, "score": outcome.score})
            return False
        if sum(action_counts.values()) >= request.max_actions:
            input_error = "budget"
            log(name, "action_budget_exhausted", {"max_actions": request.max_actions})
            return False
        if stop.should_stop() or time.monotonic() >= deadline:
            input_error = "stopped" if stop.should_stop() else "timeout"
            log(name, "input_prevented", {"stop_requested": stop.should_stop()})
            return False
        x, y = outcome.center
        if on_submit is not None:
            on_submit()
        action_counts[counter] += 1
        log(name, "input_submitted", {"x": x, "y": y, "template": m.template})
        try:
            succeeded = bool(backend.click(x, y, deadline, stop.should_stop))
        except MaaStopUnconfirmed as exc:
            attach_stop_context(exc)
            raise
        except MaaStopped:
            input_error = "stopped"
            return False
        except MaaTimeout:
            input_error = "timeout"
            return False
        except MaaError:
            input_error = "sdk"
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

    def refresh(
        wait_page_ids: set[str], *, wait_for_claim_transition: bool = False,
        wait_for_reward_change_from: str | None = None,
    ) -> tuple[Classification, BulkObservation, bool]:
        nonlocal current_image, final_page_id
        end = min(deadline, time.monotonic() + float(config.get("transition_wait_seconds", 8)))
        while True:
            if stop.should_stop():
                raise MaaStopped("批量邮件步骤后收到停止请求")
            current_image, cls_after, obs, items = screenshot()
            final_page_id = obs.page_id
            log("verify", "mail_bulk_transition_observed", {
                "page_id": obs.page_id, "claimable": obs.claimable,
                "no_pending": obs.no_pending, "reward_items": items,
                "reward_item_matches": list(obs.reward_item_matches),
            })
            partial_reward_transition = (
                obs.ambiguous
                and "reward_popup" in cls_after.ambiguous_pages
                and set(cls_after.ambiguous_pages).issubset({"reward_popup", "mail_inbox"})
            )
            if (wait_for_reward_change_from is not None and not obs.blocker
                    and (partial_reward_transition or (
                        obs.page_id == "reward_popup"
                        and obs.reward_signature == wait_for_reward_change_from
                    ))):
                remaining = min(end, deadline) - time.monotonic()
                if remaining > 0:
                    log("verify", "reward_transition_pending", {
                        "page_id": obs.page_id,
                        "partial_foreground": partial_reward_transition,
                        "reward_item_matches": list(obs.reward_item_matches),
                        "remaining_seconds": round(remaining, 3),
                    })
                    stop.wait(min(0.25, remaining))
                    if stop.should_stop():
                        raise MaaStopped("奖励关闭后等待画面过渡时收到停止请求")
                    if time.monotonic() >= deadline:
                        raise MaaTimeout("等待奖励关闭后的画面过渡超过整轮截止时间")
                    continue
                return cls_after, obs, items
            if wait_for_claim_transition and partial_reward_transition and not obs.blocker:
                # 一键阅读后的第一张奖励弹窗仍在动画中：只读等待，不授权任何输入。
                remaining = min(end, deadline) - time.monotonic()
                if remaining > 0:
                    log("verify", "claim_reward_transition_pending", {
                        "ambiguous_pages": list(cls_after.ambiguous_pages),
                        "remaining_seconds": round(remaining, 3),
                    })
                    stop.wait(min(0.25, remaining))
                    if stop.should_stop():
                        raise MaaStopped("等待一键阅读奖励弹窗动画时收到停止请求")
                    if time.monotonic() >= deadline:
                        raise MaaTimeout("等待一键阅读奖励弹窗动画超过整轮截止时间")
                    continue
                return cls_after, obs, items
            if obs.blocker or obs.ambiguous or obs.page_id not in wait_page_ids:
                # 其他未知页面不授权输入；已知页面可只读等待过渡。
                if not obs.blocker and not obs.ambiguous and obs.page_id not in wait_page_ids and obs.page_id is not None and time.monotonic() < end:
                    stop.wait(min(0.25, max(0.0, end - time.monotonic())))
                    continue
                return cls_after, obs, items
            if wait_for_claim_transition and obs.page_id == "mail_inbox" and obs.claimable:
                if time.monotonic() < end:
                    remaining = min(end, deadline) - time.monotonic()
                    if remaining > 0:
                        stop.wait(min(0.25, remaining))
                        if stop.should_stop():
                            raise MaaStopped("等待一键阅读结果过渡时收到停止请求")
                        if time.monotonic() >= deadline:
                            raise MaaTimeout("等待一键阅读结果超过整轮截止时间")
                    continue
            return cls_after, obs, items

    def refresh_or_finish(
        wait_page_ids: set[str], *, wait_for_claim_transition: bool = False,
        wait_for_reward_change_from: str | None = None,
    ) -> tuple[Classification, BulkObservation, bool] | TaskResult:
        try:
            outcome = refresh(
                wait_page_ids,
                wait_for_claim_transition=wait_for_claim_transition,
                wait_for_reward_change_from=wait_for_reward_change_from,
            )
            observed_cls, observed, _ = outcome
            # 阻断提示可与背景页面同时命中，不能仅凭背景主城确认终态。
            if observed.blocker or observed.ambiguous:
                return finish(
                    RunStatus.NEEDS_ATTENTION, blocker_reason(observed_cls),
                    blocker_message(observed_cls, "动作后出现阻断提示或页面身份仍不完整；不授权后续输入或收尾成功"),
                )
            return outcome
        except MaaStopUnconfirmed as exc:
            attach_stop_context(exc)
            raise
        except MaaStopped as exc:
            return finish(RunStatus.CANCELLED, ReasonCode.USER_CANCELLED,
                          f"动作后收到停止；已记录输入数，当前步骤未完成：{exc}")
        except MaaTimeout as exc:
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT,
                          f"共享截止时间内未确认动作后状态；已记录输入数：{exc}")
        except MaaError as exc:
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                          f"动作后识别/截图失败；已记录输入数：{exc}")

    page_cls = cls
    # Full route: More, Mail, one batch submission, up to N reward dismissals,
    # then the mail X. This bounds state transitions as well as inputs.
    for _ in range(4 + max_reward_popups):
        if stop.should_stop():
            return finish(RunStatus.CANCELLED, ReasonCode.USER_CANCELLED, "停止请求；未提交后续输入")
        step = choose_bulk_step(current, claim_request_submitted, claim_verified)
        if cleanup_only and not current.blocker and not current.ambiguous:
            if current.page_id == "mail_inbox":
                step = "close_mail"
        if step in {"stop_blocker", "stop_unknown"}:
            return finish(RunStatus.NEEDS_ATTENTION, blocker_reason(page_cls),
                          blocker_message(page_cls, f"当前页面不可安全继续：{step}"))
        if step == "stop_uncertain":
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                          "已提交批量请求或列表状态不足以证明无奖励；不会再次点击一键阅读")

        if step == "open_more":
            if not page_cls.page or not page_cls.page.verified:
                return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.ASSETS_UNVERIFIED, "主城页面身份尚未验证")
            if not click_control("more", "navigation_action_count"):
                return input_failure(ReasonCode.ASSETS_UNVERIFIED, "主城更多入口未能验证或点击")
            outcome = refresh_or_finish({"more_menu"})
            if isinstance(outcome, TaskResult):
                return outcome
            page_cls, current, _ = outcome
            if current.page_id != "more_menu":
                return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.POSTCONDITION_NOT_MET,
                              "点击更多后没有确认菜单展开；不会重复点击")
            continue

        if step == "open_mail":
            if not page_cls.page or not page_cls.page.verified:
                return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.ASSETS_UNVERIFIED, "更多菜单身份尚未验证")
            if not click_control("mail", "navigation_action_count"):
                return input_failure(ReasonCode.ASSETS_UNVERIFIED, "更多菜单邮件入口未能验证或点击")
            outcome = refresh_or_finish({"mail_inbox"})
            if isinstance(outcome, TaskResult):
                return outcome
            page_cls, current, _ = outcome
            if current.page_id != "mail_inbox":
                return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.POSTCONDITION_NOT_MET,
                              "点击邮件后没有确认系统邮件页；不会重复点击")
            continue

        if step == "claim_all":
            if not page_cls.page or not page_cls.page.verified:
                return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.ASSETS_UNVERIFIED, "系统邮件页身份素材未验证")
            if not current.claimable:
                return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN, "未识别待处理邮件")
            # 设置提交标志紧邻输入 API，SDK 失败时也绝不盲目重试。
            def mark_claim_submitted() -> None:
                nonlocal claim_request_submitted
                claim_request_submitted = True

            if not click_control("bulk_read", "claim_action_count", mark_claim_submitted):
                return input_failure(ReasonCode.OUTCOME_UNCERTAIN,
                                     "一键阅读按钮未能安全提交，或提交结果不确定；不会重试")
            outcome = refresh_or_finish({"reward_popup", "mail_inbox"}, wait_for_claim_transition=True)
            if isinstance(outcome, TaskResult):
                return outcome
            page_cls, current, reward_items = outcome
            if current.page_id == "reward_popup" and reward_items and not current.claimable:
                claim_verified = True
                log("verify", "bulk_reward_confirmed", {
                    "matched_reward_items": list(current.reward_item_matches),
                })
            elif current.page_id == "mail_inbox" and current.no_pending and not current.claimable:
                return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                              "邮件状态发生变化但未出现已验证奖励内容；不能确认附件已领取")
            elif current.page_id != "reward_popup":
                return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                              "批量结果未出现已验证奖励弹窗；不会再次提交")
            continue

        if step in {"dismiss_reward", "dismiss_initial_reward"}:
            if not page_cls.page or not page_cls.page.verified:
                return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.ASSETS_UNVERIFIED,
                              "奖励弹窗身份尚未验证；不能授权关闭")
            if (last_closed_reward_signature is not None
                    and current.reward_signature == last_closed_reward_signature):
                return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.POSTCONDITION_NOT_MET,
                              "关闭后仍是同一奖励画面；无法确认出现了下一张弹窗，不重复点击")
            if reward_popup_count >= max_reward_popups:
                return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.BUDGET_EXHAUSTED,
                              f"本轮奖励弹窗数量达到安全上限 {max_reward_popups}；保留当前画面，不再点击")
            reward_popup_count += 1
            if not current.reward_items:
                reward_evidence_complete = False
                unverified_reward_popup_count += 1
                log("reward_popup", "reward_content_unconfirmed", {
                    "popup_index": reward_popup_count,
                    "message": "已确认可关闭的奖励弹窗，物品内容未确认；只收尾，不证明领取成功",
                })
            log("reward_popup", "reward_popup_authorized", {
                "popup_index": reward_popup_count,
                "matched_reward_items": list(current.reward_item_matches),
                "reward_content_verified": current.reward_items,
                "max_reward_popups": max_reward_popups,
            })
            last_closed_reward_signature = current.reward_signature
            if not click_control("reward_close", "dismiss_action_count"):
                return input_failure(ReasonCode.ASSETS_UNVERIFIED, "奖励弹窗关闭控件未验证")
            reward_wait_pages = {"reward_popup", "mail_inbox"}
            if not claim_request_submitted:
                reward_wait_pages.update({"mail_detail", "main_city"})
            outcome = refresh_or_finish(
                reward_wait_pages,
                wait_for_reward_change_from=current.reward_signature,
            )
            if isinstance(outcome, TaskResult):
                return outcome
            page_cls, current, _ = outcome
            if claim_request_submitted and current.page_id == "mail_inbox":
                post_batch_state_verified = current.no_pending and not current.claimable
            if current.page_id == "main_city":
                cleanup_verified = True
                if claim_verified and reward_evidence_complete and post_batch_state_verified:
                    return finish(RunStatus.COMPLETED, ReasonCode.CLAIM_CONFIRMED,
                                  "批量奖励已确认，奖励关闭后已返回主城")
                if claim_verified:
                    return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                                  "已返回主城，但奖励内容或批后邮件状态证据不完整")
                return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                              "关闭了运行前已有的奖励弹窗并返回主城；该弹窗不构成本轮领取成功")
            continue

        if step == "close_detail":
            if not page_cls.page or not page_cls.page.verified or not click_control("detail_close", "dismiss_action_count"):
                return input_failure(ReasonCode.ASSETS_UNVERIFIED, "邮件详情关闭控件未验证")
            outcome = refresh_or_finish({"mail_inbox"})
            if isinstance(outcome, TaskResult):
                return outcome
            page_cls, current, _ = outcome
            if current.page_id != "mail_inbox":
                return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.POSTCONDITION_NOT_MET,
                              "关闭详情后没有回到系统邮件列表；不重复点击")
            continue

        if step == "close_mail":
            if not claim_request_submitted and not cleanup_only and current.no_pending:
                no_reward_proven = True
            if not page_cls.page or not page_cls.page.verified or not click_control("mail_close", "dismiss_action_count"):
                return input_failure(ReasonCode.ASSETS_UNVERIFIED, "邮件窗口关闭控件未验证")
            outcome = refresh_or_finish({"main_city"})
            if isinstance(outcome, TaskResult):
                return outcome
            page_cls, current, _ = outcome
            if claim_request_submitted and current.page_id == "mail_inbox":
                post_batch_state_verified = current.no_pending and not current.claimable
            if current.page_id == "main_city":
                cleanup_verified = True
                if cleanup_only:
                    return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                                  "已自动关闭运行前的奖励弹窗及邮件并返回主城；未提交新领取，不确认此前领取结果")
                if claim_verified and reward_evidence_complete and post_batch_state_verified:
                    return finish(RunStatus.COMPLETED, ReasonCode.CLAIM_CONFIRMED, "批量领取与返回主城均已确认")
                if claim_verified:
                    return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                                  "已返回主城，但奖励内容或批后邮件状态证据不完整")
                if no_reward_proven:
                    return finish(RunStatus.NOTHING_TO_CLAIM, ReasonCode.NOTHING_TO_CLAIM, "邮件列表无待处理奖励并已返回主城")
                return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                              "返回主城已确认，但领取结果未能证明")
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.POSTCONDITION_NOT_MET,
                          "关闭邮件后没有确认返回主城；不重复点击")

    return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.BUDGET_EXHAUSTED, "批量邮件状态步数达到保护上限")
