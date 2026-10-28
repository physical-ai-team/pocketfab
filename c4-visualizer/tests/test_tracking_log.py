"""추적 기록(TRACK_LOG_DIR) 테스트. tools/analyze_tracking.py가 읽는 형식으로 남는지 본다."""

import json

from app import main


def test_추적_기록은_세션별_파일에_한_줄씩_남는다(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "TRACK_LOG_DIR", str(tmp_path))
    meta = {
        "session_id": "abc", "frame_id": 7, "timings": {"c1_recv": 12_000.0},
        "objects": [{"box_id": 0, "box": [0.1, 0.2, 0.2, 0.4], "verdict": "defect", "defect_type": "bite",
                     "track_id": 3, "truncated": False, "polygon": [[0, 0]], "circularity": 0.7}],
        "tracking": {"count_line_x": 0.34, "direction": "left", "used_for_tracking": True,
                     "counted": [], "totals": {"total": 0, "normal": 0, "defect": 0, "uncertain": 0}, "active_tracks": 1},
    }
    main.log_tracking(meta)
    main.log_tracking({**meta, "frame_id": 8})

    lines = (tmp_path / "abc.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["frame_id"] == 7 and first["t"] == 12.0
    # 분석·재생에 필요한 필드만 남긴다 (윤곽선 같은 큰 값은 뺀다)
    assert first["objects"] == [{"box": [0.1, 0.2, 0.2, 0.4], "verdict": "defect", "defect_type": "bite",
                                 "track_id": 3, "truncated": False}]
    assert first["tracking"]["count_line_x"] == 0.34
