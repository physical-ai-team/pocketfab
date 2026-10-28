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

    # 2. 색으로 이진화한다: 과자는 노랑·주황(따뜻한 색), 벨트는 회색·하늘색(차가운 색)이다.
    #    LAB 색공간의 b 채널은 "파랑(작음) ↔ 노랑(큼)" 축이라, 이 값에 Otsu를 쓰면 과자가 흰색으로 남는다.
    #    밝기(흑백)로 나누면 모니터 반사로 하얗게 뜬 과자와 벨트를 구분하지 못한다 (실측: 아래 줄 검출 2.3배 차이).
    warmth = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)[:, :, 2]
    _, thresh = cv2.threshold(warmth, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # 3. 모폴로지: 닫힘으로 초코칩 구멍을 먼저 메우고, 열림으로 작은 잡음 제거
    kernel = np.ones((5, 5), np.uint8)
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)
    opening = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel, iterations=2)

    # 4. 윤곽선(Contours) 찾기
    contours, _ = cv2.findContours(opening, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # 면적 기준 내림차순 정렬 후 최대 30개까지만 선택
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:30]

    min_area = (w * h) * 0.003  # 화면의 0.3% 미만은 잡음으로 보고 버림
    blobs = []  # (윤곽선 면적, 윤곽선)
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area >= min_area:
            blobs.append((area, cnt))

    # 5. 과자 하나로 볼 수 없는 덩어리는 버린다 (모니터 반사로 밝아진 배경, 붙어 버린 과자 두 개, 벨트 테두리 띠 등)
    blobs = drop_non_cookies(blobs, w, h)

    # 6. 박스를 "과자 한 개 크기"에 맞춘다 (가장자리가 어둡게 찍힌 과자는 밝은 가운데만 잡혀 박스가 작아진다)
    rects = grow_to_cookie_size([cv2.boundingRect(cnt) for _, cnt in blobs], w, h)

    boxes = []
    box_id = 0
    for (area, _), (bx, by, bw, bh) in zip(blobs, rects):
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


# 과자가 아닌 덩어리를 거르는 기준. 시뮬레이터 과자는 모두 지름이 같다는 점을 이용한다.
MAX_AREA_VS_MEDIAN = 1.6   # 면적이 "보통 과자"(중간값)의 1.6배를 넘으면 과자 두 개 이상이 붙었거나 배경이다
MIN_ASPECT, MAX_ASPECT = 0.4, 2.5  # 가로/세로 비. 반쪽 과자(half)가 약 2라서 2.5까지는 허용한다


def drop_non_cookies(blobs: list, w: int, h: int) -> list:
    """(면적, 윤곽선) 목록에서 과자로 보기 어려운 것을 뺀다.

    - 너무 큰 덩어리: 면적 중간값의 MAX_AREA_VS_MEDIAN배 초과 (덩어리가 3개 이상일 때만. 적으면 중간값을 믿기 어렵다)
    - 너무 길쭉한 덩어리: 가로/세로 비가 MIN_ASPECT~MAX_ASPECT 밖.
      단, 좌우 끝에 걸린 과자는 세로로 가늘게 잘리므로 "너무 가는 쪽"(MIN_ASPECT 미만)만 봐준다.
      가로로 긴 띠(MAX_ASPECT 초과)는 어디에 있든 과자가 아니다 (화면 테두리 띠, 벨트 경계선 등)
    """
    if len(blobs) >= 3:
        median = float(np.median([a for a, _ in blobs]))
        blobs = [(a, c) for a, c in blobs if a <= median * MAX_AREA_VS_MEDIAN]

    kept = []
    for a, c in blobs:
        bx, by, bw, bh = cv2.boundingRect(c)
        on_side_edge = bx <= 1 or bx + bw >= w - 1
        aspect = bw / bh
        if aspect <= MAX_ASPECT and (aspect >= MIN_ASPECT or on_side_edge):
            kept.append((a, c))
    return kept


def grow_to_cookie_size(rects: list, w: int, h: int) -> list:
    """(x, y, 폭, 높이) 박스들 중 "보통 과자"(폭·높이 중간값)보다 작은 박스를 중심은 그대로 둔 채 그 크기까지 넓힌다.

    폰으로 모니터를 비스듬히 찍으면 한 줄 전체의 과자 가장자리가 어둡게 찍힌다. Otsu는 밝은 가운데만 과자로 잡아
    박스가 과자보다 작아지고, C3(SAM 2)는 박스 밖으로 많이 나간 마스크를 지우므로 정상 과자가 잘려 불량이 된다.
    모든 과자는 지름이 같으니, 작은 박스는 보통 과자 크기까지 넓혀도 된다. 화면 끝에 걸린 박스는 잘린 과자라 그대로 둔다.
    """
    def on_edge(r):
        x, y, rw, rh = r
        return x <= 1 or y <= 1 or x + rw >= w - 1 or y + rh >= h - 1

    inner = [r for r in rects if not on_edge(r)]
    if len(inner) < 3:  # 적으면 중간값을 믿기 어렵다
        return rects
    med_w = int(np.median([r[2] for r in inner]))
    med_h = int(np.median([r[3] for r in inner]))

    out = []
    for r in rects:
        x, y, rw, rh = r
        if not on_edge(r) and (rw < med_w or rh < med_h):
            cx, cy = x + rw / 2, y + rh / 2
            rw, rh = max(rw, med_w), max(rh, med_h)
            x = int(max(0, min(w - rw, cx - rw / 2)))
            y = int(max(0, min(h - rh, cy - rh / 2)))
        out.append((x, y, rw, rh))
    return out