"""Simula uma live inteira: vídeo em tempo real + chat com explosão de risada + botão 'cortar agora'."""
import asyncio
import random
import shutil
import subprocess
import time

import pytest

from gabecuts import pipeline
from gabecuts.chat import ChatMessage
from gabecuts.config import Config
from gabecuts.platforms import LiveStatus
from gabecuts.recorder import Recorder

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg ausente")
LIVE_SECONDS = 70
BURST_AT = 32


def test_live_simulation_produces_clips(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    src = tmp_path / "live.ts"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size=320x180:rate=15:d={LIVE_SECONDS}", "-f", "lavfi", "-i",
                    f"sine=d={LIVE_SECONDS}", "-c:v", "libx264", "-preset", "ultrafast", "-g", "15",
                    "-c:a", "aac", "-shortest", str(src)], check=True)

    class FileRecorder(Recorder):
        def _input_args(self):
            return ["-re", "-i", str(src)]

    t_start = time.time()
    monkeypatch.setattr(pipeline, "Recorder", FileRecorder)
    monkeypatch.setattr(pipeline, "check_live", lambda cfg: LiveStatus(
        time.time() - t_start < LIVE_SECONDS + 15, "https://kick.com/teste", "fake"))

    async def fake_chat(cfg):
        rng = random.Random(1)
        while True:
            el = time.time() - t_start
            burst = BURST_AT <= el <= BURST_AT + 6
            for _ in range(25 if burst else rng.randint(1, 3)):
                yield ChatMessage(time.time(), f"u{rng.randint(0, 5000)}",
                                  "KKKKKKKKKK" if burst else rng.choice(["salve", "eae", "qual jogo"]))
            await asyncio.sleep(0.25)

    monkeypatch.setattr(pipeline, "read_all_chats", fake_chat)
    cfg = Config(data_dir=tmp_path / "data", segment_seconds=5, warmup_seconds=15, analysis_lag=12,
                 baseline_seconds=60, ai_enabled=False, ai_min_score=0, whisper_model="off",
                 video_preset="ultrafast", live_check_interval=1)
    runner = pipeline.LiveRunner(cfg)

    async def main():
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(45)
        manual_id = runner.clip_now(20)
        deadline = time.time() + 70
        while time.time() < deadline:
            clips = runner.db.list()
            if clips and all(c["status"] not in ("na fila", "processando") for c in clips) and len(clips) >= 2:
                break
            await asyncio.sleep(2)
        task.cancel()
        if runner.recorder:
            runner.recorder.stop()
        return manual_id

    manual_id = asyncio.run(main())
    clips = {c["id"]: c for c in runner.db.list()}
    assert manual_id in clips
    for c in clips.values():
        assert c["status"] == "pendente", (c["status"], c["reason"])
        assert (cfg.clips_dir / c["file_h"]).exists()
        assert (cfg.clips_dir / c["file_v"]).exists()
    assert clips[manual_id]["category"] == "manual"
    auto = [c for i, c in clips.items() if i != manual_id]
    assert len(auto) == 1 and auto[0]["category"] == "engraçado"
    # o corte automático contém o momento da explosão
    assert auto[0]["t_start"] - t_start < BURST_AT < auto[0]["t_end"] - t_start + 5
