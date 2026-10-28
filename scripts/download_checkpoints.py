"""SAM 2.1 체크포인트를 공식 경로(Meta)에서 checkpoints/ 폴더로 내려받는다.

사용법 (레포 루트에서):
    python scripts/download_checkpoints.py              # tiny, small 모두
    python scripts/download_checkpoints.py tiny         # tiny만
    python scripts/download_checkpoints.py --force small  # 이미 있어도 다시 받기

표준 라이브러리만 사용하므로 별도 패키지 설치가 필요 없다.
"""

import argparse
import sys
import urllib.request
from pathlib import Path

# 공식 다운로드 경로: https://github.com/facebookresearch/sam2 의 checkpoints/download_ckpts.sh
BASE_URL = "https://dl.fbaipublicfiles.com/segment_anything_2/092824"

# 모델 이름 → 파일 이름. 우선 CPU에서 쓸 만한 tiny, small만 지원한다.
MODELS = {
    "tiny": "sam2.1_hiera_tiny.pt",
    "small": "sam2.1_hiera_small.pt",
}

CHECKPOINT_DIR = Path(__file__).resolve().parent.parent / "checkpoints"


def download(name: str, force: bool) -> None:
    filename = MODELS[name]
    dest = CHECKPOINT_DIR / filename
    url = f"{BASE_URL}/{filename}"

    with urllib.request.urlopen(url) as resp:
        total = int(resp.headers.get("Content-Length", 0))

        # 크기까지 같으면 이미 받은 것으로 본다
        if dest.exists() and not force and dest.stat().st_size == total:
            print(f"[건너뜀] {filename} 이미 있음")
            return

        # 받는 도중 끊기면 .part만 남고, 완료된 뒤에만 원래 이름으로 바꾼다
        part = dest.with_suffix(".part")
        done = 0
        with open(part, "wb") as f:
            while chunk := resp.read(1024 * 1024):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r[받는 중] {filename} {done / total:6.1%} "
                          f"({done / 2**20:.1f} / {total / 2**20:.1f} MB)", end="", flush=True)
        print()

    if total and done != total:
        part.unlink()
        raise RuntimeError(f"{filename} 크기가 맞지 않음 ({done} / {total} bytes)")

    part.replace(dest)
    print(f"[완료] {dest}")


def main() -> int:
    parser = argparse.ArgumentParser(description="SAM 2.1 체크포인트 다운로드")
    parser.add_argument("models", nargs="*", default=list(MODELS),
                        help=f"받을 모델: {', '.join(MODELS)} (기본: 전부)")
    parser.add_argument("--force", action="store_true", help="이미 있어도 다시 받기")
    args = parser.parse_args()

    unknown = [m for m in args.models if m not in MODELS]
    if unknown:
        parser.error(f"지원하지 않는 모델: {', '.join(unknown)} (가능: {', '.join(MODELS)})")

    CHECKPOINT_DIR.mkdir(exist_ok=True)
    failed = []
    for name in args.models:
        try:
            download(name, args.force)
        except Exception as e:
            print(f"\n[실패] {name}: {e}", file=sys.stderr)
            failed.append(name)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
