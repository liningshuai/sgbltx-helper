"""从原始截图中裁剪素材与脱敏测试样本。

用法：
    python tools/crop_assets.py --source <原图.png> --out-dir <目录> --crop name=x,y,w,h ...

原图只保存在本地 artifacts/，裁剪产物用于 resource/image/ 模板与 tests/fixtures/
样本。裁剪前请确认 ROI 内不含账号、聊天或其它个人信息。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def parse_crop(spec: str) -> tuple[str, tuple[int, int, int, int]]:
    name, _, coords = spec.partition("=")
    parts = [int(v) for v in coords.split(",")]
    if len(parts) != 4:
        raise ValueError(f"--crop 需要 name=x,y,w,h，收到：{spec}")
    return name.strip(), (parts[0], parts[1], parts[2], parts[3])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--crop", action="append", default=[], required=True)
    ap.add_argument("--scale", type=float, default=1.0, help="放大倍数，便于肉眼核对（不影响保存尺寸）")
    args = ap.parse_args()

    src = Image.open(args.source).convert("RGB")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"原图 {args.source} 尺寸 {src.size}")

    for spec in args.crop:
        name, box = parse_crop(spec)
        x, y, w, h = box
        if x + w > src.size[0] or y + h > src.size[1]:
            raise SystemExit(f"裁剪区域越界：{spec}（原图 {src.size[0]}x{src.size[1]}）")
        tile = src.crop((x, y, x + w, y + h))
        path = out_dir / f"{name}.png"
        tile.save(path)
        if args.scale != 1.0:
            preview = tile.resize((int(w * args.scale), int(h * args.scale)))
            preview.save(out_dir / f"{name}.preview.png")
        print(f"  {name}: box={box} -> {path}  size={tile.size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
