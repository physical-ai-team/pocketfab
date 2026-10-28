import os
import cv2
import numpy as np

def preprocess(jpeg: bytes, orig_size: dict) -> tuple[bytes, dict, dict]:
    # 1. 바이트(bytes) 데이터를 넘파이 배열(1차원 바이트 버퍼)로 변환
    nparr = np.frombuffer(jpeg, np.uint8)
    
    # 2. cv2.imdecode를 사용해 OpenCV가 읽을 수 있는 이미지 행렬(BGR 형태)로 디코딩
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    if img is None:
        # 이미지가 제대로 안 읽혔을 경우의 예외 처리
        return jpeg, dict(orig_size), {"note": "decode_failed"}

    # 환경변수 PREPROC_WIDTH 적용 (기본값 640)
    target_width = int(os.environ.get("PREPROC_WIDTH", 640))
    h, w = img.shape[:2]
    target_height = int(h * (target_width / w))
    
    # 2. 리사이즈 — 종횡비 유지 (크롭 금지)
    resized = cv2.resize(img, (target_width, target_height), interpolation=cv2.INTER_AREA)

    # 3. 노이즈 제거 (GaussianBlur로 잔주름이나 미세한 잡음 부드럽게 만들기)
    blurred = cv2.GaussianBlur(resized, (3, 3), 0)

    # 4. 밝기 보정 (LAB 색공간의 L 채널에 CLAHE를 적용해 그림자나 어두운 부분 살리기)
    lab = cv2.cvtColor(blurred, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    cl = clahe.apply(l)
    merged = cv2.merge((cl, a, b))
    enhanced = cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)

    # 5. 다시 JPEG 바이트 형태로 인코딩
    success, encoded_jpeg = cv2.imencode(".jpg", enhanced, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not success:
        return jpeg, dict(orig_size), {"note": "encode_failed"}

    # 최종 결과물 및 크기/정보 반환
    proc_size = {"w": target_width, "h": target_height}
    info = {"note": "preprocessed"}
    return encoded_jpeg.tobytes(), proc_size, info


def detect_boxes(jpeg: bytes, proc_size: dict) -> list[dict]:
    """전처리된 이미지에서 과자 후보 박스를 찾는다.
    반환 형식 (좌표는 0~1 정규화):
        [{"box_id": 0, "box": [x1, y1, x2, y2], "area_ratio": 0.052}, ...]
    """
    # 1. 전처리된 JPEG 바이트를 이미지 행렬로 디코딩
    nparr = np.frombuffer(jpeg, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return []

    h, w = proc_size["h"], proc_size["w"]

    # 2. 그레이스케일 변환 및 이진화 (THRESH_BINARY로 변경하여 밝은 과자가 잡히도록 수정)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # 3. 모폴로지 연산으로 작은 잡음 제거 및 구멍 메우기
    kernel = np.ones((5, 5), np.uint8)
    opening = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=2)
    sure_bg = cv2.dilate(opening, kernel, iterations=3)

    # 4. 윤곽선(Contours) 찾기
    contours, _ = cv2.findContours(sure_bg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # 면적 기준 내림차순 정렬 후 최대 30개까지만 선택
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:30]

    boxes = []
    min_area = (w * h) * 0.003  # 전체 화면의 0.3% 미만 크기의 잡음은 제외
    box_id = 0

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area:
            continue

        # 사각형 바운딩 박스 추출 (x, y, w, h)
        bx, by, bw, bh = cv2.boundingRect(cnt)

        # 박스 패딩 5% 추가 (과자 테두리가 잘리지 않도록 여유 공간 부여)
        pad_w = int(bw * 0.05)
        pad_h = int(bh * 0.05)
        bx = max(0, bx - pad_w)
        by = max(0, by - pad_h)
        bw = min(w - bx, bw + (pad_w * 2))
        bh = min(h - by, bh + (pad_h * 2))

        # 0~1 사이 값으로 좌표 정규화 [x1, y1, x2, y2]
        x1 = max(0.0, bx / w)
        y1 = max(0.0, by / h)
        x2 = min(1.0, (bx + bw) / w)
        y2 = min(1.0, (by + bh) / h)

        area_ratio = round(area / (w * h), 4)

        boxes.append({
            "box_id": box_id,
            "box": [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)],
            "area_ratio": area_ratio
        })
        box_id += 1

    return boxes