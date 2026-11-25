"""Teste ponta a ponta do modo VOD com um vídeo sintético (precisa de ffmpeg)."""
import json
import random
import shutil
import subprocess

import pytest

from gabecuts.config import Config
from gabecuts.db import ClipDB
from gabecuts.pipeline import process_vod

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg ausente")


def _make_video(path, seconds=150, loud_at=100):
    # vídeo de teste com áudio baixo e um "grito" alto entre loud_at e loud_at+4
    vol = f"volume='if(between(t,{loud_at},{loud_at + 4}),1.0,0.03)':eval=frame"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=15:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:d={seconds}", "-af", vol,
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-c:a", "aac", "-shortest",
                    str(path)], check=True)


def test_vod_pipeline(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    video = tmp_path / "vod.mp4"
    _make_video(video)
    rng = random.Random(0)
    chat = tmp_path / "chat.jsonl"
    with chat.open("w") as f:
        for t in range(150):
            n = 30 if 101 <= t <= 108 else rng.randint(1, 3)
            for i in range(n):
                text = "KKKKKKKKK" if 101 <= t <= 108 else rng.choice(["salve", "qual jogo", "eae"])
                f.write(json.dumps({"t": t + i / max(n, 1), "user": f"u{rng.randint(0, 999)}", "text": text}) + "\n")

    cfg = Config(data_dir=tmp_path / "data", ai_enabled=False, ai_min_score=0, video_preset="ultrafast")
    ids = process_vod(cfg, video, chat)
    assert len(ids) == 1
    clip = ClipDB(cfg.db_path).get(ids[0])
    assert clip["category"] == "engraçado"
    assert clip["t_start"] - 1_000_000_000 < 100 < clip["t_end"] - 1_000_000_000
    assert (cfg.clips_dir / clip["file_h"]).exists()
    assert (cfg.clips_dir / clip["file_v"]).exists()
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", str(cfg.clips_dir / clip["file_v"])],
                         capture_output=True, text=True).stdout.strip()
    assert out == "1080,1920"
