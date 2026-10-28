"""판정 결과를 원본 프레임 위에 그린다. 지금은 텍스트만 표시한다.

TODO(김세광): objects[].polygon으로 윤곽선 그리기 (불량 빨강 / 정상 초록 / 판단불가 노랑).
"""

import cv2
import numpy as np

COLORS = {"normal": (80, 200, 80), "defect": (60, 60, 230), "uncertain": (0, 200, 230)}  # BGR
FONT = cv2.FONT_HERSHEY_SIMPLEX


def put_text(img, text, org, color, scale=0.5):
    # 어떤 배경에서도 보이도록 검은 테두리를 먼저 그린다
    cv2.putText(img, text, org, FONT, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, text, org, FONT, scale, color, 1, cv2.LINE_AA)


def summarize(objects: list[dict]) -> dict:
    s = {"total": len(objects), "normal": 0, "defect": 0, "uncertain": 0}
    for o in objects:
        s[o["verdict"]] += 1
    return s


def render(jpeg: bytes, meta: dict) -> tuple[bytes, dict]:
    """원본 JPEG + meta → (마킹된 JPEG, summary)."""
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    objects = meta.get("objects", [])
    summary = summarize(objects)
    replica = (meta.get("inference") or {}).get("replica") or "-"

    put_text(img, f"frame {meta['frame_id']}  replica {replica}", (8, 20), (255, 255, 255))
    put_text(img, f"objects {summary['total']}  normal {summary['normal']}  "
                  f"defect {summary['defect']}  uncertain {summary['uncertain']}", (8, 42), (255, 255, 255))

    for o in objects:
        x1, y1 = o["box"][0] * w, o["box"][1] * h
        label = (f"#{o['box_id']} {o['verdict'].upper()} C={o['circularity']:.2f} "
                 f"S={o['solidity']:.2f} F={o['fill_ratio']:.2f}")
        put_text(img, label, (int(x1), max(int(y1) - 6, 60)), COLORS[o["verdict"]])

    draw_tracking(img, meta)
    ok, out = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return out.tobytes(), summary


def draw_tracking(img, meta: dict):
    """검사선, 과자 단위 누적 집계, 추적 번호를 그린다 (app/tracker.py 결과)."""
    tracking = meta.get("tracking")
    if not tracking:
        return
    h, w = img.shape[:2]
    line_x = int(tracking["count_line_x"] * w)
    cv2.line(img, (line_x, 0), (line_x, h), (60, 60, 230), 1, cv2.LINE_AA)

    t = tracking["totals"]
    put_text(img, f"COUNTED {t['total']}  normal {t['normal']}  defect {t['defect']}  "
                  f"uncertain {t['uncertain']}", (8, h - 12), (255, 255, 255))

    for o in meta.get("objects", []):
        x1, y2 = o["box"][0] * w, o["box"][3] * h
        tag = "edge" if o.get("truncated") else f"T{o.get('track_id')}"
        put_text(img, tag, (int(x1), min(int(y2) + 14, h - 30)), (200, 200, 200), 0.4)

    for c in tracking["counted"]:  # 이번 프레임에 검사선을 넘은 과자
        put_text(img, f"+1 T{c['track_id']} {c['verdict'].upper()}", (line_x + 6, 70), COLORS[c["verdict"]], 0.6)
