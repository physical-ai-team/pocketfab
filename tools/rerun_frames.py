"""실제 폰 촬영 원본으로 C2(전처리·박스) → C3(마스크·판정)를 다시 돌려 줄별 결과를 본다.

C2·C3 코드를 고친 뒤, 폰 테스트를 다시 하지 않고 지난 테스트의 원본 사진으로 효과를 확인할 때 쓴다.
원본은 C4 추적 기록(TRACK_LOG_DIR)을 켜면 data/tracking/<세션>/<frame_id>_orig.jpg로 남는다.
정답 라벨이 없으므로 "줄별 불량 비율"로 본다: 시뮬레이터는 줄과 상관없이 불량을 섞으므로
줄마다 비율이 비슷하고 시뮬레이터 불량률에 가까울수록 좋다.

SAM 2가 필요해서 C3 컨테이너 안에서 실행한다 (레포 루트에서):
    $r = (Get-Location).Path
    docker compose run --rm --no-deps -v "${r}\data:/srv/data" -v "${r}\tools:/srv/tools" `
      -v "${r}\c2-preprocessing:/srv/c2" -v "${r}\c3-inference:/srv/c3" c3-inference `
      python tools/rerun_frames.py data/tracking/<세션> [--every 3] [--save]
"""

import argparse
import importlib.util
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, "/srv/c3")
from app import judge  # noqa: E402

# C2와 C3가 둘 다 "app" 패키지라서 C2 파일은 경로로 직접 불러온다
spec = importlib.util.spec_from_file_location("c2_preprocess", "/srv/c2/app/preprocess.py")
c2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c2)

ROW_GAP = 0.08
EDGE = 0.01
COLORS = {"normal": (80, 200, 80), "defect": (60, 60, 230), "uncertain": (0, 200, 230)}


def main():
    p = argparse.ArgumentParser(description="원본 사진으로 C2→C3 다시 돌리기")
    p.add_argument("folder", type=Path, help="data/tracking/<세션>")
    p.add_argument("--every", type=int, default=1, help="N장마다 하나만 (SAM 2가 느려서 빨리 보고 싶을 때)")
    p.add_argument("--segmenter", choices=["sam2", "opencv"], default="sam2")
    p.add_argument("--save", action="store_true", help="윤곽선을 그린 결과를 <세션>/rerun/에 저장")
    args = p.parse_args()

    if args.segmenter == "sam2":
        from app import sam2_segmenter as seg  # noqa: E402
    else:
        from app import opencv_segmenter as seg  # noqa: E402

    files = sorted(args.folder.glob("*_orig.jpg"))[::args.every]
    if not files:
        sys.exit(f"{args.folder}에 *_orig.jpg가 없습니다. C4 추적 기록을 켜고 다시 테스트하세요.")
    out_dir = args.folder / "rerun"
    if args.save:
        out_dir.mkdir(exist_ok=True)

    results = []  # (중심 y, 판정, 사유)
    n_boxes = []
    for f in files:
        jpeg = f.read_bytes()
        h0, w0 = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR).shape[:2]
        proc, size, _ = c2.preprocess(jpeg, {"w": w0, "h": h0})
        boxes = c2.detect_boxes(proc, size)
        n_boxes.append(len(boxes))
        img = cv2.imdecode(np.frombuffer(proc, np.uint8), cv2.IMREAD_COLOR)
        objs = [judge.build_object(s, size["w"], size["h"]) for s in seg.segment(img, boxes)]
        for o in objs:
            x1, y1, x2, y2 = o["box"]
            if x1 <= EDGE or y1 <= EDGE or x2 >= 1 - EDGE or y2 >= 1 - EDGE:
                continue  # 화면 끝에 걸린 과자는 C4처럼 뺀다
            results.append(((y1 + y2) / 2, o["verdict"], o["defect_reasons"]))
        if args.save:
            for o in objs:
                pts = np.array([[x * size["w"], y * size["h"]] for x, y in o["polygon"]], np.int32)
                cv2.polylines(img, [pts], True, COLORS[o["verdict"]], 2)
                bx = [int(o["box"][0] * size["w"]), int(o["box"][1] * size["h"]),
                      int(o["box"][2] * size["w"]), int(o["box"][3] * size["h"])]
                cv2.rectangle(img, bx[:2], bx[2:], (255, 255, 0), 1)  # C2 박스 (하늘색)
            cv2.imwrite(str(out_dir / f.name.replace("_orig", "")), img)
        print(f"  {f.name}: 박스 {len(boxes)}개", flush=True)

    # 줄 찾기: 과자 중심 y를 모아 간격이 벌어진 곳에서 나눈다
    ys = sorted(y for y, *_ in results)
    groups = [[ys[0]]] if ys else []
    for y in ys[1:]:
        if y - groups[-1][-1] > ROW_GAP:
            groups.append([])
        groups[-1].append(y)
    rows = [statistics.mean(g) for g in groups if len(g) >= 3]

    per_row = defaultdict(lambda: {"n": 0, "defect": 0, "reasons": defaultdict(int)})
    for y, verdict, reasons in results:
        r = per_row[min(range(len(rows)), key=lambda i: abs(rows[i] - y))]
        r["n"] += 1
        r["defect"] += verdict == "defect"
        for x in reasons:
            r["reasons"][x] += 1
    print(f"\n사진 {len(files)}장, 사진당 박스 중간값 {statistics.median(n_boxes)}")
    for i, y in enumerate(rows):
        r = per_row[i]
        print(f"  줄 {i + 1} (y≈{y:.2f}): 과자 {r['n']}건, 불량 비율 {r['defect'] / max(1, r['n']):.0%}, 사유 {dict(r['reasons'])}")
    if args.save:
        print(f"그린 결과: {out_dir}")


if __name__ == "__main__":
    main()
