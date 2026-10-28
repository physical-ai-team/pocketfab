"""스마트폰 대신 프레임을 /ws/camera로 보내는 가짜 카메라.

폰 없이 파이프라인을 확인하거나, 녹화 영상으로 같은 조건을 반복 측정할 때 쓴다.

    python tools/fake_camera.py                               # 합성 영상 30fps, 20초
    python tools/fake_camera.py --video data/clips/mixed/a.mp4 --fps 30 --seconds 60
    python tools/fake_camera.py --url wss://192.168.0.10/ws/camera

서버가 1초마다 보내는 통계(보냄/수락/버림/처리 fps)를 출력하고, 끝나면 요약을 보여준다.
처리된 프레임이 하나도 없으면 종료 코드 1을 반환한다.
"""

import argparse
import asyncio
import json
import ssl
import sys
import time

import cv2
import numpy as np
from websockets.asyncio.client import connect


def synthetic_frames(width=640, height=480):
    """컨베이어 위를 지나가는 원(과자)을 흉내 낸 합성 프레임."""
    i = 0
    while True:
        img = np.full((height, width, 3), 50, np.uint8)
        for k in range(3):
            x = (i * 4 + k * 220) % (width + 120) - 60
            cv2.circle(img, (x, height // 2), 50, (170, 200, 220), -1)
        cv2.putText(img, f"fake {i}", (10, height - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        yield img
        i += 1


def video_frames(path, width=640):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        sys.exit(f"영상을 열 수 없습니다: {path}")
    while True:
        ok, img = cap.read()
        if not ok:  # 끝나면 처음부터 반복
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            continue
        h, w = img.shape[:2]
        yield cv2.resize(img, (width, round(h * width / w)))


async def main(args) -> int:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # 자체 서명 인증서

    frames = video_frames(args.video) if args.video else synthetic_frames()
    last = {}

    async with connect(args.url, ssl=ctx if args.url.startswith("wss") else None, max_size=None) as ws:
        await ws.send(json.dumps({"type": "hello", "device": "fake_camera", "fps": args.fps}))

        async def read_stats():
            async for msg in ws:
                s = json.loads(msg)
                if s.get("type") == "stats":
                    last.update(s)
                    print(f"보냄 {s['sent']:5d}  수락 {s['accepted']:5d}  버림 {s['dropped']:5d}  "
                          f"처리 {s['processed_fps']:3d} fps", flush=True)

        reader = asyncio.create_task(read_stats())
        interval = 1 / args.fps
        start = time.monotonic()
        n = 0
        while time.monotonic() - start < args.seconds:
            _, jpeg = cv2.imencode(".jpg", next(frames), [cv2.IMWRITE_JPEG_QUALITY, 70])
            await ws.send(jpeg.tobytes())
            n += 1
            await asyncio.sleep(max(0, start + n * interval - time.monotonic()))

        await asyncio.sleep(2)  # 처리 중인 프레임의 결과를 조금 기다린다
        reader.cancel()

    processed = last.get("processed", 0)
    print(f"\n요약: 보냄 {n}, 수락 {last.get('accepted', 0)}, 처리 {processed}, "
          f"버림 {last.get('dropped', 0)} (C1 백프레셔 + 파이프라인 드롭 포함)")
    if processed == 0:
        print("처리된 프레임이 없습니다. docker compose logs 로 각 서비스를 확인하세요.")
        return 1
    print("대시보드에서 결과를 확인하세요.")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="가짜 카메라")
    p.add_argument("--url", default="wss://localhost/ws/camera")
    p.add_argument("--fps", type=float, default=30)
    p.add_argument("--seconds", type=float, default=20)
    p.add_argument("--video", help="보낼 영상 파일 (없으면 합성 영상)")
    sys.exit(asyncio.run(main(p.parse_args())))
