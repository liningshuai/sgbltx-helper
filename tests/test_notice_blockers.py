"""随机提示阻断页：实机截图裁剪 → 真实 MaaFw 识别 → 三个任务零输入停止并写明人工处理方法。

fixtures 都是不含账号名、玩家名的 ROI 裁剪，贴回原始位置后整页识别（来源见 docs/assets.md）。
这些提示都没有按页面校准过的空白关闭目标，因此只能停止，绝不点击“确定”或空白处。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from conftest import FakeBackend
from sgbltx_helper.classification import Classification, blocker_reason, classify
from sgbltx_helper.contracts import DeviceProfile, InspectionResult, RunMode, RunStatus, TaskRequest
from sgbltx_helper.maa_adapter import MaaBackend
from sgbltx_helper.pages import PageSpec, load_page_catalog
from sgbltx_helper.runner import StopController, run

PROJECT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

TASK_PACKS = {
    "mail_bulk_claim": "mail_bulk",
    "daily_rewards_claim": "daily_rewards",
    "fief_industry_collect": "fief_industry",
}

# 提示 → (贴回原位的裁剪, 原因码, 结果里必须写明的人工处理方法)
NOTICES = {
    # run-20261021T081047Z-8320：“提示 / 网络有异常,请稍等片刻 / 确定”
    "network_error": (
        [("notice_network_error_title_roi.png", (440, 810)), ("notice_network_error_text_roi.png", (150, 940))],
        "network_error",
        "网络异常：请重启游戏后重新登录",
    ),
    # 另一会话的魏帝当选公告；模板取自 run-20261020T064958Z-328756，这里验证跨样本命中
    "weidi_announcement": (
        [("notice_weidi_title_roi.png", (360, 620))],
        "announcement",
        "魏帝公告：请手动关闭",
    ),
}


def _canvas(patches: list[tuple[str, tuple[int, int]]]) -> np.ndarray:
    canvas = Image.new("RGB", (1080, 1920), (0, 0, 0))
    for name, origin in patches:
        canvas.paste(Image.open(FIXTURES / name).convert("RGB"), origin)
    return np.ascontiguousarray(np.asarray(canvas)[:, :, ::-1])


@dataclass
class ReplayBackend(FakeBackend):
    """固定一帧实机裁剪画面，识别交给真实 MaaFw；输入只记录，不连设备。"""

    frame: np.ndarray | None = None
    maa: MaaBackend | None = None

    def screencap(self, deadline=None, should_stop=None) -> np.ndarray:  # type: ignore[no-untyped-def]
        self.screencap_count += 1
        assert self.frame is not None
        return self.frame.copy()

    def load_resource(self, resource_root: Path, deadline=None, should_stop=None, task_id=None) -> list[str]:  # type: ignore[no-untyped-def]
        self.maa = MaaBackend(adb_path="", address="")
        return self.maa.load_resource(resource_root, deadline, should_stop, task_id)

    def recognize(self, image, template, roi=(0, 0, 0, 0), threshold=0.85, deadline=None, should_stop=None):  # type: ignore[no-untyped-def]
        assert self.maa is not None
        return self.maa.recognize(image, template, roi, threshold, deadline, should_stop)

    def close(self) -> None:
        if self.maa is not None:
            self.maa.close()
        super().close()


def _run_on_notice(tmp_path: Path, task_id: str, notice: str, mode: RunMode):  # type: ignore[no-untyped-def]
    pack = TASK_PACKS[task_id]
    shutil.copytree(PROJECT / "resource" / pack, tmp_path / "resource" / pack)
    backend = ReplayBackend(frame=_canvas(NOTICES[notice][0]))
    request = TaskRequest(
        task_id=task_id,
        profile=DeviceProfile("test", "main", "", "emulator-5554", "com.sgbltx.goodgame", (1080, 1920)),
        mode=mode,
    )
    return run(request, backend, tmp_path / "artifacts" / "locks", tmp_path, StopController()), backend


@pytest.mark.parametrize("notice", NOTICES)
@pytest.mark.parametrize("task_id", TASK_PACKS)
def test_task_stops_without_input_and_names_the_manual_fix(tmp_path, task_id, notice) -> None:
    result, backend = _run_on_notice(tmp_path, task_id, notice, RunMode.EXECUTE)
    _, reason, manual_fix = NOTICES[notice]
    assert backend.input_calls == []
    assert result.action_count == 0
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code.value == reason
    assert manual_fix in result.detail["message"]


@pytest.mark.parametrize("notice", NOTICES)
@pytest.mark.parametrize("task_id", TASK_PACKS)
def test_dry_run_reports_the_notice_and_its_manual_fix(tmp_path, task_id, notice) -> None:
    result, backend = _run_on_notice(tmp_path, task_id, notice, RunMode.DRY_RUN)
    assert isinstance(result, InspectionResult)
    assert backend.input_calls == []
    assert any(NOTICES[notice][2] in line for line in result.blockers), result.blockers


@pytest.mark.parametrize(("blockers", "expected"), [
    ([PageSpec("network_error", role="blocker", reason_code="network_error")], "network_error"),
    # pages.json 原因码拼错：按未知页停止，不能在已有输入后抛异常中断收尾
    ([PageSpec("typo", role="blocker", reason_code="netwrok_error")], "unknown_page"),
    ([PageSpec("bare", role="blocker")], "unknown_page"),
    ([], "unknown_page"),
])
def test_blocker_reason_falls_back_to_unknown_page(blockers, expected) -> None:
    assert blocker_reason(Classification(blockers=blockers)).value == expected


@pytest.mark.parametrize(("task_id", "pack"), TASK_PACKS.items())
def test_weidi_notice_dimmed_under_duplicate_login_dialog_is_not_a_second_blocker(task_id, pack) -> None:
    """重复登录弹窗压在魏帝公告上：只能报重复登录。

    该弹窗同样以“提示”为标题（网络提示只命中一半），遮罩下的魏帝标题框归一化得分仍有 0.998，
    靠亮度下限排除。两个裁剪取自同一张实机截图（raw_conflict_1080x1920.png）。
    """
    frame = _canvas([("page_account_conflict_dialog.png", (120, 830)),
                     ("notice_weidi_title_dimmed_roi.png", (360, 620))])
    backend = MaaBackend(adb_path="", address="")
    backend.load_resource(PROJECT / "resource" / pack, task_id=task_id)
    try:
        cls = classify(backend, frame, load_page_catalog(PROJECT / "resource" / pack / "pages.json"))
    finally:
        backend.close()
    assert [page.page_id for page in cls.blockers] == ["account_conflict"]
    assert cls.ambiguous_pages == []
