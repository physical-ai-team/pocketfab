"""C3 Inference

② C2 → C3  POST /v1/infer (Nginx 경유)
③ C3 → C4  POST {C4_URL}
API 형식은 docs/api-spec.md 4장 ②③ 참고.

복제본 1개는 한 번에 프레임 1장만 추론한다. 추론 중에 요청이 오면 503을 돌려주고,
Nginx가 다른 복제본으로 넘긴다 (모두 바쁘면 C2가 c3_busy로 버린다).
"""

import asyncio
import json
import logging
import os
import socket
import time
from contextlib import asynccontextmanager

import cv2
import httpx
import numpy as np
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app import judge, opencv_segmenter

SEGMENTER = os.getenv("SEGMENTER", "opencv")
DEVICE = os.getenv("DEVICE", "cpu")
SAM2_MODEL = os.getenv("SAM2_MODEL", "sam2.1_hiera_tiny")
C4_URL = os.getenv("C4_URL", "http://c4-visualizer:8000/v1/render")
TIMEOUT_S = float(os.getenv("C3_TIMEOUT_S", "10"))
# 추론 시간을 흉내 내는 지연(ms). SAM 2 없이 로드밸런싱·백프레셔를 시험할 때 쓴다 (CPU는 쓰지 않음).
SIMULATED_INFER_MS = float(os.getenv("SIMULATED_INFER_MS", "0"))
REPLICA = socket.gethostname()

log = logging.getLogger("uvicorn.error")
client: httpx.AsyncClient
busy = asyncio.Semaphore(1)


def now_ms() -> float:
    return time.time() * 1000


def load_segmenter():
    if SEGMENTER == "opencv":
        return opencv_segmenter.segment
    if SEGMENTER == "sam2":
        # TODO(박민성): app/sam2_segmenter.py를 만들고 여기서 import한다.
        # torch는 이 분기 안에서만 import해야 opencv 모드가 torch 없이 돈다.
        raise RuntimeError("SEGMENTER=sam2는 아직 구현되지 않았습니다. SEGMENTER=opencv를 쓰세요.")
    raise RuntimeError(f"알 수 없는 SEGMENTER: {SEGMENTER}")


segment = load_segmenter()


def infer(jpeg: bytes, boxes: list[dict]) -> list[dict]:
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    h, w = image.shape[:2]
    return [judge.build_object(seg, w, h) for seg in segment(image, boxes)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    client = httpx.AsyncClient(timeout=TIMEOUT_S)
    log.info("C3 시작: replica=%s segmenter=%s device=%s", REPLICA, SEGMENTER, DEVICE)
    yield
    await client.aclose()


app = FastAPI(title="C3 Inference", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "c3-inference", "replica": REPLICA}


@app.post("/v1/infer")
async def infer_endpoint(
    meta: str = Form(...),
    image: UploadFile = File(...),
    original: UploadFile = File(...),
):
    m = json.loads(meta)
    m["timings"]["c3_start"] = now_ms()
    jpeg = await image.read()

    # 확인과 잠금 사이에 await가 없어야 두 요청이 동시에 통과하지 않는다
    if busy.locked():
        return JSONResponse(status_code=503, content={
            "frame_id": m["frame_id"], "status": "dropped", "drop_reason": "c3_busy", "stage": "c3"})
    async with busy:
        m["timings"]["c3_infer_start"] = now_ms()
        if SIMULATED_INFER_MS:
            await asyncio.sleep(SIMULATED_INFER_MS / 1000)
        objects = await run_in_threadpool(infer, jpeg, m.get("boxes", []))
        m["timings"]["c3_infer_end"] = now_ms()

    m["inference"] = {
        "replica": REPLICA,
        "segmenter": SEGMENTER,
        "model": SAM2_MODEL if SEGMENTER == "sam2" else None,
        "device": DEVICE,
        "circularity_threshold": judge.CIRCULARITY_THRESHOLD,
        "solidity_threshold": judge.SOLIDITY_THRESHOLD,
    }
    m["objects"] = objects
    m["timings"]["c3_end"] = now_ms()

    # 추론이 끝났으니 잠금을 푼 상태에서 C4로 넘긴다 (그동안 다음 프레임을 받을 수 있음)
    files = {
        "meta": (None, json.dumps(m), "application/json"),
        "original": ("frame.jpg", await original.read(), "image/jpeg"),
    }
    try:
        r = await client.post(C4_URL, files=files)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        log.warning("C4 호출 실패: %r", e)
        return {"frame_id": m["frame_id"], "status": "dropped", "drop_reason": "upstream_error", "stage": "c3"}
