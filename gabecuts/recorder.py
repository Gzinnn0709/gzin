"""Gravação contínua da live em segmentos curtos (buffer circular) com streamlink + ffmpeg.

Cada segmento é salvo como `seg_<epoch>.ts`, onde <epoch> é o horário (em segundos) em que o
segmento começou. Isso permite mapear qualquer instante do chat para um trecho de vídeo.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SEG_RE = re.compile(r"seg_(\d+)\.ts$")


@dataclass(frozen=True)
class Segment:
    path: Path
    start: float
    duration: float

    @property
    def end(self) -> float:
        return self.start + self.duration


class Recorder:
    """Grava a live em segmentos.

    * Com `hls_url` (Kick, quando a API responde): o ffmpeg lê o .m3u8 direto.
    * Sem ela: streamlink -> ffmpeg (Twitch, ou Kick via plugin do streamlink).
    """

    def __init__(self, page_url: str, buffer_dir: Path, segment_seconds: int = 10,
                 quality: str = "best", hls_url: str | None = None):
        self.page_url = page_url
        self.hls_url = hls_url
        self.buffer_dir = buffer_dir
        self.segment_seconds = segment_seconds
        self.quality = quality
        self._sl: subprocess.Popen | None = None
        self._ff: subprocess.Popen | None = None

    def _segment_args(self) -> list[str]:
        return ["-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-f", "segment",
                "-segment_time", str(self.segment_seconds), "-reset_timestamps", "1",
                "-strftime", "1", str(self.buffer_dir / "seg_%s.ts")]

    def start(self) -> None:
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
        if self.hls_url:
            from .kick import BROWSER_UA
            self._ff = subprocess.Popen(
                ["ffmpeg", "-v", "error", "-user_agent", BROWSER_UA,
                 "-headers", "Referer: https://kick.com/\r\nOrigin: https://kick.com\r\n",
                 "-i", self.hls_url, *self._segment_args()], stderr=subprocess.DEVNULL)
            log.info("Gravação iniciada (HLS direto): %s", self.page_url)
            return
        extra = (["--twitch-disable-ads", "--twitch-low-latency"] if "twitch.tv" in self.page_url else [])
        self._sl = subprocess.Popen(
            ["streamlink", "--stdout", *extra, "--hls-live-edge", "2", self.page_url, self.quality],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._ff = subprocess.Popen(["ffmpeg", "-v", "error", "-i", "pipe:0", *self._segment_args()],
                                    stdin=self._sl.stdout, stderr=subprocess.DEVNULL)
        self._sl.stdout.close()  # o ffmpeg é o único leitor
        log.info("Gravação iniciada: %s (%s)", self.page_url, self.quality)

    def running(self) -> bool:
        ff_ok = self._ff is not None and self._ff.poll() is None
        sl_ok = self._sl is None or self._sl.poll() is None
        return ff_ok and sl_ok

    def stop(self) -> None:
        for p in (self._sl, self._ff):
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()
        log.info("Gravação parada")


def list_segments(buffer_dir: Path, default_duration: float = 10.0,
                  include_last: bool = False) -> list[Segment]:
    """Lista os segmentos em ordem. O último (ainda sendo escrito) é omitido por padrão.

    A duração de cada segmento é a diferença até o próximo (mais precisa que o valor nominal).
    """
    items = []
    for p in buffer_dir.glob("seg_*.ts"):
        m = SEG_RE.search(p.name)
        if m:
            items.append((int(m.group(1)), p))
    items.sort()
    segs = []
    for i, (start, p) in enumerate(items):
        if i + 1 < len(items):
            dur = items[i + 1][0] - start
            if dur <= 0 or dur > default_duration * 3:
                dur = default_duration
        else:
            if not include_last:
                break
            dur = default_duration
        segs.append(Segment(p, float(start), float(dur)))
    return segs


def segments_for_range(segs: list[Segment], t0: float, t1: float) -> list[Segment]:
    return [s for s in segs if s.end > t0 and s.start < t1]


def cleanup_buffer(buffer_dir: Path, keep_seconds: float, now: float | None = None) -> int:
    now = now or time.time()
    removed = 0
    for s in list_segments(buffer_dir):
        if s.end < now - keep_seconds:
            s.path.unlink(missing_ok=True)
            removed += 1
    return removed


def split_file_to_segments(src: Path, buffer_dir: Path, base_epoch: int,
                           segment_seconds: int = 10) -> list[Segment]:
    """Modo VOD: corta um vídeo local em segmentos com nomes de epoch artificiais."""
    buffer_dir.mkdir(parents=True, exist_ok=True)
    tmp = buffer_dir / "_tmp_%05d.ts"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:v:0?", "-map", "0:a:0?",
                    "-c", "copy", "-f", "segment", "-segment_time", str(segment_seconds),
                    "-reset_timestamps", "1", str(tmp)], check=True)
    offset = 0.0
    for p in sorted(buffer_dir.glob("_tmp_*.ts")):
        dur = probe_duration(p) or segment_seconds
        p.rename(buffer_dir / f"seg_{base_epoch + int(round(offset))}.ts")
        offset += dur
    return list_segments(buffer_dir, segment_seconds, include_last=True)


def probe_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", str(path)],
                             capture_output=True, text=True, check=True).stdout.strip()
        return float(out)
    except (subprocess.CalledProcessError, ValueError):
        return None
