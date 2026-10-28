"""C3 판정 정확도를 시뮬레이터 정답 라벨로 채점한다.

C2 박스 검출과 상관없이 C3만 평가하려고, 정답 bbox에 패딩을 붙여 C2 박스처럼 넣는다.
화면 가장자리에 걸린 과자는 뺀다 (api-spec 7.4).

    # 레포 루트에서. 데이터 폴더 = 시뮬레이터 프레임 PNG + 라벨 JSON 쌍 (같은 이름)
    python tools/c3_eval.py data/sim/base                    # SEGMENTER=opencv
    python tools/c3_eval.py data/sim/base --segmenter sam2   # SAM2_CHECKPOINT_DIR=checkpoints 필요
    python tools/c3_eval.py data/sim/base --masks gt         # 정답 폴리곤으로 판정 규칙만 평가
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TYPES = ["bite", "cut", "wedge", "half"]
BOX_PAD = 0.08  # C2가 박스를 넓혀 보내는 비율 (api-spec 7.1: 5~10%)


def is_truncated(bbox, w, h):
    x, y, bw, bh = bbox
    return x < 1 or y < 1 or x + bw > w - 1 or y + bh > h - 1


def gt_box(bbox, w, h):
    """정답 [x, y, w, h] 픽셀 → C2 형식 [x1, y1, x2, y2] 0~1 (패딩 포함)."""
    x, y, bw, bh = bbox
    px, py = bw * BOX_PAD, bh * BOX_PAD
    return [max(0, (x - px) / w), max(0, (y - py) / h), min(1, (x + bw + px) / w), min(1, (y + bh + py) / h)]


def poly_mask(points_px, w, h):
    m = np.zeros((h, w), np.uint8)
    cv2.fillPoly(m, [np.round(np.asarray(points_px, np.float64).reshape(-1, 2) * 4).astype(np.int32)], 1, shift=2)
    return m


def main():
    p = argparse.ArgumentParser(description="C3 판정 정확도 채점")
    p.add_argument("data", type=Path, nargs="+")
    p.add_argument("--segmenter", choices=["opencv", "sam2"], default="opencv")
    p.add_argument("--masks", choices=["gt"], help="세그멘터 대신 정답 폴리곤으로 판정 규칙만 평가")
    args = p.parse_args()

    os.environ["SEGMENTER"] = args.segmenter
    os.environ.setdefault("SAM2_CHECKPOINT_DIR", str(ROOT / "checkpoints"))
    sys.path.insert(0, str(ROOT / "c3-inference"))
    from app import judge  # noqa: E402
    if args.masks == "gt":
        segment = None
    elif args.segmenter == "sam2":
        from app import sam2_segmenter as seg_mod  # noqa: E402
        segment = seg_mod.segment
    else:
        from app import opencv_segmenter as seg_mod  # noqa: E402
        segment = seg_mod.segment

    for data in args.data:
        rows, ious, lat = [], [], []
        # 팀 규칙 이름(0001.png + 0001.json)과 시뮬레이터 기본 이름(frame_*.png) 모두 읽는다
        for jp in sorted(p for p in data.glob("*.json") if p.with_suffix(".png").exists()):
            doc = json.loads(jp.read_text(encoding="utf-8"))
            w, h = doc["image"]["width"], doc["image"]["height"]
            anns = [a for a in doc["annotations"] if not is_truncated(a["bbox"], w, h)]
            if not anns:
                continue
            if segment is None:
                segs = [{"box_id": i, "box": gt_box(a["bbox"], w, h), "mask_score": None,
                         "contour": np.round(np.asarray(a["segmentation"][0]).reshape(-1, 1, 2)).astype(np.int32)}
                        for i, a in enumerate(anns)]
            else:
                image = cv2.imread(str(jp.with_suffix(".png")))
                boxes = [{"box_id": i, "box": gt_box(a["bbox"], w, h)} for i, a in enumerate(anns)]
                t0 = time.perf_counter()
                segs = segment(image, boxes)
                lat.append((time.perf_counter() - t0) * 1000)
            by_id = {s["box_id"]: s for s in segs}
            for i, a in enumerate(anns):
                gt = "defect" if a["category"] == "deformed" else "normal"
                if i not in by_id:
                    rows.append((gt, "missing", a["defect_type"], None))
                    continue
                o = judge.build_object(by_id[i], w, h)
                rows.append((gt, o["verdict"], a["defect_type"], o["defect_type"]))
                if segment is not None:
                    pm = np.zeros((h, w), np.uint8)
                    cv2.drawContours(pm, [by_id[i]["contour"]], -1, 1, cv2.FILLED)
                    gm = poly_mask(a["segmentation"][0], w, h)
                    ious.append((pm & gm).sum() / max(1, (pm | gm).sum()))

        tp = sum(g == "defect" and v == "defect" for g, v, *_ in rows)
        fp = sum(g == "normal" and v == "defect" for g, v, *_ in rows)
        fn = sum(g == "defect" and v != "defect" for g, v, *_ in rows)
        unc = sum(v == "uncertain" for _, v, *_ in rows)
        normals = sum(g == "normal" for g, *_ in rows)
        prec, rec = tp / max(1, tp + fp), tp / max(1, tp + fn)
        f1 = 2 * prec * rec / max(1e-9, prec + rec)
        typed = [(gt_t, pr_t) for g, v, gt_t, pr_t in rows if g == "defect" and v == "defect"]
        type_acc = sum(a == b for a, b in typed) / max(1, len(typed))
        mode = "gt-mask" if segment is None else args.segmenter
        print(f"\n[{data.name} · {mode}] 객체 {len(rows)}  불량 P {prec:.3f} R {rec:.3f} F1 {f1:.3f}  "
              f"유형 정확도 {type_acc:.3f}  uncertain {unc}"
              + (f"  IoU {np.mean(ious):.3f}" if ious else "")
              + (f"  지연 p50 {np.percentile(lat[1:] or lat, 50):.0f}ms" if lat else ""))
        # 임계값 보정 기준 (performance-plan 3장): 정상 오경보율 2% 이하
        print(f"  정상 오경보 {fp}/{normals} ({fp / max(1, normals):.1%})")
        missed = Counter(gt_t for g, v, gt_t, _ in rows if g == "defect" and v != "defect")
        if missed:
            print("  놓친 불량:", dict(missed))
        conf = Counter(typed)
        print("  유형 혼동(행=정답, 열=예측): " + "  ".join(
            f"{g}→" + ",".join(f"{q}:{conf[(g, q)]}" for q in TYPES if conf[(g, q)]) for g in TYPES))


if __name__ == "__main__":
    main()
