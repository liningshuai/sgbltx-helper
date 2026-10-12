"""真实截图回放：用**实际安装的** MaaFramework 识别实现做正反样本验证。

这里不使用任何预写分类结果——每一步都真的调用 ``Tasker.post_recognition``，
因此能证明模板、阈值和 ROI 在真实画面上确实成立。

样本说明（见 docs/assets.md）：
  * tests/fixtures/ 下的样本都经过裁剪，只保留必要区域，不含账号或聊天信息。
  * 完整原始截图只保存在本地 artifacts/local-samples/，不进入 Git；
    依赖原图的用例在文件缺失时 skip。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sgbltx_helper.classification import classify
from sgbltx_helper.mail_bulk import BulkObservation, choose_bulk_step, match_reward_items
from sgbltx_helper.maa_adapter import MaaBackend
from sgbltx_helper.pages import load_page_catalog
from sgbltx_helper.runner import load_pipeline

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESOURCE = PROJECT_ROOT / "resource"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
LOCAL_SAMPLES = PROJECT_ROOT / "artifacts" / "local-samples"
MAIL_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "dryrun-20261005T141255Z-157536" / "before.png"
MAIN_CITY_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "dryrun-20261005T140643Z-166296" / "before.png"
MAIL_DETAIL_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "dryrun-20261005T145021Z-166744" / "before.png"
MAIL_INBOX_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "dryrun-20261005T143920Z-166324" / "before.png"
MAIL_SUCCESS_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "run-20261005T150342Z-154560" / "after.png"
MAIL_REPEAT_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "dryrun-20261005T151042Z-163996" / "before.png"
TAISHOU_DETAIL_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "dryrun-20261006T011905Z-164888" / "before.png"
TAISHOU_SUCCESS_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "run-20261006T014134Z-173084" / "after.png"
MAIL_BULK_INBOX_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "dryrun-20261006T030453Z-175716" / "before.png"
MAIL_BULK_CITY_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "dryrun-20261006T050617Z-179348" / "before.png"
MAIL_BULK_OTHER_CITY_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "dryrun-20261006T081100Z-179196" / "before.png"
MAIL_BULK_CURRENT_CITY_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "dryrun-20261006T080328Z-179112" / "before.png"
MAIL_BULK_NEW_CITY_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "dryrun-20261006T093831Z-188456" / "before.png"
MAIL_BULK_MORE_MENU_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "run-20261006T081132Z-173092" / "after.png"
MAIL_BULK_EXPIRED_INBOX_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "run-20261006T081653Z-150896" / "after.png"
MAIL_BULK_NEW_INBOX_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "dryrun-20261006T094103Z-188892" / "before.png"
MAIL_BULK_CONFLICT_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "dryrun-20261005T133916Z-155900" / "before.png"
MAIL_BULK_REWARD_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "run-20261006T151602Z-183208" / "step-0012.png"
MAIL_BULK_NEXT_REWARD_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "cleanup-20261006T1516Z-manual-close" / "reward-dismissed.png"
MAIL_BULK_POST_REWARDS_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "cleanup-20261006T1516Z-manual-close" / "reward-dismissed-2.png"
MAIL_BULK_UNREGISTERED_REWARD_SCREENSHOT = PROJECT_ROOT / "artifacts" / "runs" / "run-20261007T025546Z-201920" / "after.png"

CONFLICT_TEMPLATE = "page_account_conflict_text.png"
CONFLICT_ROI = (120, 830, 840, 350)


def load_bgr(path: Path) -> np.ndarray:
    """把 PNG 读成 BGR 数组，与 MaaFramework 的返回约定一致。"""
    rgb = np.asarray(Image.open(path).convert("RGB"))
    return np.ascontiguousarray(rgb[:, :, ::-1])


@pytest.fixture(scope="module")
def backend() -> MaaBackend:
    """只绑 Resource、不绑 Controller 的后端。

    实测确认：``post_recognition`` 不需要设备，所以截图回放天然不发出任何输入。
    """
    b = MaaBackend(adb_path="", address="")
    b.load_resource(RESOURCE)
    yield b
    b.close()


@pytest.fixture(scope="module")
def catalog():  # type: ignore[no-untyped-def]
    return load_page_catalog(RESOURCE / "pages.json")


@pytest.fixture(scope="module")
def bulk_backend() -> MaaBackend:
    b = MaaBackend(adb_path="", address="")
    b.load_resource(RESOURCE / "mail_bulk", task_id="mail_bulk_claim")
    yield b
    b.close()


@pytest.fixture(scope="module")
def bulk_catalog():  # type: ignore[no-untyped-def]
    return load_page_catalog(RESOURCE / "mail_bulk" / "pages.json")


class TestTemplateMatchReplay:
    def test_positive_sample_hits(self, backend: MaaBackend) -> None:
        """真实「重复登录」弹窗裁剪图 → 必须命中。"""
        image = load_bgr(FIXTURES / "page_account_conflict_dialog.png")
        outcome = backend.recognize(image, CONFLICT_TEMPLATE, (0, 0, 0, 0), 0.8)
        assert outcome.hit is True, f"正样本未命中，score={outcome.score}"
        assert outcome.score is not None and outcome.score >= 0.8

    def test_dialog_title_only_does_not_hit(self, backend: MaaBackend) -> None:
        """只有「提示」标题、没有正文的对话框区域 → 不得命中。"""
        image = load_bgr(FIXTURES / "page_account_conflict_title.png")
        outcome = backend.recognize(image, CONFLICT_TEMPLATE, (0, 0, 0, 0), 0.8)
        assert outcome.hit is False

    def test_main_city_bottom_nav_does_not_hit(self, backend: MaaBackend) -> None:
        """真实主城底部导航条 → 不得命中（干扰样本）。"""
        image = load_bgr(FIXTURES / "main_city_bottom_nav.png")
        outcome = backend.recognize(image, CONFLICT_TEMPLATE, (0, 0, 0, 0), 0.8)
        assert outcome.hit is False

    def test_main_city_top_hud_does_not_hit(self, backend: MaaBackend) -> None:
        image = load_bgr(FIXTURES / "main_city_top_hud.png")
        outcome = backend.recognize(image, CONFLICT_TEMPLATE, (0, 0, 0, 0), 0.8)
        assert outcome.hit is False

    def test_threshold_is_meaningful(self, backend: MaaBackend) -> None:
        """对降质样本：低阈值命中、高阈值漏检——说明阈值不是装饰。"""
        from PIL import ImageFilter

        path = FIXTURES / "page_account_conflict_dialog.png"
        blurred = Image.open(path).convert("RGB").filter(ImageFilter.GaussianBlur(radius=4))
        image = np.ascontiguousarray(np.asarray(blurred)[:, :, ::-1])

        lenient = backend.recognize(image, CONFLICT_TEMPLATE, (0, 0, 0, 0), 0.5)
        strict = backend.recognize(image, CONFLICT_TEMPLATE, (0, 0, 0, 0), 0.98)
        assert lenient.score is not None and strict.score is not None
        assert lenient.score == strict.score, "两次识别应针对同一张图给出同一分数"
        assert lenient.score < 1.0, "降质样本的分数应低于完美匹配"
        assert lenient.hit is True
        assert strict.hit is False

    def test_self_match_scores_one(self, backend: MaaBackend) -> None:
        """模板取自本图时分数应为 1.0——确认识别链路本身没有额外损耗。"""
        image = load_bgr(FIXTURES / "page_account_conflict_dialog.png")
        outcome = backend.recognize(image, CONFLICT_TEMPLATE, (0, 0, 0, 0), 0.8)
        assert outcome.score == 1.0
        assert outcome.box == (180, 125, 570, 70)

    def test_roi_narrows_search(self, backend: MaaBackend) -> None:
        """模板不在 ROI 内时不得命中，证明 ROI 真的生效。"""
        image = load_bgr(FIXTURES / "page_account_conflict_dialog.png")
        # 只搜对话框顶部 60 像素，模板（位于 y≈125）不在其中
        outcome = backend.recognize(image, CONFLICT_TEMPLATE, (0, 0, 840, 60), 0.8)
        assert outcome.hit is False


class TestClassificationReplay:
    def test_conflict_sample_classified_as_blocker(self, backend: MaaBackend, catalog) -> None:  # type: ignore[no-untyped-def]
        """裁剪图放在配置的 ROI 位置上做整页分类，应识别为阻断页。"""
        canvas = np.zeros((1920, 1080, 3), dtype=np.uint8)
        dialog = load_bgr(FIXTURES / "page_account_conflict_dialog.png")
        x, y, w, h = CONFLICT_ROI
        canvas[y : y + h, x : x + w] = dialog

        result = classify(backend, canvas, catalog)
        assert [b.page_id for b in result.blockers] == ["account_conflict"]
        assert result.page is None, "阻断页成立时不应再判定普通页面"

    def test_blank_canvas_yields_no_page(self, backend: MaaBackend, catalog) -> None:  # type: ignore[no-untyped-def]
        blank = np.zeros((1920, 1080, 3), dtype=np.uint8)
        result = classify(backend, blank, catalog)
        assert result.page is None
        assert result.blockers == []

    def test_observations_recorded_for_every_matcher(self, backend: MaaBackend, catalog) -> None:  # type: ignore[no-untyped-def]
        blank = np.zeros((1920, 1080, 3), dtype=np.uint8)
        result = classify(backend, blank, catalog)
        # 页面身份不成立时，不解释任何领取/已领状态模板。
        expected_identity_count = sum(len(page.identity) for page in catalog.pages)
        assert len(result.observations) == expected_identity_count
        assert all(o.hit is False for o in result.observations)


class TestFullScreenshotReplay:
    """依赖本地原始截图（不进入 Git）。缺失时 skip，而不是伪造结果。"""

    @pytest.mark.parametrize(
        "filename,expect_blocker",
        [
            ("raw_conflict_1080x1920.png", True),
            ("raw_main_city_1080x1920.png", False),
        ],
    )
    def test_full_screen_replay(self, backend: MaaBackend, catalog, filename: str, expect_blocker: bool) -> None:  # type: ignore[no-untyped-def]
        path = LOCAL_SAMPLES / filename
        if not path.is_file():
            pytest.skip(f"本地原始截图不存在（不进入 Git）：{path}")
        image = load_bgr(path)
        assert image.shape[:2] == (1920, 1080)
        result = classify(backend, image, catalog)
        assert (len(result.blockers) > 0) is expect_blocker


class TestMailPageReplay:
    def test_live_taishou_detail_page_and_button_are_registered_separately(self, backend: MaaBackend, catalog) -> None:  # type: ignore[no-untyped-def]
        if not TAISHOU_DETAIL_SCREENSHOT.is_file():
            pytest.skip(f"太守奖励详情实机截图不存在（仅本机留存）：{TAISHOU_DETAIL_SCREENSHOT}")
        result = classify(backend, load_bgr(TAISHOU_DETAIL_SCREENSHOT), catalog)
        assert result.page is not None and result.page.page_id == "mail_taishou_detail"
        assert result.claimable and result.claimable[0].hit
        assert not result.has_already_claimed_marker

    def test_real_claim_success_screen_is_explicit_postcondition(self, backend: MaaBackend, catalog) -> None:  # type: ignore[no-untyped-def]
        if not MAIL_SUCCESS_SCREENSHOT.is_file():
            pytest.skip(f"实机领取成功截图不存在（仅本机留存）：{MAIL_SUCCESS_SCREENSHOT}")
        result = classify(backend, load_bgr(MAIL_SUCCESS_SCREENSHOT), catalog)
        assert result.page is not None and result.page.page_id == "mail_claim_detail"
        assert result.has_already_claimed_marker
        assert not result.has_claimable

    def test_live_mail_detail_page_identity_and_claim_button(self, backend: MaaBackend, catalog) -> None:  # type: ignore[no-untyped-def]
        if not MAIL_DETAIL_SCREENSHOT.is_file():
            pytest.skip(f"实机邮件详情截图不存在（仅本机留存）：{MAIL_DETAIL_SCREENSHOT}")
        result = classify(backend, load_bgr(MAIL_DETAIL_SCREENSHOT), catalog)
        assert result.page is not None and result.page.page_id == "mail_claim_detail"
        assert len(result.claimable) == 1
        assert result.claimable[0].hit is True

    def test_mail_inbox_and_detail_overlay_are_distinct(self, backend: MaaBackend, catalog) -> None:  # type: ignore[no-untyped-def]
        if not MAIL_INBOX_SCREENSHOT.is_file() or not MAIL_DETAIL_SCREENSHOT.is_file():
            pytest.skip("实机邮件列表/详情截图不存在（仅本机留存）")
        inbox = classify(backend, load_bgr(MAIL_INBOX_SCREENSHOT), catalog)
        detail = classify(backend, load_bgr(MAIL_DETAIL_SCREENSHOT), catalog)
        assert inbox.page is not None and inbox.page.page_id == "mail_inbox"
        assert detail.page is not None and detail.page.page_id == "mail_claim_detail"
        assert inbox.ambiguous_pages == [] and detail.ambiguous_pages == []


class TestClaimRewardNodeReplay:
    def test_pipeline_condition_matches_page_claimable_condition(self, catalog) -> None:  # type: ignore[no-untyped-def]
        pipeline = load_pipeline(RESOURCE / "pipeline" / "claim_primary.json")
        page = catalog.by_id(catalog.target_page_id)
        assert page is not None and len(page.claimable) == 1
        matcher = page.claimable[0]
        param = pipeline["ClaimReward"]["recognition"]["param"]
        assert param["template"] == [matcher.template]
        assert tuple(param["roi"]) == matcher.roi
        assert param["threshold"] == [matcher.threshold]

        entry_param = pipeline["ClaimPrimary"]["recognition"]["param"]
        assert entry_param["template"] == [page.identity[0].template]

    @pytest.mark.parametrize(
        "path,expected_hit",
        [(TAISHOU_DETAIL_SCREENSHOT, True), (MAIL_DETAIL_SCREENSHOT, False)],
    )
    def test_pipeline_entry_is_scoped_to_the_selected_mail_theme(
        self, backend: MaaBackend, path: Path, expected_hit: bool
    ) -> None:
        if not path.is_file():
            pytest.skip(f"本机真实截图不存在（仅本机留存）：{path}")
        node = load_pipeline(RESOURCE / "pipeline" / "claim_primary.json")["ClaimPrimary"]["recognition"]["param"]
        outcome = backend.recognize(
            load_bgr(path), node["template"][0], tuple(node["roi"]), node["threshold"][0]
        )
        assert outcome.hit is expected_hit

    @pytest.mark.parametrize(
        "path,expected_hit",
        [
            (MAIL_DETAIL_SCREENSHOT, True),
            (MAIL_SUCCESS_SCREENSHOT, False),
            (MAIL_REPEAT_SCREENSHOT, False),
        ],
    )
    def test_actual_pipeline_node_recognition_distinguishes_green_and_claimed_button(
        self, backend: MaaBackend, path: Path, expected_hit: bool
    ) -> None:
        if not path.is_file():
            pytest.skip(f"本机真实截图不存在（仅本机留存）：{path}")
        node = load_pipeline(RESOURCE / "pipeline" / "claim_primary.json")["ClaimReward"]["recognition"]["param"]
        outcome = backend.recognize(
            load_bgr(path),
            node["template"][0],
            tuple(node["roi"]),
            node["threshold"][0],
        )
        assert outcome.hit is expected_hit

    def test_live_mail_inbox_identity(self, backend: MaaBackend, catalog) -> None:  # type: ignore[no-untyped-def]
        """实际邮件列表页应由专属“一键阅读”标志识别。"""
        if not MAIL_SCREENSHOT.is_file():
            pytest.skip(f"实机邮件截图不存在（仅本机留存）：{MAIL_SCREENSHOT}")
        image = load_bgr(MAIL_SCREENSHOT)
        result = classify(backend, image, catalog)
        assert result.page is not None and result.page.page_id == "mail_inbox"
        assert result.claimable == []

    def test_main_city_is_not_misclassified_as_mail_page(self, backend: MaaBackend, catalog) -> None:  # type: ignore[no-untyped-def]
        if not MAIN_CITY_SCREENSHOT.is_file():
            pytest.skip(f"实机主城截图不存在（仅本机留存）：{MAIN_CITY_SCREENSHOT}")
        image = load_bgr(MAIN_CITY_SCREENSHOT)
        result = classify(backend, image, catalog)
        assert result.page is None or result.page.page_id != "mail_inbox"


class TestMailBulkRealScreenshots:
    @pytest.mark.parametrize("screenshot", [MAIL_BULK_REWARD_SCREENSHOT, MAIL_BULK_NEXT_REWARD_SCREENSHOT])
    def test_bulk_reward_foreground_wins_over_mail_background(self, bulk_backend: MaaBackend, bulk_catalog, screenshot: Path) -> None:  # type: ignore[no-untyped-def]
        if not screenshot.is_file():
            pytest.skip(f"本机批量奖励截图不存在：{screenshot}")
        result = classify(bulk_backend, load_bgr(screenshot), bulk_catalog)
        assert result.page is not None and result.page.page_id == "reward_popup"

    def test_batch_reward_items_and_dismiss_hint_match_real_popups(self, bulk_backend: MaaBackend) -> None:
        import json

        config = json.loads((RESOURCE / "mail_bulk" / "mail_bulk.json").read_text(encoding="utf-8"))
        reward_defs = config["reward_items"]
        samples = [
            (MAIL_BULK_REWARD_SCREENSHOT, {"bulk_reward_gold.png", "bulk_reward_competition.png", "bulk_reward_silver.png", "bulk_reward_grain.png"}),
            (MAIL_BULK_NEXT_REWARD_SCREENSHOT, {"bulk_reward_merit.png"}),
            (MAIL_BULK_UNREGISTERED_REWARD_SCREENSHOT, {
                "bulk_reward_hero_skill_packet.png", "bulk_reward_battle_flag_chest.png",
                "bulk_reward_heart_chest.png", "bulk_reward_dream_dust.png",
            }),
        ]
        for path, expected in samples:
            if not path.is_file():
                pytest.skip(f"本机批量奖励截图不存在：{path}")
            image = load_bgr(path)
            matches = set(match_reward_items(bulk_backend, image, reward_defs))
            assert expected <= matches, f"{path}: expected={expected}, actual={matches}"
            close = config["controls"]["reward_close"]
            outcome = bulk_backend.recognize(image, close["template"], tuple(close["roi"]), close["threshold"])
            assert outcome.hit and outcome.center is not None, f"奖励关闭提示未命中：{path}, score={outcome.score}"
            old_x = bulk_backend.recognize(image, "mail_reward_close_x.png", (25, 410, 125, 130), 0.95)
            assert not old_x.hit, "奖励弹窗不应授权单封邮件详情关闭 X"

    def test_new_reward_layout_templates_do_not_match_previous_reward_states(self, bulk_backend: MaaBackend) -> None:
        import json

        config = json.loads((RESOURCE / "mail_bulk" / "mail_bulk.json").read_text(encoding="utf-8"))
        new_templates = {
            "bulk_reward_hero_skill_packet.png",
            "bulk_reward_battle_flag_chest.png",
            "bulk_reward_heart_chest.png",
            "bulk_reward_dream_dust.png",
        }
        new_defs = [item for item in config["reward_items"] if item["template"] in new_templates]
        assert {item["template"] for item in new_defs} == new_templates
        for path in (MAIL_BULK_REWARD_SCREENSHOT, MAIL_BULK_NEXT_REWARD_SCREENSHOT, MAIL_BULK_POST_REWARDS_SCREENSHOT):
            if not path.is_file():
                pytest.skip(f"本机批量奖励负样本缺失：{path}")
            matches = set(match_reward_items(bulk_backend, load_bgr(path), new_defs))
            assert not matches, f"新布局物品误命中旧状态：{path}: {matches}"

    def test_batch_reward_panel_without_items_is_not_claim_evidence(self, bulk_backend: MaaBackend) -> None:
        if not MAIL_BULK_REWARD_SCREENSHOT.is_file():
            pytest.skip("本机批量奖励截图缺失")
        image = load_bgr(MAIL_BULK_REWARD_SCREENSHOT).copy()
        image[820:1045, 110:970] = 0
        import json

        config = json.loads((RESOURCE / "mail_bulk" / "mail_bulk.json").read_text(encoding="utf-8"))
        assert match_reward_items(bulk_backend, image, config["reward_items"]) == []
        close = config["controls"]["reward_close"]
        assert bulk_backend.recognize(image, close["template"], tuple(close["roi"]), close["threshold"]).hit

    def test_batch_post_reward_list_is_not_reward_popup(self, bulk_backend: MaaBackend, bulk_catalog) -> None:  # type: ignore[no-untyped-def]
        if not MAIL_BULK_POST_REWARDS_SCREENSHOT.is_file():
            pytest.skip("本机批量奖励关闭后列表截图缺失")
        result = classify(bulk_backend, load_bgr(MAIL_BULK_POST_REWARDS_SCREENSHOT), bulk_catalog)
        assert result.page is not None and result.page.page_id == "mail_inbox"

    def test_partial_reward_foreground_cue_suppresses_mail_background_actions(self, bulk_backend: MaaBackend, bulk_catalog) -> None:  # type: ignore[no-untyped-def]
        if not MAIL_BULK_REWARD_SCREENSHOT.is_file():
            pytest.skip("本机批量奖励截图缺失")
        image = load_bgr(MAIL_BULK_REWARD_SCREENSHOT).copy()
        # Keep the reward title and inbox background, but remove the separate
        # close hint. A partial overlay identity must suppress the background.
        image[1100:1165, 330:750] = 0
        result = classify(bulk_backend, image, bulk_catalog)
        assert result.page is None
        assert "reward_popup" in result.ambiguous_pages

    def test_bulk_inbox_and_controls_match_actual_screen(self, bulk_backend: MaaBackend, bulk_catalog) -> None:  # type: ignore[no-untyped-def]
        if not MAIL_BULK_INBOX_SCREENSHOT.is_file():
            pytest.skip(f"本机批量邮件列表截图不存在：{MAIL_BULK_INBOX_SCREENSHOT}")
        image = load_bgr(MAIL_BULK_INBOX_SCREENSHOT)
        result = classify(bulk_backend, image, bulk_catalog)
        assert result.page is not None and result.page.page_id == "mail_inbox"
        assert result.has_claimable is True
        assert result.has_already_claimed_marker is False
        read_icon = bulk_backend.recognize(image, "read_mail_envelope.png", (48, 510, 135, 970), 0.96)
        assert read_icon.hit, "正样本里应存在已读信封，但不能据此推断邮件附件已领取"
        for template, roi, threshold in [
            ("inbox_bulk_button.png", (300, 1480, 500, 220), 0.97),
            ("inbox_close_x.png", (25, 335, 125, 95), 0.97),
        ]:
            outcome = bulk_backend.recognize(image, template, roi, threshold)
            assert outcome.hit, f"{template} 未命中，score={outcome.score}"

    def test_main_city_template_matches_real_screenshot_and_not_mail_inbox(self, bulk_backend: MaaBackend, bulk_catalog) -> None:  # type: ignore[no-untyped-def]
        if not MAIL_BULK_CURRENT_CITY_SCREENSHOT.is_file() or not MAIL_BULK_INBOX_SCREENSHOT.is_file():
            pytest.skip("本机当前主城或邮件列表原图缺失")
        city = classify(bulk_backend, load_bgr(MAIL_BULK_CURRENT_CITY_SCREENSHOT), bulk_catalog)
        inbox = classify(bulk_backend, load_bgr(MAIL_BULK_INBOX_SCREENSHOT), bulk_catalog)
        reward = classify(bulk_backend, load_bgr(MAIL_BULK_REWARD_SCREENSHOT), bulk_catalog)
        assert city.page is not None and city.page.page_id == "main_city"
        assert reward.page is not None and reward.page.page_id == "reward_popup"
        assert inbox.page is not None and inbox.page.page_id == "mail_inbox"

    @pytest.mark.parametrize(
        "screenshot",
        [MAIL_BULK_CITY_SCREENSHOT, MAIL_BULK_CURRENT_CITY_SCREENSHOT, MAIL_BULK_OTHER_CITY_SCREENSHOT, MAIL_BULK_NEW_CITY_SCREENSHOT],
    )
    def test_registered_main_city_samples_classify_as_main_city(self, bulk_backend: MaaBackend, bulk_catalog, screenshot: Path) -> None:  # type: ignore[no-untyped-def]
        if not screenshot.is_file():
            pytest.skip(f"主城原始截图缺失：{screenshot}")
        result = classify(bulk_backend, load_bgr(screenshot), bulk_catalog)
        assert result.page is not None and result.page.page_id == "main_city", screenshot

    def test_more_control_matches_old_and_new_city_with_configured_matcher(self, bulk_backend: MaaBackend) -> None:
        positives = [MAIL_BULK_CITY_SCREENSHOT, MAIL_BULK_CURRENT_CITY_SCREENSHOT, MAIL_BULK_OTHER_CITY_SCREENSHOT, MAIL_BULK_NEW_CITY_SCREENSHOT]
        if any(not p.is_file() for p in positives):
            pytest.skip("旧/新主城样本缺失")
        import json

        config = json.loads((RESOURCE / "mail_bulk" / "mail_bulk.json").read_text(encoding="utf-8"))
        matcher = config["controls"]["more"]
        roi = tuple(matcher["roi"])
        for screenshot in positives:
            result = bulk_backend.recognize(
                load_bgr(screenshot), matcher["template"], roi, matcher["threshold"],
            )
            assert result.hit and result.center is not None, f"{screenshot}: score={result.score}"

    def test_known_overlays_and_hud_only_occlusion_do_not_authorize_more_navigation(self, bulk_backend: MaaBackend, bulk_catalog) -> None:  # type: ignore[no-untyped-def]
        import json

        config = json.loads((RESOURCE / "mail_bulk" / "mail_bulk.json").read_text(encoding="utf-8"))
        matcher = config["controls"]["more"]
        overlay_samples = [
            (MAIL_BULK_NEW_INBOX_SCREENSHOT, "mail_inbox"),
            (MAIL_BULK_MORE_MENU_SCREENSHOT, "more_menu"),
            (MAIL_BULK_REWARD_SCREENSHOT, "reward_popup"),
            (MAIL_BULK_CONFLICT_SCREENSHOT, None),
        ]
        for screenshot, expected_page in overlay_samples:
            if not screenshot.is_file():
                pytest.skip(f"入口负样本缺失：{screenshot}")
            image = load_bgr(screenshot)
            result = classify(bulk_backend, image, bulk_catalog)
            assert result.page is None if expected_page is None else result.page is not None and result.page.page_id == expected_page
            target = bulk_backend.recognize(
                image, matcher["template"], tuple(matcher["roi"]), matcher["threshold"],
            )
            observation = BulkObservation(
                page_id=result.page.page_id if result.page else None,
                blocker=result.blockers[0].page_id if result.blockers else None,
                ambiguous=bool(result.ambiguous_pages),
            )
            assert choose_bulk_step(observation, False) != "open_more", screenshot
            if result.blockers:
                assert choose_bulk_step(observation, False) == "stop_blocker"
            else:
                assert not target.hit, f"overlay must not authorize More: {screenshot}, score={target.score}"

        # 合成遮挡控制：只保留真实主城固定 HUD 图标与 More 圆心，遮去城景语义区域。
        # 即使 More 控件识别命中，缺少城景确认时页面仍应保持未知。
        if not MAIL_BULK_NEW_CITY_SCREENSHOT.is_file():
            pytest.skip("新主城截图缺失，无法构造遮挡控制")
        city = load_bgr(MAIL_BULK_NEW_CITY_SCREENSHOT)
        occluded = np.zeros_like(city)
        occluded[132:183, 429:472] = city[132:183, 429:472]
        occluded[1660:1700, 990:1030] = city[1660:1700, 990:1030]
        result = classify(bulk_backend, occluded, bulk_catalog)
        assert result.page is None
        observation = BulkObservation(page_id=None, ambiguous=bool(result.ambiguous_pages))
        assert choose_bulk_step(observation, False) == "stop_unknown"

    def test_more_button_control_matches_current_city_and_not_mail_or_reward(self, bulk_backend: MaaBackend) -> None:
        if not MAIL_BULK_CURRENT_CITY_SCREENSHOT.is_file() or not MAIL_BULK_INBOX_SCREENSHOT.is_file():
            pytest.skip("本机当前主城或邮件列表原图缺失")
        current = load_bgr(MAIL_BULK_CURRENT_CITY_SCREENSHOT)
        inbox = load_bgr(MAIL_BULK_INBOX_SCREENSHOT)
        reward = load_bgr(TAISHOU_SUCCESS_SCREENSHOT)
        roi = (940, 1630, 140, 115)
        positive = bulk_backend.recognize(current, "more_action_icon_nodot.png", roi, 0.97)
        assert positive.hit and positive.center is not None
        assert not bulk_backend.recognize(inbox, "more_action_icon_nodot.png", roi, 0.97).hit
        assert not bulk_backend.recognize(reward, "more_action_icon_nodot.png", roi, 0.97).hit

    def test_more_menu_and_mail_entry_match_actual_menu_only(self, bulk_backend: MaaBackend, bulk_catalog) -> None:  # type: ignore[no-untyped-def]
        if not MAIL_BULK_MORE_MENU_SCREENSHOT.is_file() or not MAIL_BULK_CURRENT_CITY_SCREENSHOT.is_file():
            pytest.skip("本机更多菜单或主城原图缺失")
        menu = load_bgr(MAIL_BULK_MORE_MENU_SCREENSHOT)
        city = load_bgr(MAIL_BULK_CURRENT_CITY_SCREENSHOT)
        inbox = load_bgr(MAIL_BULK_INBOX_SCREENSHOT)
        assert classify(bulk_backend, menu, bulk_catalog).page.page_id == "more_menu"
        assert classify(bulk_backend, city, bulk_catalog).page.page_id == "main_city"
        assert classify(bulk_backend, inbox, bulk_catalog).page.page_id == "mail_inbox"

    def test_disabled_bulk_button_marks_no_claims_only_on_expired_inbox(self, bulk_backend: MaaBackend, bulk_catalog) -> None:  # type: ignore[no-untyped-def]
        if not MAIL_BULK_EXPIRED_INBOX_SCREENSHOT.is_file() or not MAIL_BULK_INBOX_SCREENSHOT.is_file():
            pytest.skip("本机灰色或金色一键阅读截图缺失")
        expired = load_bgr(MAIL_BULK_EXPIRED_INBOX_SCREENSHOT)
        available = load_bgr(MAIL_BULK_INBOX_SCREENSHOT)
        result = classify(bulk_backend, expired, bulk_catalog)
        assert result.page is not None and result.page.page_id == "mail_inbox"
        assert not result.has_claimable
        disabled = bulk_backend.recognize(
            expired, "inbox_bulk_button_disabled.png", (300, 1480, 500, 220), 0.97,
        )
        enabled_not_disabled = bulk_backend.recognize(
            available, "inbox_bulk_button_disabled.png", (300, 1480, 500, 220), 0.97,
        )
        assert disabled.hit
        assert not enabled_not_disabled.hit

    def test_reward_content_matcher_requires_real_batch_item_pixels_not_only_panel(self, bulk_backend: MaaBackend, bulk_catalog) -> None:  # type: ignore[no-untyped-def]
        if not MAIL_BULK_REWARD_SCREENSHOT.is_file() or not MAIL_BULK_NEXT_REWARD_SCREENSHOT.is_file():
            pytest.skip("真实批量奖励截图缺失")
        import json

        config = json.loads((RESOURCE / "mail_bulk" / "mail_bulk.json").read_text(encoding="utf-8"))
        matcher_data = config["reward_items"]
        first = load_bgr(MAIL_BULK_REWARD_SCREENSHOT)
        second = load_bgr(MAIL_BULK_NEXT_REWARD_SCREENSHOT)
        assert {"bulk_reward_gold.png", "bulk_reward_competition.png", "bulk_reward_silver.png", "bulk_reward_grain.png"} <= set(match_reward_items(bulk_backend, first, matcher_data))
        assert "bulk_reward_merit.png" in match_reward_items(bulk_backend, second, matcher_data)

        # 控制负样本保留真实批量弹窗身份/关闭提示，只遮去物品展示区域。
        panel_only = first.copy()
        panel_only[820:1045, 110:970] = 0
        result = classify(bulk_backend, panel_only, bulk_catalog)
        assert result.page is not None and result.page.page_id == "reward_popup"
        assert match_reward_items(bulk_backend, panel_only, matcher_data) == []

    def test_bulk_reward_popup_identity_is_verified_on_two_consecutive_real_results(self, bulk_backend: MaaBackend, bulk_catalog) -> None:  # type: ignore[no-untyped-def]
        for path in [MAIL_BULK_REWARD_SCREENSHOT, MAIL_BULK_NEXT_REWARD_SCREENSHOT]:
            if not path.is_file():
                pytest.skip(f"本机真实批量奖励截图缺失：{path}")
            result = classify(bulk_backend, load_bgr(path), bulk_catalog)
            assert result.page is not None and result.page.page_id == "reward_popup"
            assert result.page.verified is True

    def test_bulk_reward_hint_not_single_mail_x_matches_batch_screens_only(self, bulk_backend: MaaBackend) -> None:
        if not MAIL_BULK_REWARD_SCREENSHOT.is_file() or not MAIL_BULK_NEXT_REWARD_SCREENSHOT.is_file():
            pytest.skip("至少一张真实批量奖励截图缺失")
        import json

        config = json.loads((RESOURCE / "mail_bulk" / "mail_bulk.json").read_text(encoding="utf-8"))
        matcher = config["controls"]["reward_close"]
        assert matcher["template"] == "bulk_reward_dismiss_hint.png"
        for path in [MAIL_BULK_REWARD_SCREENSHOT, MAIL_BULK_NEXT_REWARD_SCREENSHOT]:
            image = load_bgr(path)
            close = bulk_backend.recognize(image, matcher["template"], tuple(matcher["roi"]), matcher["threshold"])
            old_x = bulk_backend.recognize(image, "mail_reward_close_x.png", (25, 410, 125, 130), 0.95)
            assert close.hit, f"批量奖励关闭提示未命中：{path}, score={close.score}"
            assert not old_x.hit, f"批量奖励不应通过单封详情 X 关闭：{path}, score={old_x.score}"
        inbox = load_bgr(MAIL_BULK_INBOX_SCREENSHOT)
        close_negative = bulk_backend.recognize(inbox, matcher["template"], tuple(matcher["roi"]), matcher["threshold"])
        mail_x = bulk_backend.recognize(inbox, "inbox_close_x.png", (25, 335, 125, 95), 0.97)
        assert not close_negative.hit
        assert mail_x.hit
