"""受限日常奖励流程。每个动作都由当前页面与当前控件识别共同授权。"""

from __future__ import annotations

import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np

from .classification import Classification, blocker_message, blocker_notes, blocker_reason, classify
from .contracts import (
    CandidateAction, InspectionResult, ReasonCode, RunMode, RunStatus, TaskRequest, TaskResult,
)
from .evidence import RunWorkspace, utc_now_iso
from .maa_adapter import Backend, MaaError, MaaStopUnconfirmed, MaaStopped, MaaTimeout
from .pages import PageCatalog, Matcher


def _control_matcher(raw: dict[str, Any]) -> Matcher:
    roi = raw.get("roi", [0, 0, 0, 0])
    return Matcher(
        template=str(raw["template"]),
        threshold=float(raw.get("threshold", 0.9)),
        roi=tuple(int(value) for value in roi),
        verified=bool(raw.get("verified", False)),
    )


def run_daily_rewards(
    *, request: TaskRequest, backend: Backend, catalog: PageCatalog,
    config: dict[str, Any], workspace: RunWorkspace, run_id: str, started_at: str,
    initial_image: np.ndarray, before_path: Path, seq: int, deadline: float,
    stop: Any,
) -> TaskResult | InspectionResult:
    """Claim only the registered completed daily rewards, then safely clean up.

    The executor follows the native batch button through a bounded green-to-gray
    loop. Every submission must produce a newly observed reward foreground,
    which is closed by its verified hint before a fresh daily-page button state
    can authorize another round. It never activates row-level tasks. Starting
    on a reward popup is cleanup only and cannot enter the claim path.
    """
    controls = config.get("controls", {})
    action_counts = {
        "navigation_action_count": 0,
        "claim_action_count": 0,
        "dismiss_action_count": 0,
        "scroll_action_count": 0,
    }
    current_image = initial_image
    final_page_id: str | None = None
    after_path: Path | None = None
    observed_paths = [str(before_path)]
    project_ledger: list[dict[str, Any]] = []
    chest_status: list[dict[str, Any]] = []
    daily_project_status: dict[str, dict[str, Any]] = {}
    reward_item_matches: list[str] = []
    coverage: dict[str, Any] = {"complete": False, "scanned_pages": 0, "boundary_verified": False}
    cleanup_only = False
    cleanup_verified = False
    input_uncertain = False
    claim_request_submitted = False
    claim_verified = False
    post_batch_state_verified = False
    announcement_dismiss_attempted = False
    announcement_dismissed = False
    reward_popup_count = 0
    unverified_reward_popup_count = 0
    batch_rounds: list[dict[str, Any]] = []
    no_reward_proven = False
    pre_project_status: dict[str, dict[str, Any]] = {}
    last_result: TaskResult | None = None
    capture_index = 0

    def terminal_rounds() -> list[dict[str, Any]]:
        # Normalize before persisting; detach the terminal snapshot from live records.
        for item in batch_rounds:
            if item.get("submission") in {"pending", "submitted"}:
                item["submission"] = "uncertain"
        return deepcopy(batch_rounds)

    def log(node: str, event: str, detail: dict[str, Any] | None = None) -> None:
        nonlocal seq
        workspace.log_event(seq, node, event, detail or {})
        seq += 1

    def finish(status: RunStatus, reason: ReasonCode, message: str) -> TaskResult:
        nonlocal last_result
        # A terminal stop/error must survive subsequent cleanup branches.
        if last_result is not None:
            return last_result
        rounds_snapshot = terminal_rounds()
        detail = {
            **action_counts,
            "cleanup_only": cleanup_only,
            "cleanup_verified": cleanup_verified,
            "final_page_id": final_page_id,
            "project_ledger": project_ledger,
            "daily_project_status": daily_project_status,
            "claim_request_submitted": claim_request_submitted,
            "batch_rounds": rounds_snapshot,
            "confirmed_batch_round_count": sum(
                1 for item in rounds_snapshot if item.get("submission") == "confirmed"
            ),
            "uncertain_batch_round_count": sum(
                1 for item in rounds_snapshot if item.get("submission") != "confirmed"
            ),
            "pending_batch_round_count": 0,
            "max_batch_rounds": int(config.get("max_batch_rounds", 5)),
            "claim_verified": claim_verified,
            "post_batch_state_verified": post_batch_state_verified,
            "announcement_dismiss_attempted": announcement_dismiss_attempted,
            "announcement_dismissed": announcement_dismissed,
            "reward_popup_count": reward_popup_count,
            "unverified_reward_popup_count": unverified_reward_popup_count,
            "reward_item_matches": reward_item_matches,
            "chest_status": chest_status,
            "coverage": coverage,
            "input_outcome_uncertain": input_uncertain,
            "action_count_is_upper_bound": input_uncertain,
            "before": str(before_path),
            "after": str(after_path) if after_path else None,
            "screenshots": list(observed_paths),
            "message": message,
        }
        paths = list(observed_paths)
        paths.extend([str(workspace.events_path), str(workspace.result_path)])
        result = TaskResult(
            run_id=run_id, task_id=request.task_id, account_alias=request.profile.account_alias,
            device_serial=request.profile.serial, status=status, reason_code=reason,
            started_at=started_at, finished_at=utc_now_iso(), action_count=sum(action_counts.values()),
            evidence_paths=paths, mode=request.mode, detail=deepcopy(detail),
        )
        workspace.write_json(workspace.result_path, result.to_dict())
        log("finish", "run_finished", {"status": str(status), "reason": str(reason)})
        last_result = result
        return result

    def attach_context(exc: MaaStopUnconfirmed) -> None:
        rounds_snapshot = terminal_rounds()
        exc.run_context = deepcopy({
            **action_counts,
            "action_count": sum(action_counts.values()),
            "cleanup_only": cleanup_only,
            "cleanup_verified": cleanup_verified,
            "final_page_id": final_page_id,
            "input_outcome_uncertain": True,
            "action_count_is_upper_bound": True,
            "project_ledger": project_ledger,
            "daily_project_status": daily_project_status,
            "claim_request_submitted": claim_request_submitted,
            "batch_rounds": rounds_snapshot,
            "confirmed_batch_round_count": sum(
                item.get("submission") == "confirmed" for item in rounds_snapshot
            ),
            "uncertain_batch_round_count": sum(
                item.get("submission") != "confirmed" for item in rounds_snapshot
            ),
            "pending_batch_round_count": 0,
            "max_batch_rounds": int(config.get("max_batch_rounds", 5)),
            "claim_verified": claim_verified,
            "post_batch_state_verified": post_batch_state_verified,
            "announcement_dismiss_attempted": announcement_dismiss_attempted,
            "announcement_dismissed": announcement_dismissed,
            "reward_popup_count": reward_popup_count,
            "unverified_reward_popup_count": unverified_reward_popup_count,
            "chest_status": chest_status,
            "coverage": coverage,
            "before": str(before_path),
            "after": str(after_path) if after_path else None,
        })

    def observe(image: np.ndarray) -> tuple[Classification, dict[str, bool]]:
        nonlocal daily_project_status, reward_item_matches
        cls = classify(backend, image, catalog, deadline, stop.should_stop)
        control_hits: dict[str, bool] = {}
        # Background controls may remain visible under overlays. They are only
        # interpreted after a page identity has been established.
        if cls.page is not None and not cls.blockers and not cls.ambiguous_pages:
            for name, raw in controls.items():
                if not isinstance(raw, dict) or not raw.get("verified") or "template" not in raw:
                    control_hits[name] = False
                    continue
                matcher = _control_matcher(raw)
                outcome = backend.recognize(
                    image, matcher.template, matcher.roi, matcher.threshold, deadline, stop.should_stop,
                )
                control_hits[name] = bool(outcome.hit and outcome.center is not None)
        daily_project_status = {}
        # Task names/rows are intentionally not gates or success criteria for
        # the native one-click workflow. The freshly identified batch control
        # alone determines green, gray, conflict, or unknown state.
        reward_item_matches = []
        if cls.page is not None and cls.page.page_id == "daily_reward_popup" and not cls.blockers and not cls.ambiguous_pages:
            for item in config.get("reward_items", []):
                if not isinstance(item, dict) or not item.get("verified"):
                    continue
                outcome = backend.recognize(
                    image, str(item["template"]), tuple(item.get("roi", [0, 0, 0, 0])),
                    float(item.get("threshold", 0.97)), deadline, stop.should_stop,
                )
                if outcome.hit:
                    reward_item_matches.append(str(item.get("reward_id", item["template"])))
        return cls, control_hits

    try:
        cls, control_hits = observe(current_image)
    except MaaStopped:
        raise
    except MaaTimeout:
        raise
    final_page_id = cls.page.page_id if cls.page else None
    log("classify", "daily_rewards_initial_state", {
        "page_id": final_page_id,
        "blockers": [item.page_id for item in cls.blockers],
        "ambiguous": cls.ambiguous_pages,
        "control_hits": control_hits,
        "daily_project_status": daily_project_status,
        "reward_item_matches": reward_item_matches,
    })

    if request.mode is RunMode.DRY_RUN:
        candidates: list[CandidateAction] = []
        entry_control = {
            "main_city": ("tasks_entry", "open_daily_tasks"),
            "daily_hub": ("daily_tab", "open_daily_rewards"),
        }.get(cls.page.page_id if cls.page else "")
        if cls.page and entry_control and entry_control[0] in controls:
            control_name, action_id = entry_control
            raw = controls[control_name]
            verified = bool(raw.get("verified"))
            matched = control_hits.get(control_name, False)
            candidates.append(CandidateAction(
                action_id=action_id,
                description=str(raw.get("description", "打开主城任务入口")),
                verified=verified,
                blocked_reason=None if verified and matched else "当前入口未同时通过素材验证和当帧识别",
            ))
        if cls.page and cls.page.page_id == "daily_tasks":
            raw = controls.get("daily_claim_all", {})
            green = control_hits.get("daily_claim_all", False)
            gray = control_hits.get("daily_claim_all_disabled", False)
            ready = (
                not cls.blockers and not cls.ambiguous_pages
                and bool(raw.get("verified")) and green and not gray
            )
            candidates.append(CandidateAction(
                action_id="claim_daily_all",
                description="按页面当前绿色一键领取状态处理；每轮只点一次，不逐项领取或执行‘前往’",
                verified=ready,
                blocked_reason=None if ready else "需要日常页身份明确，且顶部一键领取绿色控件当帧命中、灰色控件未命中",
            ))
        blockers = blocker_notes(cls)
        if cls.ambiguous_pages:
            blockers.append(f"页面身份不唯一：{', '.join(cls.ambiguous_pages)}")
        if cls.page is None and not blockers:
            blockers.append("当前画面不是已登记页面；只读检查不导航")
        inspection = InspectionResult(
            run_id=run_id, task_id=request.task_id, account_alias=request.profile.account_alias,
            device_serial=request.profile.serial, observed_at=utc_now_iso(), page_id=final_page_id,
            observations=list(cls.observations), candidate_actions=candidates, blockers=blockers,
            evidence_paths=[str(before_path), str(workspace.events_path), str(workspace.inspection_path)],
            notes=["日常奖励 dry-run 只分析当前画面，无点击、滑动、按键或启动应用。",
                   ("当前为已登记日常任务页；记录今日完成计数但不将其作为领取成功依据。"
                    if final_page_id == "daily_tasks" else "当前页不在可领取状态范围；未列出或领取任何项目。")],
        )
        workspace.write_json(workspace.inspection_path, inspection.to_dict())
        log("inspect", "daily_rewards_dry_run", {"page_id": final_page_id, "input_count": 0})
        return inspection

    if stop.should_stop():
        return finish(RunStatus.CANCELLED, ReasonCode.USER_CANCELLED, "停止请求；未发出输入")

    def fresh_capture(stage: str) -> bool:
        nonlocal current_image, after_path, cls, control_hits, final_page_id, input_uncertain, capture_index
        if last_result is not None:
            return False
        if stop.should_stop():
            finish(RunStatus.CANCELLED, ReasonCode.USER_CANCELLED, f"{stage} 前收到停止请求")
            return False
        if time.monotonic() >= deadline:
            finish(RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT, f"{stage} 前整轮截止时间已到")
            return False
        try:
            current_image = backend.screencap(deadline, stop.should_stop)
            capture_index += 1
            name = "after" if capture_index == 1 else f"after_{capture_index}"
            after_path = workspace.save_image(name, current_image)
            observed_paths.append(str(after_path))
            cls, control_hits = observe(current_image)
        except MaaStopUnconfirmed as exc:
            attach_context(exc)
            raise
        except MaaStopped:
            finish(RunStatus.CANCELLED, ReasonCode.USER_CANCELLED,
                   f"{stage} 后重新截图时停止；保留已提交输入与证据")
            return False
        except MaaTimeout:
            input_uncertain = True
            finish(RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT,
                   f"{stage} 后截图或识别超时；保留已提交输入计数")
            return False
        except MaaError as exc:
            input_uncertain = True
            finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                   f"{stage} 后截图或识别失败：{exc}")
            return False
        final_page_id = cls.page.page_id if cls.page else None
        log(stage, "daily_rewards_state_observed", {
            "page_id": final_page_id,
            "blockers": [item.page_id for item in cls.blockers],
            "ambiguous": cls.ambiguous_pages,
            "control_hits": control_hits,
            "after": str(after_path),
        })
        return True

    def submit_input(
        action_id: str, raw: dict[str, Any], counter: str, *,
        swipe: dict[str, Any] | None = None, before_submit: Any = None,
    ) -> bool:
        nonlocal input_uncertain
        if last_result is not None:
            return False
        if sum(action_counts.values()) >= request.max_actions:
            finish(RunStatus.NEEDS_ATTENTION, ReasonCode.ACTION_BUDGET_EXHAUSTED,
                   "剩余动作预算不足；保留当前画面")
            return False
        if stop.should_stop() or time.monotonic() >= deadline:
            finish(RunStatus.CANCELLED if stop.should_stop() else RunStatus.NEEDS_ATTENTION,
                   ReasonCode.USER_CANCELLED if stop.should_stop() else ReasonCode.TIMEOUT,
                   "输入提交前停止或整轮截止时间到")
            return False
        if swipe is not None:
            gesture = swipe
            action_counts[counter] += 1
            log(action_id, "input_submitted", {"gesture": gesture, "source_page": final_page_id})
            try:
                succeeded = backend.swipe(
                    gesture["x1"], gesture["y1"], gesture["x2"], gesture["y2"],
                    gesture["duration_ms"], deadline, stop.should_stop,
                )
            except MaaStopUnconfirmed as exc:
                input_uncertain = True
                attach_context(exc)
                raise
            except MaaStopped:
                finish(RunStatus.CANCELLED, ReasonCode.USER_CANCELLED,
                       "滚动停止；保留已提交输入计数")
                return False
            except MaaTimeout:
                input_uncertain = True
                finish(RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT,
                       "滚动超时；输入结果不确定，不重试")
                return False
            except MaaError as exc:
                input_uncertain = True
                finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                       f"滚动 SDK 结果不确定：{exc}")
                return False
        else:
            matcher = _control_matcher(raw)
            try:
                target = backend.recognize(current_image, matcher.template, matcher.roi,
                                           matcher.threshold, deadline, stop.should_stop)
            except MaaStopUnconfirmed as exc:
                attach_context(exc)
                raise
            except MaaStopped:
                finish(RunStatus.CANCELLED, ReasonCode.USER_CANCELLED,
                       "点击前目标复核停止；保留此前输入和轮次，未提交本次点击")
                return False
            except MaaTimeout:
                finish(RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT,
                       "点击前目标复核超时；保留此前输入和轮次，未提交本次点击")
                return False
            except MaaError as exc:
                finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                       f"点击前目标复核失败：{exc}；保留此前输入和轮次，未提交本次点击")
                return False
            if not target.hit or target.center is None:
                finish(RunStatus.NEEDS_ATTENTION, ReasonCode.ASSETS_UNVERIFIED,
                       f"{action_id} 点击前目标未命中；未发出输入")
                return False
            if stop.should_stop() or time.monotonic() >= deadline:
                finish(RunStatus.CANCELLED if stop.should_stop() else RunStatus.NEEDS_ATTENTION,
                       ReasonCode.USER_CANCELLED if stop.should_stop() else ReasonCode.TIMEOUT,
                       "点击提交前停止或整轮截止时间到")
                return False
            x, y = target.center
            if before_submit is not None:
                before_submit()
            action_counts[counter] += 1
            log(action_id, "input_submitted", {"x": x, "y": y, "template": matcher.template,
                                                "source_page": final_page_id})
            try:
                succeeded = backend.click(x, y, deadline, stop.should_stop)
            except MaaStopUnconfirmed as exc:
                input_uncertain = True
                attach_context(exc)
                raise
            except MaaStopped:
                finish(RunStatus.CANCELLED, ReasonCode.USER_CANCELLED,
                       f"{action_id} 点击停止；保留已提交输入计数")
                return False
            except MaaTimeout:
                input_uncertain = True
                finish(RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT,
                       f"{action_id} 点击超时；输入结果不确定，不重试")
                return False
            except MaaError as exc:
                input_uncertain = True
                finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                       f"{action_id} 点击 SDK 结果不确定：{exc}")
                return False
        if not succeeded:
            input_uncertain = True
            finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                   f"{action_id} 输入没有确认成功，不重试")
            return False
        return fresh_capture(action_id)

    def dismiss_known_announcement() -> bool:
        """Dismiss only the sampled attack-order notice via its verified blank target."""
        nonlocal announcement_dismiss_attempted, announcement_dismissed
        if not cls.blockers:
            return True
        blocker_ids = [item.page_id for item in cls.blockers]
        known_announcement_ids = {"unrelated_announcement", "unrelated_announcement_defense"}
        if (announcement_dismiss_attempted or len(blocker_ids) != 1
                or blocker_ids[0] not in known_announcement_ids
                or cls.page is None or cls.page.page_id != "daily_tasks"
                or not cls.page.verified or cls.ambiguous_pages):
            return False
        raw = controls.get("dismiss_known_announcement")
        if not isinstance(raw, dict) or not raw.get("verified") or not raw.get("template"):
            finish(RunStatus.NEEDS_ATTENTION, ReasonCode.ASSETS_UNVERIFIED,
                   "攻城令公告已识别，但当前页面没有已验证的空白关闭目标；未点击")
            return False
        announcement_dismiss_attempted = True
        log("dismiss_known_announcement", "announcement_blank_target_selected", {
            "blocker_ids": blocker_ids,
            "underlying_page_id": cls.page.page_id,
            "template": raw["template"],
            "policy": "one_attempt_then_fresh_capture",
        })
        if not submit_input("dismiss_known_announcement", raw, "dismiss_action_count"):
            return False
        announcement_dismissed = not cls.blockers
        log("dismiss_known_announcement", "announcement_dismissal_rechecked", {
            "dismissed": announcement_dismissed,
            "page_id": cls.page.page_id if cls.page else None,
            "blockers": [item.page_id for item in cls.blockers],
        })
        return announcement_dismissed

    def batch_button_state() -> str:
        green = control_hits.get("daily_claim_all", False)
        gray = control_hits.get("daily_claim_all_disabled", False)
        if green and gray:
            return "conflict"
        if green:
            return "green"
        if gray:
            return "gray"
        return "unknown"

    def wait_for_transition(stage: str, predicate: Any, seconds: float) -> bool:
        end = min(deadline, time.monotonic() + max(0.0, seconds))
        while True:
            if last_result is not None:
                return False
            if stop.should_stop():
                finish(RunStatus.CANCELLED, ReasonCode.USER_CANCELLED, f"{stage} 时收到停止；不再输入")
                return False
            if predicate():
                return True
            if cls.blockers:
                return False
            if time.monotonic() >= end:
                return False
            stop.wait(min(0.2, max(0.0, end - time.monotonic())))
            if not fresh_capture(stage):
                return False

    def wait_after_reward_close(stage: str, closed_items: frozenset[str], *,
                                require_button_state: bool = True) -> bool:
        """Wait without input for the daily page or a distinctly identified popup.

        Pixel animation and partial foreground-cue flicker are not transitions.
        Identical/unknown item presentations require an uncovered daily page.
        Only the immediately closed popup matters, so A -> B -> A is valid.
        """
        foreground_exit_seen = False

        def changed() -> bool:
            nonlocal foreground_exit_seen
            if (cls.page is None or not cls.page.verified
                    or cls.blockers or cls.ambiguous_pages):
                return False
            if cls.page.page_id == "daily_tasks":
                reward_cues = [o for o in cls.observations
                               if o.name.startswith("daily_reward_popup:identity:")]
                if not reward_cues or any(o.hit for o in reward_cues):
                    return False
                foreground_exit_seen = True
                return not require_button_state or batch_button_state() in {"green", "gray"}
            if cls.page.page_id == "daily_reward_popup":
                current_items = frozenset(reward_item_matches)
                # Gaining or losing matches alone can be a transient miss or
                # recovery on the same popup. Require a positive replacement.
                distinct_items = bool(closed_items and current_items
                                      and current_items - closed_items and closed_items - current_items)
                return foreground_exit_seen or distinct_items
            return False

        return wait_for_transition(stage, changed, float(config.get("transition_wait_seconds", 8)))

    # A later invocation may be started while an earlier, uncertain claim is
    # still showing its reward popup. Treat this as cleanup-only: no batch
    # claim path is reachable from here, and the result remains uncertain.
    if (cls.page is not None and cls.page.page_id == "daily_reward_popup"
            and not cls.blockers and not cls.ambiguous_pages):
        cleanup_only = True
        max_popups = int(config.get("max_reward_popups", 6))
        while cls.page is not None and cls.page.page_id == "daily_reward_popup":
            if cls.blockers or cls.ambiguous_pages:
                break
            closed_items = frozenset(reward_item_matches)
            close = controls.get("daily_reward_close")
            if (reward_popup_count >= max_popups
                    or not cls.page.verified
                    or not isinstance(close, dict) or not close.get("verified")
                    or not control_hits.get("daily_reward_close", False)):
                break
            reward_popup_count += 1
            if not reward_item_matches:
                unverified_reward_popup_count += 1
            log("daily_reward_popup", "cleanup_only_popup_authorized", {
                "popup_index": reward_popup_count,
                "matched_reward_items": list(reward_item_matches),
                "reward_content_verified": bool(reward_item_matches),
                "claim_action_count": 0,
            })
            if not submit_input("dismiss_daily_reward", close, "dismiss_action_count"):
                break
            if not wait_after_reward_close("cleanup_only_reward_transition", closed_items,
                                           require_button_state=False):
                break

        if (cls.page is not None and cls.page.page_id == "daily_tasks"
                and not cls.blockers and not cls.ambiguous_pages):
            back = controls.get("daily_back")
            if isinstance(back, dict) and back.get("verified") and control_hits.get("daily_back", False):
                if submit_input("close_daily_tasks", back, "dismiss_action_count"):
                    home_end = min(deadline, time.monotonic() + float(config.get("transition_wait_seconds", 8)))
                    while not (cls.page is not None and cls.page.page_id == "main_city"
                               and not cls.blockers and not cls.ambiguous_pages):
                        if cls.blockers or cls.ambiguous_pages or time.monotonic() >= home_end:
                            break
                        stop.wait(min(0.2, max(0.0, home_end - time.monotonic())))
                        if not fresh_capture("cleanup_only_wait_for_main_city"):
                            break
                    cleanup_verified = bool(
                        cls.page is not None and cls.page.page_id == "main_city"
                        and not cls.blockers and not cls.ambiguous_pages
                    )
        if cleanup_verified:
            log("cleanup_only", "safe_cleanup_completed", {
                "claim_action_count": 0,
                "reward_popup_count": reward_popup_count,
                "final_page_id": final_page_id,
                "previous_claim_result_rewritten": False,
            })
        return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                      "仅执行奖励弹窗/日常页安全收尾；领取结果仍不确定，未再次提交领取")

    # A supported transition is one verified action followed by a fresh capture.
    transitions = {
        "main_city": ("tasks_entry", "open_daily_tasks", "daily_hub"),
        "daily_hub": ("daily_tab", "open_daily_rewards", "daily_tasks"),
    }
    while cls.page and cls.page.page_id in transitions:
        if cls.blockers or cls.ambiguous_pages:
            break
        source_page = cls.page.page_id
        control_name, action_id, expected_page = transitions[source_page]
        transitions_left = 2 if source_page == "main_city" else 1
        cleanup_reserve = 1 if isinstance(controls.get("daily_back"), dict) and controls["daily_back"].get("verified") else 0
        if sum(action_counts.values()) + transitions_left + cleanup_reserve > request.max_actions:
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.ACTION_BUDGET_EXHAUSTED,
                          "动作预算不足以完成已知导航并保留回城额度；未继续导航")
        raw = controls.get(control_name)
        if not cls.page.verified or not isinstance(raw, dict) or not raw.get("verified") or not control_hits.get(control_name):
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.ASSETS_UNVERIFIED,
                          f"{source_page} 身份或 {control_name} 当帧识别未通过；未发出输入")
        if not submit_input(action_id, raw, "navigation_action_count"):
            return last_result or finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                                          "导航未能确认；保留动作计数与当前证据")
        transition_deadline = min(deadline, time.monotonic() + float(config.get("transition_wait_seconds", 8)))
        while (cls.page is None or cls.page.page_id != expected_page) and not cls.blockers and not cls.ambiguous_pages:
            if stop.should_stop() or time.monotonic() >= transition_deadline:
                break
            if not fresh_capture(f"wait_for_{expected_page}"):
                return last_result or finish(RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT,
                                              "页面过渡等待结束；保留已提交动作")
        if cls.blockers:
            if cls.blockers[0].page_id in {"unrelated_announcement", "unrelated_announcement_defense"}:
                if not dismiss_known_announcement():
                    if last_result is not None:
                        return last_result
                    return finish(RunStatus.NEEDS_ATTENTION, blocker_reason(cls),
                                  blocker_message(cls, "公告空白区点按后阻断画面仍在或无法确认；停止后续输入"))
                if cls.ambiguous_pages or cls.page is None or cls.page.page_id != expected_page:
                    return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.UNKNOWN_PAGE,
                                  "公告关闭后页面未通过新鲜识别；停止后续输入")
            else:
                return finish(RunStatus.NEEDS_ATTENTION, blocker_reason(cls),
                              blocker_message(cls, "导航后识别到阻断页；停止所有输入"))
        if cls.ambiguous_pages or cls.page is None:
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.UNKNOWN_PAGE,
                          "导航后页面未知或有歧义；已保存新鲜截图并停止")
        if cls.page.page_id != expected_page:
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.PARTIAL_PROGRESS,
                          f"导航后识别到 {cls.page.page_id}，预期 {expected_page}；停止输入")

    if cls.blockers:
        if cls.blockers[0].page_id in {"unrelated_announcement", "unrelated_announcement_defense"}:
            if not dismiss_known_announcement():
                if last_result is not None:
                    return last_result
                return finish(RunStatus.NEEDS_ATTENTION, blocker_reason(cls),
                              blocker_message(cls, "攻城令公告未能通过已验证空白区安全关闭；未继续任务"))
        else:
            return finish(RunStatus.NEEDS_ATTENTION, blocker_reason(cls),
                          blocker_message(cls, "命中阻断页；未继续输入"))
    if cls.blockers:
        return finish(RunStatus.NEEDS_ATTENTION, blocker_reason(cls),
                      blocker_message(cls, "空白区点按后公告仍可见；只允许一次尝试"))
    if cls.ambiguous_pages or cls.page is None:
        return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.UNKNOWN_PAGE,
                      "页面身份未知或有歧义；未继续输入")
    if cls.page.page_id != "daily_tasks" or not cls.page.verified:
        return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.PARTIAL_PROGRESS,
                      f"当前页 {cls.page.page_id} 尚无已验证的日常奖励扫描流程")

    def return_to_home(stage: str) -> bool:
        nonlocal cleanup_verified, final_page_id
        if (cls.page is not None and cls.page.page_id == "main_city"
                and not cls.blockers and not cls.ambiguous_pages):
            cleanup_verified = True
            final_page_id = "main_city"
            return True
        back = controls.get("daily_back")
        if (cls.page is None or cls.page.page_id != "daily_tasks" or cls.blockers or cls.ambiguous_pages
                or not cls.page.verified or not isinstance(back, dict) or not back.get("verified")
                or not control_hits.get("daily_back", False)):
            return False
        if not submit_input("close_daily_tasks", back, "dismiss_action_count"):
            return False
        home_wait = float(config.get("transition_wait_seconds", 8))
        reached = wait_for_transition(
            stage,
            lambda: cls.page is not None and cls.page.page_id == "main_city"
            and not cls.blockers and not cls.ambiguous_pages,
            home_wait,
        )
        cleanup_verified = bool(reached and cls.page is not None and cls.page.page_id == "main_city")
        final_page_id = cls.page.page_id if cls.page else None
        return cleanup_verified

    if cls.page is None or cls.page.page_id != "daily_tasks" or not cls.page.verified:
        return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.PARTIAL_PROGRESS,
                      "当前页身份未满足日常任务循环条件；未提交领取")

    button_state = batch_button_state()
    log("daily_batch_button", "daily_batch_state_observed", {
        "state": button_state,
        "green_template": controls.get("daily_claim_all", {}).get("template"),
        "gray_template": controls.get("daily_claim_all_disabled", {}).get("template"),
        "fixed_project_rows_not_required": True,
    })
    if button_state == "gray":
        no_reward_proven = True
        post_batch_state_verified = True
        if return_to_home("daily_gray_return_home"):
            return finish(RunStatus.NOTHING_TO_CLAIM, ReasonCode.NOTHING_TO_CLAIM,
                          "日常页一键领取灰态已明确识别，未提交领取，并已重新识别主城")
        return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.PARTIAL_PROGRESS,
                      "日常页一键领取灰态已识别；未提交领取，但安全回城未确认")

    if button_state != "green":
        returned = return_to_home("daily_unknown_state_return_home")
        return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.ASSETS_UNVERIFIED,
                      "顶部一键领取状态未知或绿灰同时命中；没有领取。"
                      + ("已安全回到主城。" if returned else "未能安全收尾。"))

    claim_control = controls.get("daily_claim_all")
    back_control = controls.get("daily_back")
    max_popups = max(1, int(config.get("max_reward_popups", 6)))
    max_rounds = max(1, int(config.get("max_batch_rounds", 5)))
    transition_seconds = max(0.0, float(config.get("transition_wait_seconds", 8)))
    back_reserve = 1 if isinstance(back_control, dict) and back_control.get("verified") else 0
    failure_message: str | None = None
    final_state = "unknown"

    for round_index in range(1, max_rounds + 1):
        if cls.page is None or cls.page.page_id != "daily_tasks" or cls.blockers or cls.ambiguous_pages:
            failure_message = "领取轮次之间页面身份丢失或出现阻断；停止输入"
            break
        state = batch_button_state()
        if state != "green":
            final_state = state
            break
        # Reserve this round's submission, its maximum popup-close allowance,
        # and the page-back action. When starting a later round, this same
        # reserve is the next round's cleanup budget.
        action_reserve = 1 + max_popups + back_reserve
        time_reserve = (max_popups + 1) * transition_seconds
        if sum(action_counts.values()) + action_reserve > request.max_actions:
            failure_message = "剩余动作预算不足以保留本轮领取、奖励收尾和回城额度"
            break
        if deadline - time.monotonic() < time_reserve:
            failure_message = "剩余总时限不足以保留本轮领取、奖励过渡和回城等待"
            break
        if not isinstance(claim_control, dict) or not claim_control.get("verified"):
            failure_message = "一键领取绿色控件素材未验证；未提交领取"
            break

        round_record: dict[str, Any] = {
            "round_index": round_index, "submission": "pending", "reward_popup_seen": False,
            "reward_content_verified": False, "reward_popup_count": 0, "reward_popup_closed": False,
            "post_batch_state": "unknown",
        }

        def mark_claim_submitted() -> None:
            nonlocal claim_request_submitted
            claim_request_submitted = True
            round_record["submission"] = "submitted"
            batch_rounds.append(round_record)

        if not submit_input("claim_daily_all", claim_control, "claim_action_count",
                            before_submit=mark_claim_submitted):
            return last_result or finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                                         "一键领取已提交或结果不明；本轮绝不重试")

        popup_ready = wait_for_transition(
            f"wait_for_daily_reward_round_{round_index}",
            lambda: cls.page is not None and cls.page.page_id == "daily_reward_popup"
            and cls.page.verified and not cls.blockers and not cls.ambiguous_pages,
            transition_seconds,
        )
        if not popup_ready:
            if last_result is not None:
                return last_result
            round_record["submission"] = "uncertain"
            log("daily_batch_button", "daily_batch_round_result", {
                "round_index": round_index, "round_result": "uncertain",
                "reason": "reward_popup_not_observed",
            })
            failure_message = "已提交一键领取，但等待期内未确认本轮奖励弹窗；禁止重提"
            input_uncertain = True
            break

        round_popup_count = 0
        round_content_verified = True
        popup_transition_failed = False
        while cls.page is not None and cls.page.page_id == "daily_reward_popup":
            if cls.blockers or cls.ambiguous_pages:
                popup_transition_failed = True
                break
            closed_items = frozenset(reward_item_matches)
            close = controls.get("daily_reward_close")
            if (round_popup_count >= max_popups or not cls.page.verified
                    or not isinstance(close, dict) or not close.get("verified")
                    or not control_hits.get("daily_reward_close", False)):
                popup_transition_failed = True
                break
            round_popup_count += 1
            reward_popup_count += 1
            known_items = list(reward_item_matches)
            content_verified = bool(known_items)
            round_content_verified = round_content_verified and content_verified
            round_record["reward_popup_seen"] = True
            round_record["reward_content_verified"] = round_content_verified
            round_record["reward_popup_count"] = round_popup_count
            log("daily_reward_popup", "daily_reward_round_popup_observed", {
                "round_index": round_index, "popup_index": round_popup_count,
                "matched_reward_items": known_items, "reward_content_verified": content_verified,
                "close_template": close["template"],
            })
            if not content_verified:
                unverified_reward_popup_count += 1
            if not submit_input("dismiss_daily_reward", close, "dismiss_action_count"):
                popup_transition_failed = True
                break

            changed = wait_after_reward_close(
                f"wait_after_daily_reward_close_{round_index}_{round_popup_count}", closed_items,
            )
            if not changed:
                if last_result is not None:
                    return last_result
                popup_transition_failed = True
                break
            if cls.page is not None and cls.page.page_id == "daily_tasks":
                round_record["reward_popup_closed"] = True
                break

        if last_result is not None:
            return last_result

        if popup_transition_failed:
            round_record["submission"] = "uncertain"
            log("daily_batch_button", "daily_batch_round_result", {
                "round_index": round_index, "round_result": "uncertain",
                "reason": "reward_popup_not_closed_or_transition_unconfirmed",
            })
            failure_message = "本轮奖励弹窗未能完整确认并安全关闭；不重提领取"
            input_uncertain = True
            break

        if cls.page is None or cls.page.page_id != "daily_tasks" or cls.blockers or cls.ambiguous_pages:
            round_record["submission"] = "uncertain"
            failure_message = "奖励关闭后没有重新确认稳定日常页；不重提领取"
            input_uncertain = True
            break

        state = batch_button_state()
        final_state = state
        round_record["post_batch_state"] = state
        round_record["submission"] = "confirmed" if (
            round_record["reward_popup_seen"] and round_record["reward_popup_closed"]
            and state in {"green", "gray"}
        ) else "uncertain"
        post_batch_state_verified = state in {"green", "gray"}
        log("daily_batch_button", "daily_batch_round_rechecked", {
            "round_index": round_index, "state": state,
            "reward_popup_closed": round_record["reward_popup_closed"],
            "reward_content_verified": round_record["reward_content_verified"],
            "round_result": "confirmed" if (
                round_record["reward_popup_seen"] and round_record["reward_popup_closed"]
                and state in {"green", "gray"}
            ) else "uncertain",
        })
        if state == "gray":
            break
        if state != "green":
            failure_message = "奖励关闭后顶部一键领取状态未知或绿灰冲突；不继续领取"
            input_uncertain = True
            break
        if round_index >= max_rounds:
            failure_message = "达到单次运行批领轮数上限；保留本轮证据，不报告已领完"
            break

    if claim_request_submitted:
        all_rounds_confirmed = bool(batch_rounds) and all(
            item.get("submission") == "confirmed" and item.get("reward_popup_seen") is True
            and item.get("reward_popup_closed") is True and item.get("post_batch_state") in {"green", "gray"}
            for item in batch_rounds
        )
        claim_verified = all_rounds_confirmed
        if final_state == "gray" and post_batch_state_verified:
            post_batch_state_verified = True
        home_ok = return_to_home("daily_claim_return_home")
        if (final_state == "gray" and claim_verified and post_batch_state_verified and home_ok):
            return finish(RunStatus.COMPLETED, ReasonCode.CLAIM_CONFIRMED,
                          "本轮奖励弹窗已观察并关闭，顶部灰态已确认并返回主城；物品名称识别仅作记录")
        if failure_message and claim_verified and (
            failure_message.startswith("达到单次运行批领轮数上限")
            or failure_message.startswith("剩余动作预算不足")
            or failure_message.startswith("剩余总时限不足")
        ):
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.PARTIAL_PROGRESS,
                          failure_message + ("已安全返回主城。" if home_ok else "未能确认安全回城。"))
        if final_state == "green" and failure_message is None:
            failure_message = "日常页仍显示绿色，当前状态未证明所有奖励已领取"
        if not claim_verified or final_state != "gray" or not home_ok:
            input_uncertain = input_uncertain or not claim_verified
            return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
                          (failure_message or "批量领取结果或收尾证据不足；保留不确定结果")
                          + ("已安全返回主城。" if home_ok else "未能确认安全回城。"))

    if cls.page is not None and cls.page.page_id == "daily_tasks" and not cls.blockers and not cls.ambiguous_pages:
        return_to_home("daily_no_claim_return_home")
    if failure_message and failure_message.startswith("剩余动作预算不足"):
        return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.ACTION_BUDGET_EXHAUSTED,
                      failure_message + ("已安全返回主城。" if cleanup_verified else ""))
    if failure_message and failure_message.startswith("剩余总时限不足"):
        return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT,
                      failure_message + ("已安全返回主城。" if cleanup_verified else ""))
    return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.ASSETS_UNVERIFIED,
                  failure_message or "日常一键领取状态未知；未提交领取，且不会将未知状态解释为无奖励")
