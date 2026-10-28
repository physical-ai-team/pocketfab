"""과자 단위 집계 테스트. 실행: c4-visualizer 폴더에서 `pytest`

시뮬레이터 기본값과 비슷한 장면을 만든다: 화면 폭 900px, 지름 78px, 간격 90px, 속도 130px/s, 3개 라인.
"""

import copy

from app import tracker
from app.tracker import SessionTracker

W, H = 900, 450
D, GAP, SPEED = 78, 90, 130
LINES_Y = [75, 225, 375]


def scene(t: float, verdict_of=lambda line, k: "normal", speed=SPEED) -> list[dict]:
    """시각 t의 화면에 보이는 과자 목록 (C3 출력 objects 형식)."""
    objs = []
    pitch = D + GAP
    for line, cy in enumerate(LINES_Y):
        for k in range(40):  # k번째 과자: 처음엔 화면 오른쪽 밖에 있다가 왼쪽으로 이동
            cx = W + D / 2 + k * pitch - speed * t
            if cx + D / 2 < 0 or cx - D / 2 > W:
                continue
            box = [max(cx - D / 2, 0) / W, (cy - D / 2) / H, min(cx + D / 2, W) / W, (cy + D / 2) / H]
            objs.append({"box_id": len(objs), "box": box, "verdict": verdict_of(line, k)})
    return objs


def crossed(t_end: float, speed=SPEED) -> int:
    """t_end까지 검사선을 지난 과자 수 (정답)."""
    line_px = tracker.COUNT_LINE_X * W
    n = 0
    for _ in LINES_Y:
        for k in range(40):
            if W + D / 2 + k * (D + GAP) - speed * t_end <= line_px:
                n += 1
    return n


def run(fps: float, seconds: float, verdict_of=lambda line, k: "normal", shuffle_every=0, speed=SPEED):
    st = SessionTracker()
    frames = [(i, i / fps) for i in range(int(seconds * fps))]
    if shuffle_every:  # C3 복제본 때문에 순서가 뒤바뀌는 상황: 이웃한 두 프레임을 맞바꾼다
        for j in range(0, len(frames) - 1, shuffle_every):
            frames[j], frames[j + 1] = frames[j + 1], frames[j]
    for frame_id, t in frames:
        st.update(frame_id, t, scene(t, verdict_of, speed))
    return st


SECONDS = 30  # 화면을 여러 번 지나갈 만큼 길게 돌린다 (나간 과자의 추적이 뒤 과자를 가로채는지 확인)


def last_t(fps):
    return (int(SECONDS * fps) - 1) / fps


def test_each_cookie_counted_once_at_30fps():
    st = run(fps=30, seconds=SECONDS)
    assert st.totals["total"] == crossed(last_t(30))


def test_each_cookie_counted_once_at_low_fps():
    # CPU 환경: 처리 fps 1~3에서도 추적이 유지되어야 한다 (프레임 사이 이동 43~130px < 과자 간격 168px)
    for fps in (3, 2, 1):
        st = run(fps=fps, seconds=SECONDS)
        assert abs(st.totals["total"] - crossed(last_t(fps))) <= 1, fps


def test_sam2_cpu_speed_keeps_tracks():
    # 줌 시연: SAM 2(CPU) 복제본 2개면 결과가 4~5초마다 온다. 시뮬레이터는 25px/s로 낮춘다
    # (프레임 사이 이동 약 112px < 과자 간격 168px). 추적 유지 시간이 결과 간격보다 짧으면 집계가 0이 된다.
    fps, speed, seconds = 1 / 4.5, 25, 200
    st = run(fps=fps, seconds=seconds, speed=speed)
    expected = crossed((int(seconds * fps) - 1) / fps, speed)
    assert expected > 10
    assert abs(st.totals["total"] - expected) <= 1


def test_out_of_order_frames_do_not_double_count():
    st = run(fps=10, seconds=SECONDS, shuffle_every=3)
    assert abs(st.totals["total"] - crossed(last_t(10))) <= 1


def test_majority_vote_decides_verdict():
    # 2번 라인의 과자는 4프레임 중 3번 불량으로 판정됐다고 하자 → 불량으로 집계
    calls = {"n": 0}

    def verdict_of(line, k):
        calls["n"] += 1
        if line == 1:
            return "normal" if calls["n"] % 4 == 0 else "defect"
        return "normal"

    st = run(fps=10, seconds=SECONDS, verdict_of=verdict_of)
    assert st.totals["defect"] > 0
    assert st.totals["defect"] + st.totals["normal"] == st.totals["total"]
    # 1·3번 라인은 항상 정상이므로 불량은 2번 라인 몫(전체의 약 1/3)을 넘지 않는다
    assert st.totals["defect"] <= st.totals["total"] // 3 + 1


def test_tie_is_defect():
    assert tracker.majority({"normal": 2, "defect": 2, "uncertain": 0}) == "defect"
    assert tracker.majority({"normal": 0, "defect": 0, "uncertain": 3}) == "uncertain"


def test_truncated_objects_are_marked_and_not_tracked():
    objs = scene(0.5)
    st = SessionTracker()
    out = copy.deepcopy(objs)
    st.update(0, 0.5, out)
    edge = [o for o in out if o["truncated"]]
    assert edge, "화면 오른쪽 끝에 걸친 과자가 있어야 한다"
    assert all(o["track_id"] is None for o in edge)
    assert all(o["track_id"] is not None for o in out if not o["truncated"])
