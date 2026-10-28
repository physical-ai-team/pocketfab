"""시뮬레이터의 불량 형상별 판정 지표(원형도·solidity·원 채움률·오목 깊이) 분포를 측정한다 (이상적인 마스크 기준).

conveyor-cookie-sim.html의 makeShape()를 그대로 옮겨 과자 외곽을 만들고, 래스터화한 뒤
C3와 같은 함수(c3-inference/app/judge.py의 measure)로 지표를 잰다.
마스크 오차가 없을 때의 "최선의 경우"이므로, 임계값을 정할 때 출발점으로 쓴다.

    python tools/simulator/shape_metrics.py
    python tools/simulator/shape_metrics.py --strength 0.25 0.55 0.85 --diameter 78 156

시뮬레이터의 makeShape()가 바뀌면 이 파일의 make_radius()도 같이 고친다.
"""

import argparse
import math
import random
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "c3-inference"))
from app.judge import measure  # noqa: E402

SEG = 180  # 시뮬레이터와 같은 외곽 분할 수
TYPES = ["round", "bite", "cut", "wedge", "half"]


def make_radius(typ: str, strength: float, amp: float) -> list[float]:
    """makeShape()의 반경 함수. 각도별로 원 반경(=1) 대비 남은 비율."""
    if typ == "round":
        return [1.0] * SEG
    k = strength * amp
    bite_r = 0.35 + k * 0.55
    bite_d = 1 + bite_r * (0.62 - k * 0.34)
    cut_d = 0.86 - k * 0.52
    half_d = 0.17 - k * 0.15
    wedge_w = 0.22 + k * 0.5
    direction = random.random() * math.pi * 2
    rad = []
    for i in range(SEG):
        a = i / SEG * math.pi * 2
        d = ((a - direction + math.pi * 3) % (math.pi * 2)) - math.pi
        c = math.cos(d)
        r = 1.0
        if typ == "bite":
            b = bite_d * c
            disc = b * b - (bite_d * bite_d - bite_r * bite_r)
            if disc > 0:
                t = b - math.sqrt(disc)
                if 0 < t < 1:
                    r = t
        elif typ in ("cut", "half"):
            dist = cut_d if typ == "cut" else half_d
            if c > 0.001:
                r = min(1, dist / c)
        elif typ == "wedge":
            if abs(d) < wedge_w:
                r = 0.07 + abs(d) / wedge_w * 0.1
        rad.append(max(0.06, r))
    return rad


def rasterize(rad: list[float], diameter: int) -> np.ndarray:
    """반경 함수 → 이진 마스크의 가장 큰 외곽선 (픽셀 좌표)."""
    size = int(diameter * 1.6)
    center, radius = size / 2, diameter / 2
    pts = [(center + math.cos(i / SEG * 2 * math.pi) * radius * r,
            center + math.sin(i / SEG * 2 * math.pi) * radius * r) for i, r in enumerate(rad)]
    mask = np.zeros((size, size), np.uint8)
    cv2.fillPoly(mask, [np.round(np.array(pts) * 4).astype(np.int32)], 255, shift=2)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return max(contours, key=cv2.contourArea)


def main():
    p = argparse.ArgumentParser(description="불량 유형별 지표 분포")
    p.add_argument("--strength", type=float, nargs="+", default=[0.25, 0.55], help="시뮬레이터 '결손 크기' (0~1)")
    p.add_argument("--diameter", type=int, nargs="+", default=[78, 156], help="이미지상 과자 지름(px)")
    p.add_argument("--samples", type=int, default=200)
    args = p.parse_args()

    random.seed(0)
    span = lambda v: f"{min(v):.3f}~{max(v):.3f}"  # noqa: E731
    for strength in args.strength:
        for diameter in args.diameter:
            print(f"\n결손 크기 {strength:.0%}, 지름 {diameter}px  (표본 {args.samples}개, 개체 편차 amp 0.55~1.0)")
            print(f"{'유형':6s} {'원형도':>13s} {'solidity':>13s} {'원 채움률':>13s} {'오목 깊이':>13s}")
            for typ in TYPES:
                circ, solid, fill, conc = [], [], [], []
                for _ in range(args.samples):
                    contour = rasterize(make_radius(typ, strength, 0.55 + random.random() * 0.45), diameter)
                    m = measure(contour)
                    circ.append(m["circularity"])
                    solid.append(m["solidity"])
                    fill.append(m["fill_ratio"])
                    conc.append(m["concavity"])
                print(f"{typ:6s} {span(circ):>13s} {span(solid):>13s} {span(fill):>13s} {span(conc):>13s}")


if __name__ == "__main__":
    main()
