"""C2 Preprocessing

① C1 → C2  POST /v1/frames
② C2 → C3  POST {C3_URL} (Nginx 경유)
API 형식은 docs/api-spec.md 4장 ①② 참고.
"""

import json
import logging
import os
import socket
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.preprocess import detect_boxes, preprocess

C3_URL = os.getenv("C3_URL", "http://nginx:8080/v1/infer")
C4_URL = os.getenv("C4_URL", "http://c4-visualizer:8000/v1/render")
# 박스가 없으면 C3를 건너뛴다 (api-spec ②). 박스 검출을 구현하기 전에는 false로 두어 C3까지 흘려보낸다.
SKIP_C3_IF_NO_BOXES = os.getenv("SKIP_C3_IF_NO_BOXES", "false").lower() == "true"
TIMEOUT_S = float(os.getenv("C2_TIMEOUT_S", "10"))

log = logging.getLogger("uvicorn.error")
client: httpx.AsyncClient


def now_ms() -> float:
    return time.time() * 1000


def dropped(frame_id: int, reason: str, stage: str = "c2") -> dict:
    return {"frame_id": frame_id, "status": "dropped", "drop_reason": reason, "stage": stage}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    client = httpx.AsyncClient(timeout=TIMEOUT_S)
    yield
    await client.aclose()


app = FastAPI(title="C2 Preprocessing", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "c2-preprocessing", "replica": socket.gethostname()}


@app.post("/v1/frames")
async def frames(meta: str = Form(...), original: UploadFile = File(...)):
    m = json.loads(meta)
    m["timings"]["c2_start"] = now_ms()
    orig = await original.read()

    # OpenCV 연산은 CPU를 오래 쓰므로 스레드풀에서 돌려 서버가 멈추지 않게 한다
    image, proc_size, info = await run_in_threadpool(preprocess, orig, m["orig_size"])
    boxes = await run_in_threadpool(detect_boxes, image, proc_size)

    m["proc_size"] = proc_size
    m["preprocess"] = info
    m["boxes"] = boxes
    m["timings"]["c2_end"] = now_ms()

    if not boxes and SKIP_C3_IF_NO_BOXES:
        # 과자가 없는 프레임: SAM 2를 건너뛰고 바로 C4로 (③과 같은 형식)
        m["inference"] = None
        m["objects"] = []
        url, files = C4_URL, {"original": ("frame.jpg", orig, "image/jpeg")}
    else:
        url, files = C3_URL, {
            "image": ("image.jpg", image, "image/jpeg"),
            "original": ("frame.jpg", orig, "image/jpeg"),
        }
    files["meta"] = (None, json.dumps(m), "application/json")

    try:
        r = await client.post(url, files=files)
        if r.status_code == 503:
            return dropped(m["frame_id"], "c3_busy")
        r.raise_for_status()
        return r.json()
    except httpx.TimeoutException:
        return dropped(m["frame_id"], "timeout")
    except httpx.HTTPError as e:
        log.warning("다음 단계 호출 실패 (%s): %r", url, e)
        return dropped(m["frame_id"], "upstream_error")
