"""三个任务包共用的主城身份：只依赖固定界面，不受地图镜头位置影响，遮罩下不成立。

封地产业收取会把镜头移到被点的产业并停留，之后运行的邮件、日常任务也必须认得主城。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sgbltx_helper.classification import classify
from sgbltx_helper.maa_adapter import MaaBackend
from sgbltx_helper.pages import load_page_catalog

PROJECT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PACKS = {
    "mail_bulk": "mail_bulk_claim",
    "daily_rewards": "daily_rewards_claim",
    "fief_industry": "fief_industry_collect",
}


def _canvas(prefix: str) -> np.ndarray:
    canvas = Image.new("RGB", (1080, 1920), (0, 0, 0))
    canvas.paste(Image.open(FIXTURES / f"{prefix}_hud_roi.png").convert("RGB"), (300, 100))
    canvas.paste(Image.open(FIXTURES / f"{prefix}_task_roi.png").convert("RGB"), (80, 1540))
    return np.ascontiguousarray(np.asarray(canvas)[:, :, ::-1])


@pytest.fixture(scope="module", params=sorted(PACKS))
def pack(request):
    root = PROJECT / "resource" / request.param
    backend = MaaBackend(adb_path="", address="")
    backend.load_resource(root, task_id=PACKS[request.param])
    yield backend, load_page_catalog(root / "pages.json")
    backend.close()


def test_panned_main_city_is_identified_by_fixed_ui(pack) -> None:
    backend, catalog = pack
    cls = classify(backend, _canvas("fief_panned_city"), catalog)
    assert cls.page is not None and cls.page.page_id == "main_city"
    assert not cls.blockers and not cls.ambiguous_pages


@pytest.mark.parametrize("prefix", ["fief_dimmed_weidi", "fief_dimmed_moremenu"])
def test_dimmed_hud_under_overlay_is_not_main_city(pack, prefix) -> None:
    backend, catalog = pack
    cls = classify(backend, _canvas(prefix), catalog)
    assert cls.page is None or cls.page.page_id != "main_city"
