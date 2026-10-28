"""C2의 실제 영상 처리. 담당자는 이 파일만 채우면 된다.

두 함수 모두 서버(main.py)가 스레드풀에서 호출하므로 OpenCV를 마음껏 써도 된다.
"""

# import cv2
# import numpy as np


def preprocess(jpeg: bytes, orig_size: dict) -> tuple[bytes, dict, dict]:
    """원본 JPEG → (전처리된 JPEG, 전처리 이미지 크기 {"w","h"}, 적용 내역).

    TODO(조아람):
      1. cv2.imdecode로 디코딩
      2. PREPROC_WIDTH로 리사이즈 — 종횡비 유지, 크롭 금지 (api-spec 2.3)
      3. 노이즈 제거 (예: cv2.GaussianBlur)
      4. 밝기 보정 (예: LAB 색공간의 L 채널에 CLAHE)
      5. cv2.imencode(".jpg", ...)로 다시 인코딩
    지금은 아무것도 하지 않고 그대로 넘긴다.
    """
    return jpeg, dict(orig_size), {"note": "pass-through"}


def detect_boxes(jpeg: bytes, proc_size: dict) -> list[dict]:
    """전처리된 이미지에서 과자 후보 박스를 찾는다.

    반환 형식 (좌표는 0~1 정규화, api-spec ②):
        [{"box_id": 0, "box": [x1, y1, x2, y2], "area_ratio": 0.052}, ...]

    TODO(조아람):
      그레이스케일 → 임계값(Otsu) 또는 배경 차분 → 모폴로지 → cv2.findContours
      → 면적이 BOX_MIN_AREA_RATIO보다 작은 것 제외 → cv2.boundingRect
      → 5~10% 패딩 → MAX_BOXES개까지
    지금은 빈 목록을 반환한다.
    """
    return []
