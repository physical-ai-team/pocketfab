"""과자 단위 집계: 프레임 사이 추적 → 검사선 통과 시 1회 집계 → 다수결 판정 (api-spec 4.2).

한 과자는 화면을 지나가는 동안 여러 프레임에 찍힌다. 프레임마다 세면 같은 과자를 여러 번 세게 되므로,
같은 과자를 프레임 사이에 이어 붙이고(추적), 검사선을 지나는 순간 한 번만 센다.
판정은 그 과자가 온전히 보였던 프레임들의 판정을 모아 다수결로 정한다.

추적 상태가 이 프로세스의 메모리에 있으므로 **C4는 복제하지 않는다.**
"""

import os
import time
from dataclasses import dataclass, field

COUNT_LINE_X = float(os.getenv("COUNT_LINE_X", "0.34"))   # 검사선 x (0~1). 시뮬레이터 기본값과 같다
COUNT_DIRECTION = os.getenv("COUNT_DIRECTION", "left")    # 과자가 움직이는 방향: left / right
TRACK_MAX_SPEED = float(os.getenv("TRACK_MAX_SPEED", "1.0"))  # 화면 폭/초. 이보다 빨리 움직이면 다른 과자로 본다
TRACK_MAX_AGE_S = float(os.getenv("TRACK_MAX_AGE_S", "2.0"))  # 이 시간 동안 안 보이면 추적을 끝낸다
EDGE_MARGIN = float(os.getenv("EDGE_MARGIN", "0.01"))     # 박스가 화면 끝에서 이만큼 안에 닿으면 잘린 과자로 본다

BACKTRACK_TOLERANCE = 0.01  # 반대 방향으로 이만큼은 흔들려도 같은 과자로 인정
SESSION_IDLE_S = 60         # 이 시간 동안 프레임이 없는 세션의 추적 상태는 지운다
SIGN = -1 if COUNT_DIRECTION == "left" else 1


def is_truncated(box: list[float]) -> bool:
    x1, y1, x2, y2 = box
    return x1 <= EDGE_MARGIN or y1 <= EDGE_MARGIN or x2 >= 1 - EDGE_MARGIN or y2 >= 1 - EDGE_MARGIN


def majority(votes: dict) -> str:
    """정상/불량 중 많은 쪽. 같으면 불량(검사에서는 놓치는 쪽이 더 위험하다). 판단불가는 세지 않는다."""
    if votes["normal"] == votes["defect"] == 0:
        return "uncertain"
    return "defect" if votes["defect"] >= votes["normal"] else "normal"


@dataclass
class Track:
    id: int
    x: float  # 박스 중심 (0~1)
    y: float
    w: float  # 박스 크기 (예측 위치와 얼마나 어긋나도 되는지, 같은 라인인지 판단할 때 씀)
    h: float
    t: float  # 마지막으로 본 시각 (초)
    vx: float | None = None  # 가로 속도 (화면 폭/초). 두 번 이상 본 뒤에 생긴다
    votes: dict = field(default_factory=lambda: {"normal": 0, "defect": 0, "uncertain": 0})
    counted: bool = False

    def predict_x(self, t: float) -> float:
        """시각 t에 있을 것으로 예상되는 x. 안 보이던 동안에도 계속 움직였다고 본다."""
        return self.x + (self.vx or 0.0) * (t - self.t)


class SessionTracker:
    """카메라 세션 1개의 추적 상태."""

    def __init__(self):
        self.tracks: list[Track] = []
        self.next_id = 1
        self.last_frame_id = -1
        self.totals = {"total": 0, "normal": 0, "defect": 0, "uncertain": 0}
        self.touched = time.monotonic()

    def update(self, frame_id: int, t: float, objects: list[dict]) -> dict:
        """objects에 track_id·truncated를 채우고, 이번 프레임의 집계 결과를 돌려준다."""
        self.touched = time.monotonic()
        counted_now = []

        for o in objects:
            o["truncated"] = is_truncated(o["box"])
            o["track_id"] = None

        # C3 복제본이 여러 개라 프레임이 순서 없이 온다. 이미 지난 프레임은 추적에 쓰지 않는다.
        if frame_id <= self.last_frame_id:
            return self._result(counted_now, used=False)
        self.last_frame_id = frame_id
        # 오래 안 보였거나, 예측 위치가 화면 밖으로 나간 추적은 끝낸다
        # (남겨 두면 뒤따라온 과자가 그 자리에 왔을 때 엉뚱한 추적에 붙는다)
        self.tracks = [tr for tr in self.tracks
                       if t - tr.t <= TRACK_MAX_AGE_S and -tr.w <= tr.predict_x(t) <= 1 + tr.w]

        # 1. 잘리지 않은 과자만 기존 추적과 이어 붙인다:
        #    진행 방향으로 움직였고, 같은 라인이고, 예측 위치에 가장 가까운 것
        dets = [o for o in objects if not o["truncated"]]
        candidates = []
        for tr in self.tracks:
            max_move = TRACK_MAX_SPEED * max(t - tr.t, 1e-3) + BACKTRACK_TOLERANCE
            expected_x = tr.predict_x(t)
            for i, o in enumerate(dets):
                cx, cy, w, h = center(o["box"])
                advance = SIGN * (cx - tr.x)
                if not (-BACKTRACK_TOLERANCE <= advance <= max_move):
                    continue
                if abs(cy - tr.y) > 0.5 * max(tr.h, h):
                    continue
                miss = abs(cx - expected_x)
                if tr.vx is not None and miss > 0.5 * max(tr.w, w):  # 속도를 알면 예측 위치 근처만 인정
                    continue
                candidates.append((miss + abs(cy - tr.y), tr.id, i))

        matched_tracks, matched_dets = {}, set()
        by_id = {tr.id: tr for tr in self.tracks}
        for _, tid, i in sorted(candidates):
            if tid in matched_tracks or i in matched_dets:
                continue
            matched_tracks[tid] = i
            matched_dets.add(i)

        # 2. 이어 붙인 추적은 위치를 갱신하고, 검사선을 넘었으면 한 번만 센다
        for tid, i in matched_tracks.items():
            tr, o = by_id[tid], dets[i]
            prev_x, prev_t = tr.x, tr.t
            tr.x, tr.y, tr.w, tr.h = center(o["box"])
            tr.t = t
            if t > prev_t:
                v = (tr.x - prev_x) / (t - prev_t)
                tr.vx = v if tr.vx is None else 0.5 * tr.vx + 0.5 * v  # 흔들림을 줄이려고 이전 값과 섞는다
            tr.votes[o["verdict"]] += 1
            o["track_id"] = tr.id
            if not tr.counted and SIGN * prev_x < SIGN * COUNT_LINE_X <= SIGN * tr.x:
                tr.counted = True
                verdict = majority(tr.votes)
                self.totals["total"] += 1
                self.totals[verdict] += 1
                counted_now.append({"track_id": tr.id, "verdict": verdict, "votes": dict(tr.votes)})

        # 3. 이어지지 않은 과자는 새 추적을 시작한다
        for i, o in enumerate(dets):
            if i in matched_dets:
                continue
            cx, cy, w, h = center(o["box"])
            tr = Track(id=self.next_id, x=cx, y=cy, w=w, h=h, t=t)
            tr.votes[o["verdict"]] += 1
            self.next_id += 1
            self.tracks.append(tr)
            o["track_id"] = tr.id

        return self._result(counted_now, used=True)

    def _result(self, counted_now: list[dict], used: bool) -> dict:
        return {
            "count_line_x": COUNT_LINE_X,
            "direction": COUNT_DIRECTION,
            "used_for_tracking": used,
            "counted": counted_now,
            "totals": dict(self.totals),
            "active_tracks": len(self.tracks),
        }


def center(box: list[float]) -> tuple[float, float, float, float]:
    """(중심 x, 중심 y, 폭, 높이)"""
    x1, y1, x2, y2 = box
    return (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1


class Trackers:
    """세션별 SessionTracker 모음. 서버(main.py)는 이벤트 루프에서만 호출하므로 잠금이 필요 없다."""

    def __init__(self):
        self.sessions: dict[str, SessionTracker] = {}

    def update(self, meta: dict) -> dict:
        now = time.monotonic()
        for sid in [s for s, st in self.sessions.items() if now - st.touched > SESSION_IDLE_S]:
            del self.sessions[sid]
        st = self.sessions.setdefault(meta["session_id"], SessionTracker())
        t = meta["timings"]["c1_recv"] / 1000  # 프레임이 찍힌 시각에 가장 가까운 값
        return st.update(meta["frame_id"], t, meta.get("objects", []))
