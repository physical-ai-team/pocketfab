"""opencv 세그멘터 + 판정 규칙 테스트. 실행: c3-inference 폴더에서 `pytest`"""

import cv2
import numpy as np

from app import judge, opencv_segmenter

W, H = 400, 300
BOX = [{"box_id": 0, "box": [0.25, 0.2, 0.75, 0.8]}]


def run(image):
    segs = opencv_segmenter.segment(image, BOX)
    assert len(segs) == 1
    return judge.build_object(segs[0], W, H)


def blank():
    return np.full((H, W, 3), 40, np.uint8)  # 어두운 컨베이어 배경


def test_round_snack_is_normal():
    img = blank()
    cv2.circle(img, (200, 150), 60, (200, 180, 140), -1)
    obj = run(img)
    assert obj["verdict"] == "normal", obj
    assert obj["circularity"] > 0.85 and obj["solidity"] > 0.95 and obj["fill_ratio"] > 0.95


def test_chipped_snack_is_defect_by_solidity():
    img = blank()
    cv2.circle(img, (200, 150), 60, (200, 180, 140), -1)
    cv2.ellipse(img, (200, 150), (61, 61), 0, -20, 20, (40, 40, 40), -1)  # 쐐기 모양으로 이 빠짐
    obj = run(img)
    assert obj["verdict"] == "defect", obj
    assert "solidity" in obj["defect_reasons"]


def test_squashed_snack_is_defect_by_circularity():
    img = blank()
    cv2.ellipse(img, (200, 150), (90, 35), 0, 0, 360, (200, 180, 140), -1)  # 납작하게 눌림
    obj = run(img)
    assert obj["verdict"] == "defect", obj
    assert "circularity" in obj["defect_reasons"]


def test_straight_cut_is_defect_by_fill_ratio():
    # 볼록하게 잘린 cut은 solidity로는 정상과 구별되지 않고 원 채움률이 잡아야 한다 (api-spec 7.3)
    img = blank()
    cv2.circle(img, (200, 150), 60, (200, 180, 140), -1)
    cv2.rectangle(img, (200 + 42, 0), (W, H), (40, 40, 40), -1)  # 중심에서 0.7r 떨어진 직선으로 잘림
    obj = run(img)
    assert obj["verdict"] == "defect", obj
    assert "fill_ratio" in obj["defect_reasons"]
    assert "solidity" not in obj["defect_reasons"]


def test_low_mask_score_is_uncertain():
    metrics = {"circularity": 0.99, "solidity": 0.99, "fill_ratio": 0.99}
    assert judge.judge(metrics, mask_score=0.1) == ("uncertain", [])


def test_polygon_is_normalized():
    img = blank()
    cv2.circle(img, (200, 150), 60, (200, 180, 140), -1)
    poly = run(img)["polygon"]
    assert 3 <= len(poly) <= judge.MAX_POLYGON_POINTS
    assert all(0 <= x <= 1 and 0 <= y <= 1 for x, y in poly)
