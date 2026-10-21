from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sgbltx_helper.maa_adapter import MaaBackend
from sgbltx_helper.pages import load_page_catalog


PROJECT = Path(__file__).resolve().parents[1]
RESOURCE = PROJECT / "resource" / "fief_industry"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# 2026-10-21 实机主城 dryrun-20261021T070159Z-39596/before.png 中两处非资源标记的裁剪左上角
INVASION_MARKER_ORIGIN = (260, 660)
INVASION_MARKER_CENTER = (315, 720)
SCROLL_MARKER_ORIGIN = (260, 1220)
SCROLL_MARKER_CENTER = (318, 1280)


def _bgr_canvas(patch_path: Path, origin: tuple[int, int]) -> np.ndarray:
    canvas = Image.new("RGB", (1080, 1920), (0, 0, 0))
    canvas.paste(Image.open(patch_path).convert("RGB"), origin)
    rgb = np.asarray(canvas)
    return np.ascontiguousarray(rgb[:, :, ::-1])


def _offset_roi(center: tuple[int, int], offset: list[int]) -> tuple[int, int, int, int]:
    dx, dy, w, h = offset
    return (center[0] + dx, center[1] + dy, w, h)


@pytest.fixture(scope="module")
def fief_backend() -> MaaBackend:
    backend = MaaBackend(adb_path="", address="")
    backend.load_resource(RESOURCE, task_id="fief_industry_collect")
    yield backend
    backend.close()


def _config() -> dict:
    return json.loads((RESOURCE / "fief_industry.json").read_text(encoding="utf-8"))


def test_resource_pack_loads_and_bubbles_await_live_sample(fief_backend: MaaBackend) -> None:
    config = _config()
    assert config["resource_bubbles"] == []
    assert config["max_reward_popups"] == 4
    assert config["controls"]["reward_close"]["verified"] is False
    catalog = load_page_catalog(RESOURCE / "pages.json")
    assert catalog.by_id("reward_popup").verified is False
    assert catalog.by_id("main_city").verified is True
    assert {page.page_id for page in catalog.blockers} >= {
        "account_conflict", "unrelated_announcement", "unrelated_announcement_defense",
    }


def test_invasion_label_hits_real_invasion_marker_only(fief_backend: MaaBackend) -> None:
    label = _config()["invasion_label"]
    invasion = _bgr_canvas(FIXTURES / "fief_invasion_marker_roi.png", INVASION_MARKER_ORIGIN)
    outcome = fief_backend.recognize(
        invasion, label["template"], _offset_roi(INVASION_MARKER_CENTER, label["offset_roi"]), label["threshold"],
    )
    assert outcome.hit, f"入侵字样未命中, score={outcome.score}"

    scroll = _bgr_canvas(FIXTURES / "fief_scroll_marker_roi.png", SCROLL_MARKER_ORIGIN)
    outcome = fief_backend.recognize(
        scroll, label["template"], _offset_roi(SCROLL_MARKER_CENTER, label["offset_roi"]), label["threshold"],
    )
    assert not outcome.hit
