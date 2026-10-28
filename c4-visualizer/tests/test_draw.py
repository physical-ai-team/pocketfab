"""그리기 테스트. 실행: c4-visualizer 폴더에서 `pytest`

색이 맞는 자리에 칠해졌는지, 라벨 글자가 맞는지, 필드가 빠져도 죽지 않는지 본다.
"""

import math

import cv2
import numpy as np

from app.draw import COLORS, EDGE_COLOR, labels, render

W, H = 640, 360


def circle_polygon(cx, cy, r, n=48):
    return [[round((cx + r * math.cos(2 * math.pi * i / n)) / W, 4),
             round((cy + r * math.sin(2 * math.pi * i / n)) / H, 4)] for i in range(n)]


def obj(box_id, cx, cy, verdict, **extra):
    r = 40
    o = {"box_id": box_id, "box": [(cx - r) / W, (cy - r) / H, (cx + r) / W, (cy + r) / H],
         "polygon": circle_polygon(cx, cy, r), "circularity": 0.9, "solidity": 0.99, "fill_ratio": 0.97,
         "concavity": 0.02, "mask_score": 0.95, "verdict": verdict, "defect_reasons": [], "defect_type": None}
    o.update(extra)
    return o


def blank_jpeg():
    ok, buf = cv2.imencode(".jpg", np.full((H, W, 3), 40, np.uint8))
    return buf.tobytes()


def near(pixel, bgr, tol=60):
    return all(abs(int(a) - int(b)) <= tol for a, b in zip(pixel, bgr))


def test_윤곽선_색과_요약():
    objects = [
        obj(0, 120, 200, "normal"),
        obj(1, 320, 200, "defect", concavity=0.12, defect_reasons=["concavity"], defect_type="bite"),
        obj(2, 520, 200, "uncertain", mask_score=0.3),
    ]
    out, summary = render(blank_jpeg(), {"frame_id": 7, "objects": objects})
    assert summary == {"total": 3, "normal": 1, "defect": 1, "uncertain": 1}

    img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
    # 각 원의 오른쪽 끝(윤곽선 위)에 판정 색이 있어야 한다
    assert near(img[200, 160], COLORS["normal"])
    assert near(img[200, 360], COLORS["defect"])
    assert near(img[200, 560], COLORS["uncertain"])
    # 불량만 안쪽이 붉게 칠해진다 (빨강 채널이 배경보다 밝다)
    assert img[200, 320][2] > img[200, 120][2] + 20


def test_라벨은_불량_유형과_판정을_가른_지표만():
    bite = obj(0, 0, 0, "defect", concavity=0.12, defect_reasons=["concavity"], defect_type="bite")
    assert labels(bite) == ["bite", "D0.12"]

    cut = obj(1, 0, 0, "defect", fill_ratio=0.88, defect_reasons=["fill_ratio"], defect_type="cut")
    assert labels(cut) == ["cut", "F0.88"]

    # 여러 지표가 벗어나면 오목 깊이 > 원 채움률 > 원형도 > solidity 순으로 하나만 쓴다
    wedge = obj(2, 0, 0, "defect", circularity=0.6, concavity=0.8,
                defect_reasons=["circularity", "solidity", "fill_ratio", "concavity"], defect_type="wedge")
    assert labels(wedge) == ["wedge", "D0.80"]

    assert labels(obj(3, 0, 0, "normal")) == []  # 정상은 윤곽선만
    assert labels(obj(4, 0, 0, "uncertain", mask_score=0.3)) == ["?", "m0.30"]


def test_화면끝_과자는_회색이고_라벨이_없다():
    edge = obj(0, 40, 200, "defect", truncated=True, defect_reasons=["fill_ratio"], defect_type="cut")
    out, _ = render(blank_jpeg(), {"frame_id": 1, "objects": [edge]})
    img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
    assert near(img[200, 80], EDGE_COLOR)
    assert not near(img[200, 40], COLORS["defect"], tol=30)  # 화면 끝 과자는 칠하지 않는다


def test_검사선_통과_강조_와_polygon_없는_과자():
    o = obj(0, 220, 200, "defect", track_id=5, defect_reasons=["circularity"], defect_type="half")
    del o["polygon"]  # polygon이 없으면 박스로 그린다
    meta = {"frame_id": 3, "objects": [o], "tracking": {
        "count_line_x": 0.34, "counted": [{"track_id": 5, "verdict": "defect"}],
        "totals": {"total": 1, "normal": 0, "defect": 1, "uncertain": 0}}}
    out, _ = render(blank_jpeg(), meta)
    img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
    line_x = int(0.34 * W)
    assert near(img[H // 2, line_x], COLORS["defect"])  # 검사선이 불량 색으로 굵게 바뀐다
