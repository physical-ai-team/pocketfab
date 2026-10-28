"""SAM 2 세그멘터 테스트. torch·SAM 2·체크포인트가 없으면 건너뛴다.

로컬 실행 예 (레포 루트에 checkpoints/sam2.1_hiera_tiny.pt가 있을 때):
    cd c3-inference
    $env:SAM2_CHECKPOINT_DIR="../checkpoints"; pytest
"""

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

pytest.importorskip("torch")
pytest.importorskip("sam2")
CKPT_DIR = Path(os.getenv("SAM2_CHECKPOINT_DIR", "/checkpoints"))
if not (CKPT_DIR / f"{os.getenv('SAM2_MODEL', 'sam2.1_hiera_tiny')}.pt").exists():
    pytest.skip(f"체크포인트 없음: {CKPT_DIR}", allow_module_level=True)

from app import judge, sam2_segmenter  # noqa: E402

W, H = 400, 200
COOKIE = (75, 138, 201)   # BGR, 시뮬레이터 쿠키와 비슷한 갈색
BELT = (49, 46, 42)


def frame():
    """정상 쿠키 1개 + 쐐기 모양으로 빠진 쿠키 1개 (지름 78px, 시뮬레이터 기본값)."""
    img = np.full((H, W, 3), BELT, np.uint8)
    cv2.circle(img, (100, 100), 39, COOKIE, -1)
    cv2.circle(img, (280, 100), 39, COOKIE, -1)
    cv2.ellipse(img, (280, 100), (40, 40), 0, -30, 30, BELT, -1)
    return img


def box(cx, cy, r=39, pad=0.08):
    """C2가 보내는 형식(0~1 정규화, 약간의 패딩)의 박스."""
    p = r * 2 * pad
    return [(cx - r - p) / W, (cy - r - p) / H, (cx + r + p) / W, (cy + r + p) / H]


def test_two_boxes_give_two_masks_and_verdicts():
    boxes = [{"box_id": 0, "box": box(100, 100)}, {"box_id": 1, "box": box(280, 100)}]
    segs = sam2_segmenter.segment(frame(), boxes)
    assert [s["box_id"] for s in segs] == [0, 1]
    objs = [judge.build_object(s, W, H) for s in segs]
    for o in objs:
        assert 0 <= o["mask_score"] <= 1
        assert all(0 <= x <= 1 and 0 <= y <= 1 for x, y in o["polygon"])
    # 마스크 면적이 실제 쿠키 면적(π·39²≈4778)과 비슷해야 한다
    assert abs(objs[0]["area_px"] - 4778) / 4778 < 0.1
    assert objs[0]["verdict"] == "normal", objs[0]
    assert objs[1]["verdict"] == "defect", objs[1]


def test_single_box_and_empty():
    segs = sam2_segmenter.segment(frame(), [{"box_id": 7, "box": box(100, 100)}])
    assert len(segs) == 1 and segs[0]["box_id"] == 7
    assert sam2_segmenter.segment(frame(), []) == []
