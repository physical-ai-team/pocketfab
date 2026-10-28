"""C4 추적 기록 분석: 시뮬레이터보다 적게 셌을 때 "어떤 과자를 왜 못 셌는지" 찾는다.

기록 켜기: .env에 TRACK_LOG_DIR=/data/tracking → docker compose up -d → 폰 테스트
→ data/tracking/<세션>.jsonl 이 생긴다 (C4가 프레임마다 과자 위치·판정·추적 결과를 한 줄씩 남김).

    python tools/analyze_tracking.py                                  # 가장 최근 세션
    python tools/analyze_tracking.py data/tracking/98942958.jsonl
    python tools/analyze_tracking.py --replay                         # 기록으로 추적기를 다시 돌려 같은 결과가 나오는지
    python tools/analyze_tracking.py --replay --lr-edge-only          # 위·아래 끝에 걸린 과자도 세면 몇 개가 되는지
    python tools/analyze_tracking.py --replay --max-age 30 --edge-margin 0.005

읽는 법
  1. 줄별 집계: 한 줄이 통째로 적게 세졌으면 그 줄이 화면 위·아래 끝에 걸린 것(구도 문제)인지 본다.
  2. 못 센 추적: 선을 넘기 "전에 사라짐"이 많으면 추적이 끊긴 것, "넘은 뒤에 처음 잡힘"이 많으면
     앞 프레임에서 C2가 과자를 놓쳤거나 두 프레임 사이에 너무 많이 움직인 것(속도를 낮춘다).
  4. 줄별 판정 사유: 한 줄만 불량 비율이 크게 높으면 그 줄이 촬영(기울기·반사) 때문에 오판되는 것이다.
     data/tracking/<세션>/<frame_id>.jpg 결과 이미지로 직접 확인한다.
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROW_GAP = 0.08  # 과자 중심 y가 이만큼 넘게 벌어지면 다른 줄로 본다 (화면 높이 비율)


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def center(box):
    x1, y1, x2, y2 = box
    return (x1 + x2) / 2, (y1 + y2) / 2


def find_rows(frames: list[dict]) -> list[float]:
    """모든 과자 중심 y를 모아 줄(라인) 위치를 찾는다."""
    ys = sorted(center(o["box"])[1] for f in frames for o in f["objects"])
    if not ys:
        return []
    groups = [[ys[0]]]
    for y in ys[1:]:
        if y - groups[-1][-1] > ROW_GAP:
            groups.append([])
        groups[-1].append(y)
    return [statistics.mean(g) for g in groups if len(g) >= 3]  # 3번 미만으로 보인 곳은 잡음


def nearest_row(rows, y):
    return min(range(len(rows)), key=lambda i: abs(rows[i] - y))


def report(frames: list[dict], edge_margin: float):
    info = frames[-1]["tracking"]
    line_x, sign = info["count_line_x"], (-1 if info["direction"] == "left" else 1)
    ts = [f["t"] for f in frames]
    gaps = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    used = sum(f["tracking"]["used_for_tracking"] for f in frames)
    print(f"프레임 {len(frames)}장 (추적에 쓴 것 {used}), {ts[-1] - ts[0]:.0f}초, "
          f"결과 간격 중간값 {statistics.median(gaps):.1f}초" if gaps else "")
    print(f"최종 누적 집계: {info['totals']}")

    # 추적 번호별로 본 위치 모으기
    seen = defaultdict(list)  # track_id → [(t, x, y)]
    for f in frames:
        for o in f["objects"]:
            if o["track_id"] is not None:
                x, y = center(o["box"])
                seen[o["track_id"]].append((f["t"], x, y))
    counted = {c["track_id"]: c for f in frames for c in f["tracking"]["counted"]}

    # 1. 줄별 집계
    rows = find_rows(frames)
    print(f"\n[1] 줄별 집계 (검사선 x={line_x}, 위·아래 끝 기준 {edge_margin})")
    per_row = defaultdict(lambda: {"dets": 0, "edge_tb": 0, "edge_lr": 0, "tracks": set(), "counted": 0})
    for f in frames:
        for o in f["objects"]:
            x1, y1, x2, y2 = o["box"]
            r = per_row[nearest_row(rows, center(o["box"])[1])] if rows else per_row[0]
            r["dets"] += 1
            if y1 <= edge_margin or y2 >= 1 - edge_margin:
                r["edge_tb"] += 1
            elif o["truncated"]:
                r["edge_lr"] += 1
            if o["track_id"] is not None:
                r["tracks"].add(o["track_id"])
    for tid, pts in seen.items():
        if tid in counted and rows:
            per_row[nearest_row(rows, statistics.mean(p[2] for p in pts))]["counted"] += 1
    for i, y in enumerate(rows):
        r = per_row[i]
        tb = r["edge_tb"] / max(1, r["dets"])
        warn = "  ← 대부분 화면 위·아래 끝에 걸려 집계에서 빠짐 (구도를 넓힐 것)" if tb > 0.5 else ""
        print(f"  줄 {i + 1} (y≈{y:.2f}): 검출 {r['dets']}, 추적 {len(r['tracks'])}개, 집계 {r['counted']}개, "
              f"위·아래 끝 {tb:.0%}, 좌우 끝 {r['edge_lr'] / max(1, r['dets']):.0%}{warn}")

    # 2. 추적은 됐는데 못 센 과자
    print("\n[2] 추적은 됐는데 집계되지 않은 과자")
    end_t = ts[-1]
    reasons = defaultdict(list)
    for tid, pts in seen.items():
        if tid in counted:
            continue
        first_x, last_x, last_t = pts[0][1], pts[-1][1], pts[-1][0]
        if sign * (first_x - line_x) >= 0:
            reasons["검사선을 넘은 뒤에 처음 잡힘 (앞에서 추적이 끊겼거나 C2가 놓침)"].append(tid)
        elif end_t - last_t < 10:
            reasons["테스트가 끝날 때 아직 검사선 전 (정상)"].append(tid)
        else:
            reasons["검사선을 넘기 전에 사라짐 (추적이 끊김)"].append(tid)
    if not reasons:
        print("  없음")
    for why, tids in sorted(reasons.items(), key=lambda kv: -len(kv[1])):
        sample = ", ".join(f"#{t}({len(seen[t])}번 보임)" for t in tids[:6])
        print(f"  {len(tids):3d}개  {why}  예: {sample}")

    # 3. 집계된 과자의 표 수
    votes = [sum(c["votes"].values()) for c in counted.values()]
    if votes:
        ties = sum(c["votes"]["normal"] == c["votes"]["defect"] for c in counted.values())
        print(f"\n[3] 집계된 과자 {len(votes)}개: 표 수 중간값 {statistics.median(votes)}, "
              f"최소 {min(votes)}, 동점(→불량) {ties}개")

    # 4. 줄별 판정 사유 (기록에 지표가 있을 때). 시뮬레이터는 줄과 상관없이 불량을 섞으므로
    #    한 줄만 불량 비율이 크게 높으면 그 줄이 촬영 때문에 체계적으로 오판되는 것이다
    if rows and any("defect_reasons" in o for f in frames for o in f["objects"]):
        print("\n[4] 줄별 판정 사유 (화면 끝 과자 제외, 사진 단위)")
        stats = defaultdict(lambda: {"n": 0, "defect": 0, "reasons": defaultdict(int), "conc": [], "fill": []})
        for f in frames:
            for o in f["objects"]:
                if o["truncated"] or o.get("concavity") is None:
                    continue
                s = stats[nearest_row(rows, center(o["box"])[1])]
                s["n"] += 1
                s["defect"] += o["verdict"] == "defect"
                for r in o.get("defect_reasons") or []:
                    s["reasons"][r] += 1
                s["conc"].append(o["concavity"])
                s["fill"].append(o["fill_ratio"])
        for i, s in sorted(stats.items()):
            print(f"  줄 {i + 1} (y≈{rows[i]:.2f}): 불량 비율 {s['defect'] / s['n']:.0%} ({s['n']}건), "
                  f"사유 {dict(s['reasons'])}, 오목 깊이 중간값 {statistics.median(s['conc']):.3f}, "
                  f"원 채움률 중간값 {statistics.median(s['fill']):.3f}")
        print(f"  결과 이미지: {path_of_images(frames)}")


def path_of_images(frames: list[dict]) -> Path:
    return ROOT / "data" / "tracking" / frames[0]["session_id"]


def replay(frames: list[dict], args):
    """기록된 과자 위치·판정으로 C4 추적기를 처음부터 다시 돌린다 (폰 없이 설정 실험)."""
    sys.path.insert(0, str(ROOT / "c4-visualizer"))
    from app import tracker  # noqa: E402

    if args.max_age is not None:
        tracker.TRACK_MAX_AGE_S = args.max_age
    if args.edge_margin is not None:
        tracker.EDGE_MARGIN = args.edge_margin
    if args.lr_edge_only:  # 위·아래 끝은 봐주고 좌우 끝만 잘린 과자로 본다
        m = tracker.EDGE_MARGIN
        tracker.is_truncated = lambda box: box[0] <= m or box[2] >= 1 - m
    st = tracker.SessionTracker()
    for f in frames:
        objs = [{"box": o["box"], "verdict": o["verdict"], "defect_type": o["defect_type"]} for o in f["objects"]]
        st.update(f["frame_id"], f["t"], objs)
    print(f"\n[재생] 추적 유지 {tracker.TRACK_MAX_AGE_S}초, 끝 기준 {tracker.EDGE_MARGIN}"
          f"{', 좌우 끝만' if args.lr_edge_only else ''} → {st.totals}  (기록: {frames[-1]['tracking']['totals']})")


def main():
    p = argparse.ArgumentParser(description="C4 추적 기록 분석")
    p.add_argument("log", nargs="?", type=Path, help="세션 기록 파일 (생략하면 data/tracking의 가장 최근 파일)")
    p.add_argument("--replay", action="store_true", help="기록으로 추적기를 다시 돌린다")
    p.add_argument("--max-age", type=float, help="재생 때 TRACK_MAX_AGE_S")
    p.add_argument("--edge-margin", type=float, help="재생 때 EDGE_MARGIN")
    p.add_argument("--lr-edge-only", action="store_true", help="재생 때 위·아래 끝에 걸린 과자는 잘린 것으로 보지 않는다")
    args = p.parse_args()

    path = args.log
    if path is None:
        files = sorted((ROOT / "data" / "tracking").glob("*.jsonl"), key=lambda f: f.stat().st_mtime)
        if not files:
            sys.exit("data/tracking에 기록이 없습니다. .env에 TRACK_LOG_DIR=/data/tracking을 넣고 다시 테스트하세요.")
        path = files[-1]
    frames = load(path)
    print(f"기록: {path}")
    report(frames, args.edge_margin if args.edge_margin is not None else 0.01)
    if args.replay:
        replay(frames, args)


if __name__ == "__main__":
    main()
