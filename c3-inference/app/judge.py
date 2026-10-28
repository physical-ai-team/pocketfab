"""윤곽선 → 원형도·solidity → 판정. SAM 2 모드와 OpenCV 모드가 함께 쓴다 (api-spec 4.1)."""

import math
import os

import cv2
import numpy as np

CIRCULARITY_THRESHOLD = float(os.getenv("CIRCULARITY_THRESHOLD", "0.80"))
SOLIDITY_THRESHOLD = float(os.getenv("SOLIDITY_THRESHOLD", "0.95"))
FILL_RATIO_THRESHOLD = float(os.getenv("FILL_RATIO_THRESHOLD", "0.94"))
CONCAVITY_THRESHOLD = float(os.getenv("CONCAVITY_THRESHOLD", "0.06"))
MASK_SCORE_MIN = float(os.getenv("MASK_SCORE_MIN", "0.5"))
MAX_POLYGON_POINTS = 64


def measure(contour: np.ndarray) -> dict:
    """픽셀 좌표 윤곽선의 면적, 둘레, 원형도, solidity, 원 채움률, 오목 깊이."""
    area = cv2.contourArea(contour)
    perimeter = cv2.arcLength(contour, closed=True)
    hull_area = cv2.contourArea(cv2.convexHull(contour))
    # 원 채움률: 면적 / 최소 외접원 면적. 잘리거나 파여도 외접원은 원래 원과 거의 같으므로
    # "원래 원에서 얼마나 없어졌나"를 과자 크기를 몰라도 잴 수 있다 (cut·half를 잡는 지표).
    _, enc_r = cv2.minEnclosingCircle(contour)
    enc_area = math.pi * enc_r * enc_r
    return {
        "area_px": round(area, 1),
        "perimeter_px": round(perimeter, 1),
        "circularity": round(4 * math.pi * area / perimeter**2, 4) if perimeter else 0.0,
        "solidity": round(area / hull_area, 4) if hull_area else 0.0,
        "fill_ratio": round(area / enc_area, 4) if enc_area else 0.0,
        "concavity": round(_max_concavity(contour) / enc_r, 4) if enc_r else 0.0,
    }


def _max_concavity(contour: np.ndarray) -> float:
    """가장 깊이 파인 곳의 깊이(px) = 볼록껍질에서 윤곽선까지 최대 거리 (bite·wedge를 잡는 지표)."""
    if len(contour) < 4:
        return 0.0
    hull_idx = cv2.convexHull(contour, returnPoints=False)
    try:
        defects = cv2.convexityDefects(contour, hull_idx)
    except cv2.error:  # 윤곽선이 자기 교차하는 드문 경우
        return 0.0
    if defects is None or len(defects) == 0:
        return 0.0
    return float(np.asarray(defects).reshape(-1, 4)[:, 3].max()) / 256.0  # OpenCV는 깊이에 256을 곱해 준다


def judge(circularity: float, solidity: float, mask_score: float | None,
          fill_ratio: float = 1.0, concavity: float = 0.0) -> tuple[str, list[str]]:
    """(verdict, defect_reasons). mask_score가 None이면(OpenCV 모드) 1단계를 건너뛴다.

    네 지표 중 하나라도 벗어나면 불량이다 (api-spec 4.1).
    """
    if mask_score is not None and mask_score < MASK_SCORE_MIN:
        return "uncertain", []
    reasons = []
    if circularity < CIRCULARITY_THRESHOLD:
        reasons.append("circularity")
    if solidity < SOLIDITY_THRESHOLD:
        reasons.append("solidity")
    if fill_ratio < FILL_RATIO_THRESHOLD:
        reasons.append("fill_ratio")
    if concavity > CONCAVITY_THRESHOLD:
        reasons.append("concavity")
    return ("defect" if reasons else "normal"), reasons


def defect_type(verdict: str, fill_ratio: float, concavity: float) -> str | None:
    """불량 유형 추정 (시뮬레이터 4종). 정상·판단불가면 None.

    오목하게 파였으면(concavity 큼) bite/wedge, 볼록하게 잘렸으면 cut/half.
    wedge는 중심까지 파여 concavity가 매우 크고, half는 반 가까이 없어져 fill_ratio가 매우 작다.
    """
    if verdict != "defect":
        return None
    if concavity > CONCAVITY_THRESHOLD:
        return "wedge" if concavity > 0.45 else "bite"
    return "half" if fill_ratio < 0.70 else "cut"


def to_polygon(contour: np.ndarray, w: int, h: int) -> list[list[float]]:
    """윤곽선을 단순화해 0~1 정규화 좌표로 바꾼다."""
    approx = cv2.approxPolyDP(contour, 0.005 * cv2.arcLength(contour, True), True).reshape(-1, 2)
    if len(approx) > MAX_POLYGON_POINTS:
        approx = approx[np.linspace(0, len(approx) - 1, MAX_POLYGON_POINTS).astype(int)]
    return [[round(x / w, 4), round(y / h, 4)] for x, y in approx]


def build_object(seg: dict, w: int, h: int) -> dict:
    """세그멘터 결과 1개 → api-spec ③의 objects[] 항목."""
    m = measure(seg["contour"])
    verdict, reasons = judge(m["circularity"], m["solidity"], seg["mask_score"], m["fill_ratio"], m["concavity"])
    return {
        "box_id": seg["box_id"],
        "box": seg["box"],
        "polygon": to_polygon(seg["contour"], w, h),
        **m,
        "mask_score": seg["mask_score"],
        "verdict": verdict,
        "defect_reasons": reasons,
        "defect_type": defect_type(verdict, m["fill_ratio"], m["concavity"]),
    }
