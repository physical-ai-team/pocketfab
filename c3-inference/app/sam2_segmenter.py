"""SEGMENTER=sam2: C2가 준 박스를 프롬프트로 SAM 2 마스크를 뽑는다 (api-spec 4장 ③, 7.1).

opencv_segmenter.segment()와 입출력이 같으므로 judge.build_object를 그대로 쓴다.
torch와 SAM 2는 이 파일에서만 import한다 (opencv 모드는 torch 없이 돈다).

처리 순서 (프레임 1장)
  1. set_image()  — 이미지 인코더를 1번 실행. CPU에서 가장 오래 걸리는 단계(tiny 기준 약 2초)
  2. predict(box=모든 박스)  — 박스 K개를 한 번에 디코딩. 비용은 박스 수에 비례
  3. 마스크마다 박스(+여유) 밖으로 샌 픽셀을 지우고, 가장 큰 외곽선을 고른다
"""

import os
from pathlib import Path

import cv2
import numpy as np
import torch
from sam2.build_sam import build_sam2
from sam2.sam2_image_predictor import SAM2ImagePredictor

# 모델 이름 → SAM 2 패키지 안의 설정 파일 (scripts/download_checkpoints.py가 받는 두 모델)
CONFIGS = {
    "sam2.1_hiera_tiny": "configs/sam2.1/sam2.1_hiera_t.yaml",
    "sam2.1_hiera_small": "configs/sam2.1/sam2.1_hiera_s.yaml",
}

DEVICE = os.getenv("DEVICE", "cpu")
SAM2_MODEL = os.getenv("SAM2_MODEL", "sam2.1_hiera_tiny")
SAM2_CHECKPOINT_DIR = Path(os.getenv("SAM2_CHECKPOINT_DIR", "/checkpoints"))
TORCH_NUM_THREADS = int(os.getenv("TORCH_NUM_THREADS", "4"))
# 마스크가 박스 밖으로 이만큼(박스 크기 대비)까지는 나가도 된다. 그 밖은 옆 과자 침범으로 보고 지운다.
BOX_MARGIN = 0.10

_predictor: SAM2ImagePredictor | None = None


def load() -> SAM2ImagePredictor:
    """모델을 한 번만 불러온다. 서버 시작 시 호출해 첫 요청이 느려지지 않게 한다."""
    global _predictor
    if _predictor is None:
        if SAM2_MODEL not in CONFIGS:
            raise RuntimeError(f"지원하지 않는 SAM2_MODEL: {SAM2_MODEL} (가능: {', '.join(CONFIGS)})")
        checkpoint = SAM2_CHECKPOINT_DIR / f"{SAM2_MODEL}.pt"
        if not checkpoint.exists():
            raise RuntimeError(f"체크포인트가 없습니다: {checkpoint} "
                               f"(레포 루트에서 python scripts/download_checkpoints.py 실행)")
        if DEVICE == "cpu":
            torch.set_num_threads(TORCH_NUM_THREADS)
        model = build_sam2(CONFIGS[SAM2_MODEL], str(checkpoint), device=DEVICE)
        _predictor = SAM2ImagePredictor(model)
    return _predictor


def segment(image: np.ndarray, boxes: list[dict]) -> list[dict]:
    """박스마다 SAM 2 마스크의 가장 큰 외곽선 1개.

    반환: [{"box_id", "box", "contour"(이미지 픽셀 좌표), "mask_score"}]
    """
    if not boxes:
        return []
    predictor = load()
    h, w = image.shape[:2]
    px_boxes = np.array([[b["box"][0] * w, b["box"][1] * h, b["box"][2] * w, b["box"][3] * h]
                         for b in boxes], dtype=np.float32)

    with torch.inference_mode():
        predictor.set_image(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        masks, scores, _ = predictor.predict(box=px_boxes, multimask_output=False)
    # 박스 1개면 (1, H, W), 여러 개면 (K, 1, H, W)로 나오므로 (K, H, W)로 맞춘다
    masks = np.asarray(masks).reshape(len(boxes), h, w)
    scores = np.asarray(scores).reshape(len(boxes))

    results = []
    for b, (x1, y1, x2, y2), mask, score in zip(boxes, px_boxes, masks, scores):
        mx, my = (x2 - x1) * BOX_MARGIN, (y2 - y1) * BOX_MARGIN
        keep = np.zeros((h, w), np.uint8)
        keep[max(0, int(y1 - my)):min(h, int(y2 + my) + 1), max(0, int(x1 - mx)):min(w, int(x2 + mx) + 1)] = 1
        m = ((mask > 0) & (keep > 0)).astype(np.uint8)

        contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        results.append({"box_id": b["box_id"], "box": b["box"], "contour": contour,
                        "mask_score": round(float(score), 4)})
    return results
