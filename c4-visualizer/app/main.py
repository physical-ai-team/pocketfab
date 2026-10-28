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
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.draw import render
from app.tracker import Trackers

C1_URL = os.getenv("C1_URL", "http://c1-gateway:8000/v1/results")
TIMEOUT_S = float(os.getenv("C4_TIMEOUT_S", "5"))
# 추적 기록 (선택, 기본은 끔). 폴더를 지정하면 프레임마다 과자 위치·판정·추적 결과를 세션별 파일(JSONL)에 한 줄씩 남긴다.
# tools/analyze_tracking.py로 "어떤 과자를 왜 못 셌는지" 분석하고, 폰 없이 추적기를 다시 돌려 볼 수 있다.
TRACK_LOG_DIR = os.getenv("TRACK_LOG_DIR", "")

log = logging.getLogger("uvicorn.error")
client: httpx.AsyncClient
trackers = Trackers()


def now_ms() -> float:
    return time.time() * 1000


# 기록에 남기는 과자 필드: 재생에 필요한 것(위치·판정) + 왜 그렇게 판정했는지 볼 지표
LOG_FIELDS = ("box", "verdict", "defect_type", "track_id", "truncated",
              "defect_reasons", "circularity", "solidity", "fill_ratio", "concavity", "mask_score")


def log_tracking(m: dict):
    """추적 결과를 세션별 JSONL 파일에 한 줄 덧붙인다. 도착한 순서 그대로 남겨야 나중에 똑같이 다시 돌려 볼 수 있다."""
    line = {
        "session_id": m["session_id"],
        "frame_id": m["frame_id"],
        "t": m["timings"]["c1_recv"] / 1000,
        "objects": [{k: o.get(k) for k in LOG_FIELDS} for o in m.get("objects", [])],
        "tracking": {k: m["tracking"][k] for k in ("count_line_x", "direction", "used_for_tracking", "counted", "totals")},
    }
    with open(Path(TRACK_LOG_DIR) / f"{m['session_id']}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def log_image(m: dict, jpeg: bytes):
    """C4가 그린 결과 이미지를 <세션>/<frame_id>.jpg로 남긴다. 판정이 왜 틀렸는지 눈으로 볼 때 쓴다."""
    folder = Path(TRACK_LOG_DIR) / m["session_id"]
    folder.mkdir(exist_ok=True)
    (folder / f"{m['frame_id']:05d}.jpg").write_bytes(jpeg)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    client = httpx.AsyncClient(timeout=TIMEOUT_S)
    if TRACK_LOG_DIR:
        Path(TRACK_LOG_DIR).mkdir(parents=True, exist_ok=True)
        log.info("추적 기록 켜짐: %s", TRACK_LOG_DIR)
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
    if TRACK_LOG_DIR:
        log_tracking(m)  # 한 줄짜리 작은 쓰기라 이벤트 루프에서 바로 해도 된다 (추적과 같은 순서로 남기려는 것)
    image, summary = await run_in_threadpool(render, await original.read(), m)
    if TRACK_LOG_DIR:
        await run_in_threadpool(log_image, m, image)
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
