"""C2 박스 검출 테스트. 실행: c2-preprocessing 폴더에서 `pytest`

어두운 벨트 위에 같은 크기 과자를 그린 합성 화면으로, 과자는 모두 찾고 과자가 아닌 덩어리는 버리는지 본다.
"""

import cv2
import numpy as np

from app.preprocess import detect_boxes, preprocess

W, H = 640, 360
R = 24  # 과자 반지름 (시연 화면에서 지름 약 49px)
COOKIE = (90, 150, 210)  # BGR 갈색
BELT = (49, 46, 42)


def frame(extra=None):
    img = np.full((H, W, 3), BELT, np.uint8)
    centers = [(100, 90), (220, 90), (340, 90), (460, 90), (100, 250), (220, 250), (340, 250)]
    for cx, cy in centers:
        cv2.circle(img, (cx, cy), R, COOKIE, -1)
        cv2.circle(img, (cx - 6, cy - 4), 3, (30, 40, 60), -1)  # 초코칩
    # 반쪽 과자 (half): 길쭉하지만 과자다
    cv2.ellipse(img, (480, 250), (R, R), 0, 0, 180, COOKIE, -1)
    if extra:
        extra(img)
    jpeg = cv2.imencode(".jpg", img)[1].tobytes()
    proc, size, _ = preprocess(jpeg, {"w": W, "h": H})
    return detect_boxes(proc, size), len(centers) + 1


def test_과자는_모두_찾는다():
    boxes, n = frame()
    assert len(boxes) == n


def test_반사된_큰_밝은_영역은_버린다():
    # 모니터 반사로 벨트 일부가 하얗게 뜬 상황: 과자 여러 개만 한 밝은 사각형
    boxes, n = frame(lambda img: cv2.rectangle(img, (540, 140), (630, 340), (230, 230, 230), -1))
    assert len(boxes) == n
    assert all((b["box"][2] - b["box"][0]) * W < 4 * R for b in boxes)


def test_가늘고_긴_띠는_버린다():
    # 벨트 테두리나 화면 글자처럼 가늘고 긴 밝은 띠
    boxes, n = frame(lambda img: cv2.rectangle(img, (150, 165), (420, 178), (220, 220, 220), -1))
    assert len(boxes) == n


def test_화면_끝에_걸린_과자는_길쭉해도_남긴다():
    # 왼쪽 끝에 일부만 보이는 과자 (C4가 truncated로 표시하고 집계에서 뺀다. C2에서 지우지 않는다)
    boxes, n = frame(lambda img: cv2.circle(img, (8, 170), R, COOKIE, -1))
    assert len(boxes) == n + 1
