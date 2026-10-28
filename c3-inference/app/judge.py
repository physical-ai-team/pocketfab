"""윤곽선 → 원형도·solidity·원 채움률·오목 깊이 → 판정·불량 유형. SAM 2 모드와 OpenCV 모드가 함께 쓴다 (api-spec 4.1)."""

import math
import os

import cv2
import numpy as np

CIRCULARITY_THRESHOLD = float(os.getenv("CIRCULARITY_THRESHOLD", "0.80"))
SOLIDITY_THRESHOLD = float(os.getenv("SOLIDITY_THRESHOLD", "0.95"))
FILL_RATIO_THRESHOLD = float(os.getenv("FILL_RATIO_THRESHOLD", "0.93"))
CONCAVITY_THRESHOLD = float(os.getenv("CONCAVITY_THRESHOLD", "0.06"))
MASK_SCORE_MIN = float(os.getenv("MASK_SCORE_MIN", "0.5"))
MAX_POLYGON_POINTS = 64


def measure(contour: np.ndarray) -> dict:
    """픽셀 좌표 윤곽선의 면적, 둘레, 원형도, solidity, 원 채움률, 오목 깊이."""
    area = cv2.contourArea(contour)
    perimeter = cv2.arcLength(contour, closed=True)
    hull_area = cv2.contourArea(cv2.convexHull(contour))
    # 원 채움률: 과자를 감싸는 가장 작은 원 중 과자가 차지하는 비율. 볼록하게 잘린 cut/half도 잡는다
    _, enclosing_r = cv2.minEnclosingCircle(contour)
    enclosing_area = math.pi * enclosing_r**2
    return {
        "area_px": round(area, 1),
        "perimeter_px": round(perimeter, 1),
        "circularity": round(4 * math.pi * area / perimeter**2, 4) if perimeter else 0.0,
        "solidity": round(area / hull_area, 4) if hull_area else 0.0,
        "fill_ratio": round(area / enclosing_area, 4) if enclosing_area else 0.0,
        # 오목 깊이: 가장 깊게 파인 곳의 깊이 / 외접원 반지름. 작은 bite도 정상과 크게 벌어진다 (api-spec 7.3)
        "concavity": round(_max_concavity(contour) / enclosing_r, 4) if enclosing_r else 0.0,
    }


def _max_concavity(contour: np.ndarray) -> float:
    """볼록껍질에서 윤곽선까지의 최대 거리(px). 오목한 곳이 없으면 0."""
    if len(contour) < 4:
        return 0.0
    try:
        defects = cv2.convexityDefects(contour, cv2.convexHull(contour, returnPoints=False))
    except cv2.error:  # 윤곽선이 자기 교차하는 드문 경우
        return 0.0
    if defects is None or len(defects) == 0:
        return 0.0
    return float(np.asarray(defects).reshape(-1, 4)[:, 3].max()) / 256.0  # OpenCV는 깊이에 256을 곱해 준다


# (지표 이름, 임계값) — 하나라도 임계값 미만이면 불량 (api-spec 4.1)
def thresholds() -> list[tuple[str, float]]:
    return [
        ("circularity", CIRCULARITY_THRESHOLD),
        ("solidity", SOLIDITY_THRESHOLD),
        ("fill_ratio", FILL_RATIO_THRESHOLD),
    ]


# (지표 이름, 임계값) — 하나라도 임계값을 넘으면 불량 (값이 클수록 나쁜 지표)
def upper_limits() -> list[tuple[str, float]]:
    return [
        ("concavity", CONCAVITY_THRESHOLD),
    ]


def judge(metrics: dict, mask_score: float | None) -> tuple[str, list[str]]:
    """(verdict, defect_reasons). mask_score가 None이면(OpenCV 모드) 1단계를 건너뛴다."""
    if mask_score is not None and mask_score < MASK_SCORE_MIN:
        return "uncertain", []
    reasons = [name for name, limit in thresholds() if metrics[name] < limit]
    reasons += [name for name, limit in upper_limits() if metrics[name] > limit]
    return ("defect" if reasons else "normal"), reasons


def defect_type(verdict: str, metrics: dict) -> str | None:
    """불량 유형 추정 (시뮬레이터 4종). 정상·판단불가면 None.

    오목하게 파였으면 bite/wedge (wedge는 중심까지 파여 오목 깊이가 매우 크다),
    볼록하게 잘렸으면 cut/half (half는 반 가까이 없어져 원 채움률이 매우 작다). 경계값 근거는 api-spec 7.3.
    """
    if verdict != "defect":
        return None
    if metrics["concavity"] > CONCAVITY_THRESHOLD:
        return "wedge" if metrics["concavity"] > 0.45 else "bite"
    return "half" if metrics["fill_ratio"] < 0.70 else "cut"


def to_polygon(contour: np.ndarray, w: int, h: int) -> list[list[float]]:
    """윤곽선을 단순화해 0~1 정규화 좌표로 바꾼다."""
    approx = cv2.approxPolyDP(contour, 0.005 * cv2.arcLength(contour, True), True).reshape(-1, 2)
    if len(approx) > MAX_POLYGON_POINTS:
        approx = approx[np.linspace(0, len(approx) - 1, MAX_POLYGON_POINTS).astype(int)]
    return [[round(x / w, 4), round(y / h, 4)] for x, y in approx]


def build_object(seg: dict, w: int, h: int) -> dict:
    """세그멘터 결과 1개 → api-spec ③의 objects[] 항목."""
    m = measure(seg["contour"])
    verdict, reasons = judge(m, seg["mask_score"])
    return {
        "box_id": seg["box_id"],
        "box": seg["box"],
        "polygon": to_polygon(seg["contour"], w, h),
        **m,
        "mask_score": seg["mask_score"],
        "verdict": verdict,
        "defect_reasons": reasons,
        "defect_type": defect_type(verdict, m),
    }
