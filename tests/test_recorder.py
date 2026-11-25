"""Grava um 'ao vivo' simulado (ffmpeg -re) com o Recorder de verdade e confere os segmentos."""
import shutil
import subprocess
import time

import pytest

from gabecuts.recorder import Recorder, list_segments

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg ausente")


class FileRecorder(Recorder):
    def __init__(self, src, *a, **k):
        super().__init__("https://kick.com/teste", *a, hls_url="x", **k)
        self.src = src

    def _input_args(self):
        return ["-re", "-i", str(self.src)]


def test_recorder_segments_have_realtime_epoch_names(tmp_path):
    src = tmp_path / "live.ts"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15:d=23",
                    "-f", "lavfi", "-i", "sine=d=23", "-c:v", "libx264", "-preset", "ultrafast", "-g", "15",
                    "-c:a", "aac", "-shortest", "-output_ts_offset", "5000", str(src)], check=True)
    buf = tmp_path / "buffer"
    rec = FileRecorder(src, buf, segment_seconds=5, log_path=tmp_path / "ffmpeg.log")
    t0 = time.time()
    rec.start()
    while rec.running() and time.time() - t0 < 60:
        rec.collect()
        time.sleep(0.5)
    assert not rec.running()
    rec.stop()
    segs = list_segments(buf, 5, include_last=True)
    assert len(segs) >= 4
    # nomes refletem o horário real (o arquivo começa com timestamp 5000, ignorado)
    assert abs(segs[0].start - t0) <= 3
    gaps = [b.start - a.start for a, b in zip(segs, segs[1:])]
    assert all(4 <= g <= 6 for g in gaps), gaps
    assert not rec.session_dir.exists()
