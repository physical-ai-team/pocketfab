"""C1 Gateway

- /ws/camera    : 스마트폰이 보내는 JPEG 프레임을 받아 C2로 보낸다 (백프레셔: MAX_IN_FLIGHT)
- /v1/results   : C4가 보낸 결과를 받아 대시보드로 중계한다
- /ws/dashboard : 대시보드에 결과와 통계를 보낸다
API 형식은 docs/api-spec.md 3장, 4장 ①④, 5장 참고.
"""

import asyncio
import base64
import io
import json
import logging
import os
import socket
import time
import uuid
from collections import Counter, deque
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import qrcode
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from PIL import Image

PUBLIC_HOST = os.getenv("PUBLIC_HOST", "localhost")
# 비워두면 C3 복제본 수 × IN_FLIGHT_PER_REPLICA로 자동 결정한다. 값을 넣으면 그 값을 우선한다.
MAX_IN_FLIGHT_ENV = os.getenv("MAX_IN_FLIGHT", "").strip()
IN_FLIGHT_PER_REPLICA = int(os.getenv("IN_FLIGHT_PER_REPLICA", "1"))
C3_SERVICE_HOST = os.getenv("C3_SERVICE_HOST", "c3-inference")
REPLICA_CHECK_INTERVAL_S = 5
C2_URL = os.getenv("C2_URL", "http://c2-preprocessing:8000/v1/frames")
PIPELINE_TIMEOUT_S = float(os.getenv("C1_PIPELINE_TIMEOUT_S", "10"))

STATIC_DIR = Path(__file__).parent / "static"
log = logging.getLogger("uvicorn.error")


def now_ms() -> float:
    return time.time() * 1000


class Window:
    """최근 1초 동안 일어난 이벤트 수를 센다."""

    def __init__(self):
        self.events = deque()  # (시각, 태그)

    def add(self, tag=None):
        self.events.append((time.monotonic(), tag))

    def _trim(self):
        limit = time.monotonic() - 1.0
        while self.events and self.events[0][0] < limit:
            self.events.popleft()

    def count(self) -> int:
        self._trim()
        return len(self.events)

    def count_by_tag(self) -> dict:
        self._trim()
        return dict(Counter(tag for _, tag in self.events if tag))


class Session:
    """카메라 WebSocket 연결 1개."""

    def __init__(self):
        self.id = uuid.uuid4().hex[:8]
        self.next_frame_id = 0
        self.last_shown = -1  # 대시보드에 마지막으로 표시한 frame_id
        self.sent = self.accepted = self.dropped = self.processed_total = 0
        self.processed = Window()


# ── 전역 상태 (asyncio 단일 스레드에서만 바뀌므로 잠금 불필요) ──
sessions: dict[str, Session] = {}
dashboards: set[WebSocket] = set()
pipeline_tasks: set[asyncio.Task] = set()
in_flight = 0
max_in_flight = int(MAX_IN_FLIGHT_ENV) if MAX_IN_FLIGHT_ENV else IN_FLIGHT_PER_REPLICA
c3_replicas: int | None = None  # DNS로 센 C3 복제본 수 (모르면 None)
win_input, win_processed, win_dropped = Window(), Window(), Window()
totals = {"received": 0, "accepted": 0, "processed": 0, "stale": 0}
drops = Counter()      # "stage:reason" → 횟수
replicas = Counter()   # C3 replica → 처리 횟수
client: httpx.AsyncClient


def record_drop(session: Session | None, stage: str, reason: str):
    drops[f"{stage}:{reason}"] += 1
    win_dropped.add()
    if session:
        session.dropped += 1


# ── 파이프라인으로 보내기 ────────────────────────────────

def handle_frame(session: Session, jpeg: bytes):
    global in_flight
    session.sent += 1
    totals["received"] += 1
    win_input.add()

    # 백프레셔: 처리 중인 프레임이 많으면 새 프레임은 버린다
    if in_flight >= max_in_flight:
        record_drop(session, "c1", "c1_busy")
        return

    try:
        w, h = Image.open(io.BytesIO(jpeg)).size  # 헤더만 읽으므로 빠르다
    except Exception:
        record_drop(session, "c1", "decode_error")
        return

    meta = {
        "session_id": session.id,
        "frame_id": session.next_frame_id,
        "orig_size": {"w": w, "h": h},
        "timings": {"c1_recv": now_ms()},
    }
    session.next_frame_id += 1
    session.accepted += 1
    totals["accepted"] += 1
    in_flight += 1

    task = asyncio.create_task(run_pipeline(session, meta, jpeg))
    pipeline_tasks.add(task)  # 작업이 중간에 사라지지 않도록 참조를 보관
    task.add_done_callback(pipeline_tasks.discard)


async def run_pipeline(session: Session, meta: dict, jpeg: bytes):
    """C2로 보내고, 체인 끝(C4)까지 끝날 때까지 기다린다 (api-spec 1.1)."""
    global in_flight
    try:
        files = {
            "meta": (None, json.dumps(meta), "application/json"),
            "original": ("frame.jpg", jpeg, "image/jpeg"),
        }
        r = await client.post(C2_URL, files=files)
        r.raise_for_status()
        body = r.json()
        if body.get("status") == "dropped":
            record_drop(session, body.get("stage") or "?", body.get("drop_reason") or "?")
    except httpx.TimeoutException:
        record_drop(session, "c1", "timeout")
    except Exception as e:
        log.warning("C2 호출 실패: %r", e)
        record_drop(session, "c1", "upstream_error")
    finally:
        in_flight -= 1


# ── 통계 ──────────────────────────────────────────────

def dashboard_stats() -> dict:
    n_in = win_input.count()
    return {
        "type": "stats",
        "input_fps": n_in,
        "processed_fps": win_processed.count(),
        "drop_rate": round(win_dropped.count() / n_in, 3) if n_in else 0.0,
        "in_flight": in_flight,
        "max_in_flight": max_in_flight,
        "c3_replicas": c3_replicas,
        "replicas": win_processed.count_by_tag(),
    }


async def broadcast(message: dict):
    for ws in list(dashboards):
        try:
            await ws.send_json(message)
        except Exception:
            dashboards.discard(ws)


async def stats_loop():
    while True:
        await asyncio.sleep(1)
        await broadcast(dashboard_stats())


async def count_c3_replicas() -> int | None:
    """Docker 내장 DNS는 서비스 이름에 복제본 전체의 IP를 돌려준다. 그 개수가 복제본 수다."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(C3_SERVICE_HOST, 8000, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return None  # Docker 밖에서 실행했거나 C3가 아직 없음
    return len({info[4][0] for info in infos})


async def replica_watch_loop():
    """C3 복제본 수를 주기적으로 확인한다. --scale로 바꾸면 몇 초 안에 한도가 따라간다."""
    global c3_replicas, max_in_flight
    while True:
        n = await count_c3_replicas()
        if n != c3_replicas:
            c3_replicas = n
            if not MAX_IN_FLIGHT_ENV:
                max_in_flight = max(1, (n or 1) * IN_FLIGHT_PER_REPLICA)
            log.info("C3 복제본 %s개 → MAX_IN_FLIGHT=%d (%s)", n if n is not None else "?",
                     max_in_flight, "지정값" if MAX_IN_FLIGHT_ENV else "자동")
        await asyncio.sleep(REPLICA_CHECK_INTERVAL_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    client = httpx.AsyncClient(timeout=PIPELINE_TIMEOUT_S)
    tasks = [asyncio.create_task(stats_loop()), asyncio.create_task(replica_watch_loop())]
    log.info("C1 시작: PUBLIC_HOST=%s", PUBLIC_HOST)
    yield
    for t in tasks:
        t.cancel()
    await client.aclose()


app = FastAPI(title="C1 Gateway", lifespan=lifespan)


# ── 페이지 ────────────────────────────────────────────

@app.get("/")
async def dashboard_page():
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/camera")
async def camera_page():
    return FileResponse(STATIC_DIR / "camera.html")


@app.get("/qr")
async def qr():
    buf = io.BytesIO()
    qrcode.make(f"https://{PUBLIC_HOST}/camera").save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")


@app.get("/v1/config")
async def config():
    return {
        "camera_url": f"https://{PUBLIC_HOST}/camera",
        "max_in_flight": max_in_flight,
        "max_in_flight_source": "env" if MAX_IN_FLIGHT_ENV else "auto",
        "c3_replicas": c3_replicas,
    }


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "c1-gateway", "replica": socket.gethostname()}


@app.get("/v1/metrics")
async def metrics():
    return {
        "totals": totals,
        "drops": dict(drops),
        "replicas": dict(replicas),
        "now": dashboard_stats(),
    }


# ── WebSocket ─────────────────────────────────────────

@app.websocket("/ws/camera")
async def ws_camera(ws: WebSocket):
    await ws.accept()
    session = Session()
    sessions[session.id] = session
    log.info("카메라 연결: session=%s", session.id)

    async def push_stats():
        while True:
            await asyncio.sleep(1)
            await ws.send_json({
                "type": "stats", "session_id": session.id,
                "sent": session.sent, "accepted": session.accepted,
                "dropped": session.dropped, "processed": session.processed_total,
                "processed_fps": session.processed.count(),
            })

    stats_task = asyncio.create_task(push_stats())
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes"):
                handle_frame(session, msg["bytes"])
            elif msg.get("text"):
                log.info("카메라 메시지(session=%s): %s", session.id, msg["text"])
    except WebSocketDisconnect:
        pass
    finally:
        stats_task.cancel()
        sessions.pop(session.id, None)
        log.info("카메라 연결 종료: session=%s", session.id)


@app.websocket("/ws/dashboard")
async def ws_dashboard(ws: WebSocket):
    await ws.accept()
    dashboards.add(ws)
    try:
        while True:
            await ws.receive_text()  # 대시보드는 보내는 게 없지만 연결 유지를 위해 대기
    except WebSocketDisconnect:
        pass
    finally:
        dashboards.discard(ws)


# ── C4 → C1 결과 수신 ─────────────────────────────────

def latency_ms(t: dict) -> dict:
    def diff(a, b):
        return round(t[b] - t[a], 1) if a in t and b in t else None

    return {
        "total": diff("c1_recv", "c1_result_recv"),
        "c2": diff("c2_start", "c2_end"),
        "c3_infer": diff("c3_infer_start", "c3_infer_end"),
        "c4": diff("c4_start", "c4_end"),
    }


@app.post("/v1/results")
async def results(meta: str = Form(...), image: UploadFile = File(...)):
    m = json.loads(meta)
    m["timings"]["c1_result_recv"] = now_ms()
    jpeg = await image.read()

    # C3 복제본이 여러 개라 결과가 순서 없이 올 수 있다. 이미 더 최신 프레임을 보여줬으면 버린다.
    session = sessions.get(m["session_id"])
    if session:
        if m["frame_id"] <= session.last_shown:
            totals["stale"] += 1
            return {"ok": True, "shown": False}
        session.last_shown = m["frame_id"]
        session.processed.add()
        session.processed_total += 1

    replica = (m.get("inference") or {}).get("replica")
    totals["processed"] += 1
    win_processed.add(replica)
    if replica:
        replicas[replica] += 1

    await broadcast({
        "type": "result",
        "session_id": m["session_id"],
        "frame_id": m["frame_id"],
        "image_b64": base64.b64encode(jpeg).decode(),
        "summary": m.get("summary"),
        "objects": [
            {k: o.get(k) for k in ("box_id", "circularity", "solidity", "fill_ratio", "verdict", "defect_reasons")}
            for o in m.get("objects", [])
        ],
        "latency_ms": latency_ms(m["timings"]),
        "replica": replica,
        "tracking": m.get("tracking"),  # C4의 과자 단위 누적 집계 (api-spec 4.2)
    })
    return {"ok": True, "shown": True}
