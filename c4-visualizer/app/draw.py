"""판정 결과를 원본 프레임 위에 그린다.

과자마다: 윤곽선(불량 빨강 / 정상 초록 / 판단불가 노랑 / 화면 끝 회색) + 라벨
  1줄: "#번호 판정 유형"     예) "#2 DEFECT bite"
  2줄: 오목 깊이와 벗어난 지표 예) "depth 0.12  fill 0.91"
검사선을 넘은 순간에는 그 과자와 검사선을 굵게 강조한다 (app/tracker.py의 tracking.counted).
"""

import cv2
import numpy as np

COLORS = {"normal": (80, 200, 80), "defect": (60, 60, 230), "uncertain": (0, 200, 230)}  # BGR
EDGE_COLOR = (150, 150, 150)  # 화면 끝에 걸쳐 판정·집계에서 빠지는 과자
FONT = cv2.FONT_HERSHEY_SIMPLEX
FILL_ALPHA = 0.25  # 불량 과자 안쪽을 반투명하게 칠하는 정도
# 라벨에 쓰는 지표 약칭 (judge.py의 지표 이름 → 화면 글자)
SHORT = {"circularity": "circ", "solidity": "solid", "fill_ratio": "fill", "concavity": "depth"}


def put_text(img, text, org, color, scale=0.5):
    # 어떤 배경에서도 보이도록 검은 테두리를 먼저 그린다
    cv2.putText(img, text, org, FONT, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, text, org, FONT, scale, color, 1, cv2.LINE_AA)


def summarize(objects: list[dict]) -> dict:
    s = {"total": len(objects), "normal": 0, "defect": 0, "uncertain": 0}
    for o in objects:
        s[o["verdict"]] += 1
    return s


def to_pixels(o: dict, w: int, h: int) -> np.ndarray:
    """0~1 polygon → 픽셀 좌표 (N, 1, 2). polygon이 없으면 박스 네 꼭짓점."""
    pts = o.get("polygon")
    if not pts:
        x1, y1, x2, y2 = o["box"]
        pts = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
    return np.array([[x * w, y * h] for x, y in pts], dtype=np.int32).reshape(-1, 1, 2)


def labels(o: dict) -> list[str]:
    """과자 옆에 쓸 글자 1~2줄."""
    first = f"#{o['box_id']} {o['verdict'].upper()}"
    if o["verdict"] == "uncertain":
        return [first, f"mask {o['mask_score']:.2f}"]
    if o.get("defect_type"):
        first += f" {o['defect_type']}"
    # 오목 깊이는 항상 보여 주고(정상과 불량이 가장 잘 벌어지는 지표), 그 밖에는 벗어난 지표만 덧붙인다
    second = [f"depth {o['concavity']:.2f}"]
    second += [f"{SHORT.get(r, r)} {o[r]:.2f}" for r in o.get("defect_reasons", []) if r != "concavity"]
    return [first, "  ".join(second)]


def render(jpeg: bytes, meta: dict) -> tuple[bytes, dict]:
    """원본 JPEG + meta → (마킹된 JPEG, summary)."""
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    objects = meta.get("objects", [])
    summary = summarize(objects)
    replica = (meta.get("inference") or {}).get("replica") or "-"

    # 불량 과자 안쪽을 반투명하게 칠한다 (따로 그린 뒤 원본과 섞는다)
    overlay = img.copy()
    for o in objects:
        if o["verdict"] == "defect" and not o.get("truncated"):
            cv2.fillPoly(overlay, [to_pixels(o, w, h)], COLORS["defect"])
    img = cv2.addWeighted(overlay, FILL_ALPHA, img, 1 - FILL_ALPHA, 0)
    draw_tracking(img, meta)  # 검사선을 먼저 그려야 굵은 선이 과자 라벨을 가리지 않는다

    for o in objects:
        pts = to_pixels(o, w, h)
        if o.get("truncated"):
            cv2.polylines(img, [pts], True, EDGE_COLOR, 1, cv2.LINE_AA)
            continue  # 화면 끝 과자는 판정을 믿을 수 없으므로 라벨을 쓰지 않는다 (추적 번호 자리에 "edge" 표시)
        color = COLORS[o["verdict"]]
        cv2.polylines(img, [pts], True, color, 2, cv2.LINE_AA)
        x1, y1 = int(o["box"][0] * w), int(o["box"][1] * h)
        first, second = labels(o)
        put_text(img, first, (x1, max(y1 - 20, 76)), color)
        put_text(img, second, (x1, max(y1 - 6, 90)), color, 0.4)

    put_text(img, f"frame {meta['frame_id']}  replica {replica}", (8, 20), (255, 255, 255))
    put_text(img, f"objects {summary['total']}  normal {summary['normal']}  "
                  f"defect {summary['defect']}  uncertain {summary['uncertain']}", (8, 42), (255, 255, 255))

    ok, out = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return out.tobytes(), summary


def draw_tracking(img, meta: dict):
    """검사선, 과자 단위 누적 집계, 추적 번호, 검사선 통과 강조 (app/tracker.py 결과)."""
    tracking = meta.get("tracking")
    if not tracking:
        return
    h, w = img.shape[:2]
    line_x = int(tracking["count_line_x"] * w)
    counted = tracking["counted"]  # 이번 프레임에 검사선을 넘은 과자
    if counted:
        # 넘은 순간에는 검사선을 판정 색으로 굵게 그린다 (여러 개면 불량 > 판단불가 > 정상 순으로 색을 고른다)
        verdicts = {c["verdict"] for c in counted}
        flash = next(v for v in ("defect", "uncertain", "normal") if v in verdicts)
        cv2.line(img, (line_x, 0), (line_x, h), COLORS[flash], 4, cv2.LINE_AA)
    else:
        cv2.line(img, (line_x, 0), (line_x, h), (60, 60, 230), 1, cv2.LINE_AA)

    t = tracking["totals"]
    put_text(img, f"COUNTED {t['total']}  normal {t['normal']}  defect {t['defect']}  "
                  f"uncertain {t['uncertain']}", (8, h - 12), (255, 255, 255))

    verdict_of = {c["track_id"]: c["verdict"] for c in counted}
    for o in meta.get("objects", []):
        x1, y2 = o["box"][0] * w, o["box"][3] * h
        tag = "edge" if o.get("truncated") else f"T{o.get('track_id')}"
        put_text(img, tag, (int(x1), min(int(y2) + 14, h - 30)), (200, 200, 200), 0.4)
        if o.get("track_id") in verdict_of:  # 방금 집계된 과자는 박스를 굵게 둘러 눈에 띄게 한다
            bx1, by1, bx2, by2 = o["box"]
            cv2.rectangle(img, (int(bx1 * w) - 4, int(by1 * h) - 4), (int(bx2 * w) + 4, int(by2 * h) + 4),
                          COLORS[verdict_of[o["track_id"]]], 3, cv2.LINE_AA)

    for i, c in enumerate(counted):
        put_text(img, f"+1 T{c['track_id']} {c['verdict'].upper()}", (line_x + 6, 70 + 22 * i),
                 COLORS[c["verdict"]], 0.6)
