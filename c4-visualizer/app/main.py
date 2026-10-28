"""C4 Visualizer

③ C3 → C4  POST /v1/render
④ C4 → C1  POST {C1_URL}
API 형식은 docs/api-spec.md 4장 ③④, 과자 단위 집계는 4.2 참고.

**C4는 복제하지 않는다.** 과자 추적 상태(app/tracker.py)가 이 프로세스 메모리에 있어서,
여러 개로 늘리면 같은 과자의 프레임이 서로 다른 C4로 흩어져 집계가 틀어진다.
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

from app.draw import render
from app.tracker import Trackers

C1_URL = os.getenv("C1_URL", "http://c1-gateway:8000/v1/results")
TIMEOUT_S = float(os.getenv("C4_TIMEOUT_S", "5"))

log = logging.getLogger("uvicorn.error")
client: httpx.AsyncClient
trackers = Trackers()


def now_ms() -> float:
    return time.time() * 1000


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    client = httpx.AsyncClient(timeout=TIMEOUT_S)
    yield
    await client.aclose()


app = FastAPI(title="C4 Visualizer", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "c4-visualizer", "replica": socket.gethostname()}


@app.post("/v1/render")
async def render_endpoint(meta: str = Form(...), original: UploadFile = File(...)):
    m = json.loads(meta)
    m["timings"]["c4_start"] = now_ms()

    # 추적은 이벤트 루프에서 순서대로 처리한다 (스레드풀에서 돌리면 프레임끼리 상태가 꼬인다)
    m["tracking"] = trackers.update(m)
    image, summary = await run_in_threadpool(render, await original.read(), m)
    m["summary"] = summary
    m["timings"]["c4_end"] = now_ms()

    files = {
        "meta": (None, json.dumps(m), "application/json"),
        "image": ("result.jpg", image, "image/jpeg"),
    }
    try:
        r = await client.post(C1_URL, files=files)
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("C1 전송 실패: %r", e)
        return {"frame_id": m["frame_id"], "status": "dropped", "drop_reason": "upstream_error", "stage": "c4"}
    return {"frame_id": m["frame_id"], "status": "processed", "drop_reason": None, "stage": "c4"}
