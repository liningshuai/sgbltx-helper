"""开发期探针：离线核实 MaaFw 5.14.2 的识别 API 行为。

用途：在没有设备的情况下，用合成图片验证
    Tasker.post_recognition() + Resource.post_bundle() 的实际返回结构，
避免凭记忆猜测 SDK 行为。本脚本不连接设备、不发出任何输入。

运行：.venv/Scripts/python.exe tools/probe_recognition.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from maa.pipeline import JRecognitionType, JTemplateMatch
from maa.resource import Resource
from maa.tasker import Tasker

SCENE_W, SCENE_H = 1280, 720
PATCH_X, PATCH_Y = 300, 420
PATCH_W, PATCH_H = 48, 48


def make_scene() -> tuple[np.ndarray, np.ndarray]:
    """造一张场景图和一个嵌入其中的模板块。返回 (scene_bgr, patch_bgr)。"""
    rng = np.random.default_rng(20261005)
    scene = np.full((SCENE_H, SCENE_W, 3), 40, dtype=np.uint8)
    # 造一个高对比度花纹块
    patch = np.zeros((PATCH_H, PATCH_W, 3), dtype=np.uint8)
    patch[:, :, 0] = np.tile(np.arange(PATCH_W, dtype=np.uint8), (PATCH_H, 1))
    patch[:, :, 1] = np.tile(np.arange(PATCH_H, dtype=np.uint8).reshape(-1, 1), (1, PATCH_W))
    patch[:, :, 2] = (rng.integers(0, 256, (PATCH_H, PATCH_W))).astype(np.uint8)
    scene[PATCH_Y : PATCH_Y + PATCH_H, PATCH_X : PATCH_X + PATCH_W] = patch
    return scene, patch


def save_bgr(path: Path, bgr: np.ndarray) -> None:
    Image.fromarray(bgr[:, :, ::-1]).save(path)


def main() -> int:
    scene, patch = make_scene()

    with tempfile.TemporaryDirectory(prefix="sgbltx-probe-") as tmp:
        tmpdir = Path(tmp)
        res_dir = tmpdir / "resource"
        (res_dir / "image").mkdir(parents=True)
        (res_dir / "pipeline").mkdir(parents=True)
        save_bgr(res_dir / "image" / "probe_patch.png", patch)
        save_bgr(tmpdir / "scene.png", scene)
        # pipeline 目录必须至少含一个合法 json，否则 post_bundle 整体失败
        (res_dir / "pipeline" / "probe.json").write_text(
            json.dumps(
                {
                    "ProbeNoop": {
                        "recognition": "DirectHit",
                        "action": "DoNothing",
                    }
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        resource = Resource()
        ok = resource.post_bundle(res_dir).wait().succeeded
        print(f"[probe] resource.post_bundle succeeded = {ok}")
        print(f"[probe] resource.loaded = {resource.loaded}")
        if not ok:
            return 2

        tasker = Tasker()
        # 注意：这里刻意不绑定 controller，用来核实 post_recognition 是否依赖设备。
        # Tasker.bind() 会同时绑定 controller，所以这里直接调用底层 API 只绑资源。
        from maa.library import Library

        bound = bool(Library.framework().MaaTaskerBindResource(tasker._handle, resource._handle))
        print(f"[probe] MaaTaskerBindResource only = {bound}")
        print(f"[probe] tasker.inited = {tasker.inited}")

        param = JTemplateMatch(
            template=["probe_patch.png"],
            roi=(0, 0, 0, 0),
            threshold=[0.7],
            order_by="Horizontal",
            index=0,
            method=5,
            green_mask=False,
        )
        job = tasker.post_recognition(JRecognitionType.TemplateMatch, param, scene)
        job.wait()
        print(f"[probe] post_recognition status.succeeded = {job.succeeded}")
        detail = job.get()
        print(f"[probe] task detail = {detail!r}")
        if detail is not None:
            print(f"[probe] node_id_list = {detail.node_id_list}")
            for node in detail.nodes:
                print(f"[probe]   node name={node.name!r} completed={node.completed}")
                reco = node.recognition
                if reco is None:
                    print("[probe]   recognition = None")
                    continue
                print(f"[probe]   reco.hit={reco.hit} box={reco.box} algo={reco.algorithm}")
                print(f"[probe]   reco.best={reco.best_result}")
                print(f"[probe]   reco.raw_detail={json.dumps(reco.raw_detail, ensure_ascii=False)}")
                if reco.box is not None:
                    cx = reco.box.x + reco.box.w // 2
                    cy = reco.box.y + reco.box.h // 2
                    print(f"[probe]   center=({cx},{cy}) expected=({PATCH_X + PATCH_W // 2},{PATCH_Y + PATCH_H // 2})")

        # 负样本：把场景换成纯色，应当不命中
        blank = np.full((SCENE_H, SCENE_W, 3), 40, dtype=np.uint8)
        job2 = tasker.post_recognition(JRecognitionType.TemplateMatch, param, blank)
        job2.wait()
        detail2 = job2.get()
        hit2 = None
        if detail2 is not None:
            for node in detail2.nodes:
                if node.recognition is not None:
                    hit2 = node.recognition.hit
        print(f"[probe] negative-sample hit = {hit2} (expect False/None)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
