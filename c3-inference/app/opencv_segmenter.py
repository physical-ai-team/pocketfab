"""SEGMENTER=opencv: SAM 2 없이 박스 안에서 OpenCV 윤곽선으로 마스크를 대신한다 (api-spec 7.2).

성능·정확도 비교 기준선이자 시연 안전장치다. 아주 단순한 버전이며 조명·배경에 민감하다.
"""

import cv2
import numpy as np


def segment(image: np.ndarray, boxes: list[dict]) -> list[dict]:
    """박스마다 가장 큰 윤곽선 1개를 찾는다.

    반환: [{"box_id", "box", "contour"(이미지 픽셀 좌표), "mask_score"(None)}]
    SAM 2 세그멘터도 같은 형식으로 반환하면 judge.build_object를 그대로 쓸 수 있다.
    """
    h, w = image.shape[:2]
    results = []
    for b in boxes:
        x1, y1, x2, y2 = b["box"]
        px1, py1 = max(0, int(x1 * w)), max(0, int(y1 * h))
        px2, py2 = min(w, int(x2 * w)), min(h, int(y2 * h))
        crop = image[py1:py2, px1:px2]
        if crop.size == 0:
            continue

        gray = cv2.GaussianBlur(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        # 과자가 배경보다 밝은지 어두운지 모르므로, 박스 테두리(=배경)가 흰색이면 반전한다
        border = np.concatenate([mask[0], mask[-1], mask[:, 0], mask[:, -1]])
        if border.mean() > 127:
            mask = cv2.bitwise_not(mask)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea) + np.array([px1, py1])  # 박스 좌표 → 이미지 좌표
        results.append({"box_id": b["box_id"], "box": b["box"], "contour": contour, "mask_score": None})
    return results
