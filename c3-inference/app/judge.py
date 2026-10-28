"""윤곽선 → 원형도·solidity → 판정. SAM 2 모드와 OpenCV 모드가 함께 쓴다 (api-spec 4.1)."""

import math
import os

import cv2
import numpy as np

CIRCULARITY_THRESHOLD = float(os.getenv("CIRCULARITY_THRESHOLD", "0.80"))
SOLIDITY_THRESHOLD = float(os.getenv("SOLIDITY_THRESHOLD", "0.95"))
MASK_SCORE_MIN = float(os.getenv("MASK_SCORE_MIN", "0.5"))
MAX_POLYGON_POINTS = 64


def measure(contour: np.ndarray) -> dict:
    """픽셀 좌표 윤곽선의 면적, 둘레, 원형도, solidity."""
    area = cv2.contourArea(contour)
    perimeter = cv2.arcLength(contour, closed=True)
    hull_area = cv2.contourArea(cv2.convexHull(contour))
    return {
        "area_px": round(area, 1),
        "perimeter_px": round(perimeter, 1),
        "circularity": round(4 * math.pi * area / perimeter**2, 4) if perimeter else 0.0,
        "solidity": round(area / hull_area, 4) if hull_area else 0.0,
    }


def judge(circularity: float, solidity: float, mask_score: float | None) -> tuple[str, list[str]]:
    """(verdict, defect_reasons). mask_score가 None이면(OpenCV 모드) 1단계를 건너뛴다."""
    if mask_score is not None and mask_score < MASK_SCORE_MIN:
        return "uncertain", []
    reasons = []
    if circularity < CIRCULARITY_THRESHOLD:
        reasons.append("circularity")
    if solidity < SOLIDITY_THRESHOLD:
        reasons.append("solidity")
    return ("defect" if reasons else "normal"), reasons


def to_polygon(contour: np.ndarray, w: int, h: int) -> list[list[float]]:
    """윤곽선을 단순화해 0~1 정규화 좌표로 바꾼다."""
    approx = cv2.approxPolyDP(contour, 0.005 * cv2.arcLength(contour, True), True).reshape(-1, 2)
    if len(approx) > MAX_POLYGON_POINTS:
        approx = approx[np.linspace(0, len(approx) - 1, MAX_POLYGON_POINTS).astype(int)]
    return [[round(x / w, 4), round(y / h, 4)] for x, y in approx]


def build_object(seg: dict, w: int, h: int) -> dict:
    """세그멘터 결과 1개 → api-spec ③의 objects[] 항목."""
    m = measure(seg["contour"])
    verdict, reasons = judge(m["circularity"], m["solidity"], seg["mask_score"])
    return {
        "box_id": seg["box_id"],
        "box": seg["box"],
        "polygon": to_polygon(seg["contour"], w, h),
        **m,
        "mask_score": seg["mask_score"],
        "verdict": verdict,
        "defect_reasons": reasons,
    }
