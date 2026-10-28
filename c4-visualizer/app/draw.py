"""판정 결과를 원본 프레임 위에 그린다.

과자마다 윤곽선(불량 빨강 / 정상 초록 / 판단불가 노랑 / 화면 끝 회색)을 그린다.
과자가 작고 촘촘해도 읽히도록 글자는 불량·판단불가에만 짧게 쓴다.
  불량:     "bite" + 판정을 가른 지표 "D0.13"   (D 오목 깊이, F 원 채움률, C 원형도, S solidity)
  판단불가: "?" + 마스크 점수 "m0.42"
검사선을 넘은 순간에는 그 과자와 검사선을 굵게 강조한다 (app/tracker.py의 tracking.counted).
"""

import cv2
import numpy as np

COLORS = {"normal": (80, 200, 80), "defect": (60, 60, 230), "uncertain": (0, 200, 230)}  # BGR
EDGE_COLOR = (150, 150, 150)  # 화면 끝에 걸쳐 판정·집계에서 빠지는 과자
FONT = cv2.FONT_HERSHEY_SIMPLEX
FILL_ALPHA = 0.25  # 불량 과자 안쪽을 반투명하게 칠하는 정도
# 라벨에 쓰는 지표 약자 (judge.py의 지표 이름 → 화면 글자). 앞에 있는 지표일수록 먼저 고른다
SHORT = {"concavity": "D", "fill_ratio": "F", "circularity": "C", "solidity": "S"}
LEGEND = "D:depth F:fill C:circ S:solid"


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
    """과자 위에 쓸 글자. 정상은 윤곽선만으로 충분하므로 빈 목록."""
    if o["verdict"] == "uncertain":
        return ["?", f"m{o['mask_score']:.2f}"]
    if o["verdict"] != "defect":
        return []
    # 판정을 가른 지표 하나만 쓴다 (오목 깊이 > 원 채움률 > 원형도 > solidity 순)
    reasons = o.get("defect_reasons", [])
    key = next((k for k in SHORT if k in reasons), None)
    return [o.get("defect_type") or "defect", f"{SHORT[key]}{o[key]:.2f}" if key else ""]


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
            continue  # 화면 끝 과자는 판정을 믿을 수 없으므로 라벨을 쓰지 않는다
        color = COLORS[o["verdict"]]
        cv2.polylines(img, [pts], True, color, 2, cv2.LINE_AA)
        lines = labels(o)
        if lines:
            x1, y1 = int(o["box"][0] * w), int(o["box"][1] * h)
            put_text(img, lines[0], (x1, max(y1 - 16, 12)), color, 0.45)
            put_text(img, lines[1], (x1, max(y1 - 3, 25)), color, 0.38)

    put_text(img, f"frame {meta['frame_id']}  replica {replica}   {LEGEND}", (8, 20), (255, 255, 255))
    put_text(img, f"objects {summary['total']}  normal {summary['normal']}  "
                  f"defect {summary['defect']}  uncertain {summary['uncertain']}", (8, 42), (255, 255, 255))

    ok, out = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return out.tobytes(), summary


def draw_tracking(img, meta: dict):
    """검사선, 과자 단위 누적 집계, 검사선 통과 강조 (app/tracker.py 결과)."""
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

    # 추적 번호는 화면에 쓰지 않는다 (과자가 촘촘하면 글자가 겹친다). 방금 집계된 과자만 굵은 박스로 표시
    verdict_of = {c["track_id"]: c["verdict"] for c in counted}
    for o in meta.get("objects", []):
        if o.get("track_id") in verdict_of:
            bx1, by1, bx2, by2 = o["box"]
            cv2.rectangle(img, (int(bx1 * w) - 4, int(by1 * h) - 4), (int(bx2 * w) + 4, int(by2 * h) + 4),
                          COLORS[verdict_of[o["track_id"]]], 3, cv2.LINE_AA)

    for i, c in enumerate(counted):
        put_text(img, f"+1 {c['verdict'].upper()}", (line_x + 6, 70 + 22 * i),
                 COLORS[c["verdict"]], 0.6)
