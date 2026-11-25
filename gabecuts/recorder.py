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


def is_live(channel: str) -> bool:
    """Verifica se o canal está ao vivo usando a API do streamlink (sem token da Twitch)."""
    try:
        import streamlink
        session = streamlink.Streamlink()
        session.set_option("twitch-disable-ads", True)
        return bool(session.streams(f"https://twitch.tv/{channel}"))
    except Exception as e:  # noqa: BLE001
        log.debug("Falha ao checar live: %s", e)
        return False


class Recorder:
    def __init__(self, channel: str, buffer_dir: Path, segment_seconds: int = 10,
                 quality: str = "best"):
        self.channel = channel
        self.buffer_dir = buffer_dir
        self.segment_seconds = segment_seconds
        self.quality = quality
        self._sl: subprocess.Popen | None = None
        self._ff: subprocess.Popen | None = None

    def start(self) -> None:
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
        self._sl = subprocess.Popen(
            ["streamlink", "--stdout", "--twitch-disable-ads", "--twitch-low-latency",
             "--hls-live-edge", "2", f"https://twitch.tv/{self.channel}", self.quality],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._ff = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-i", "pipe:0", "-map", "0:v:0?", "-map", "0:a:0?",
             "-c", "copy", "-f", "segment", "-segment_time", str(self.segment_seconds),
             "-reset_timestamps", "1", "-strftime", "1",
             str(self.buffer_dir / "seg_%s.ts")],
            stdin=self._sl.stdout, stderr=subprocess.DEVNULL)
        self._sl.stdout.close()  # o ffmpeg é o único leitor
        log.info("Gravação iniciada: twitch.tv/%s (%s)", self.channel, self.quality)

    def running(self) -> bool:
        return bool(self._ff and self._ff.poll() is None and self._sl and self._sl.poll() is None)

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
