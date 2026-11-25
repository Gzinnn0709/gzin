"""Gravação contínua da live em segmentos curtos (buffer circular) com streamlink + ffmpeg.

Cada segmento é salvo como `seg_<epoch>.ts`, onde <epoch> é o horário (em segundos) em que o
segmento começou. Isso permite mapear qualquer instante do chat para um trecho de vídeo.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
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

    O ffmpeg escreve `seg_000000.ts, seg_000001.ts...` numa pasta da sessão e uma lista CSV com o
    tempo de cada segmento. `collect()` move cada segmento pronto para o buffer já com o nome
    `seg_<epoch>.ts`. (Não usamos `-strftime %s`, que não existe no Windows.)
    """

    def __init__(self, page_url: str, buffer_dir: Path, segment_seconds: int = 10,
                 quality: str = "best", hls_url: str | None = None, log_path: Path | None = None):
        self.page_url = page_url
        self.hls_url = hls_url
        self.buffer_dir = buffer_dir
        self.segment_seconds = segment_seconds
        self.quality = quality
        self.log_path = log_path or (buffer_dir.parent / "ffmpeg.log")
        self.session_dir = buffer_dir / f"rec_{int(time.time())}"
        self._list = self.session_dir / "segments.csv"
        self._anchor: float | None = None   # epoch correspondente ao tempo 0 do ffmpeg
        self._done: set[str] = set()
        self._sl: subprocess.Popen | None = None
        self._ff: subprocess.Popen | None = None
        self._log = None

    def _segment_args(self) -> list[str]:
        return ["-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-f", "segment",
                "-segment_time", str(self.segment_seconds), "-reset_timestamps", "1",
                "-segment_list", str(self._list), "-segment_list_type", "csv",
                str(self.session_dir / "seg_%06d.ts")]

    def _input_args(self) -> list[str]:
        if self.hls_url:
            from .kick import BROWSER_UA
            return ["-user_agent", BROWSER_UA,
                    "-headers", "Referer: https://kick.com/\r\nOrigin: https://kick.com\r\n",
                    "-i", self.hls_url]
        return ["-i", "pipe:0"]

    def start(self) -> None:
        # sobras de gravações anteriores (programa fechado no meio)
        for old in self.buffer_dir.glob("rec_*"):
            if old != self.session_dir:
                shutil.rmtree(old, ignore_errors=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_path, "a", encoding="utf-8", errors="replace")
        self._log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {self.page_url} ===\n")
        self._log.flush()
        stdin = None
        if not self.hls_url:
            extra = (["--twitch-disable-ads", "--twitch-low-latency"] if "twitch.tv" in self.page_url else [])
            self._sl = subprocess.Popen(
                ["streamlink", "--stdout", *extra, "--hls-live-edge", "2", self.page_url, self.quality],
                stdout=subprocess.PIPE, stderr=self._log)
            stdin = self._sl.stdout
        no_stdin = ["-nostdin"] if stdin is None else []
        self._ff = subprocess.Popen(["ffmpeg", "-v", "warning", "-y", *no_stdin,
                                     *self._input_args(), *self._segment_args()],
                                    stdin=stdin, stderr=self._log)
        if self._sl:
            self._sl.stdout.close()  # o ffmpeg é o único leitor
        log.info("Gravação iniciada%s: %s", " (HLS direto)" if self.hls_url else "", self.page_url)

    def collect(self, now: float | None = None) -> int:
        """Move os segmentos já finalizados para o buffer com nome baseado no horário real."""
        if not self._list.exists():
            return 0
        try:
            rows = self._list.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return 0
        moved = 0
        for row in rows:
            parts = row.rsplit(",", 2)
            if len(parts) != 3:
                continue
            name = Path(parts[0].strip().strip('"')).name
            if name in self._done:
                continue
            try:
                start, end = float(parts[1]), float(parts[2])
            except ValueError:
                continue  # linha ainda sendo escrita
            t = now if now is not None else time.time()
            # o segmento acabou de fechar => o fim dele é "agora" (a live é em tempo real).
            # Re-ancora se houver salto de timestamp (anúncio, reconexão...).
            if self._anchor is None or abs((self._anchor + end) - t) > 60:
                self._anchor = t - end
            epoch = int(round(self._anchor + start))
            src = self.session_dir / name
            dst = self.buffer_dir / f"seg_{epoch}.ts"
            if src.exists():
                try:
                    os.replace(src, dst)
                    moved += 1
                except OSError as e:  # arquivo ainda aberto (Windows): tenta na próxima volta
                    log.debug("Não consegui mover %s: %s", src, e)
                    continue
            self._done.add(name)
        return moved

    def running(self) -> bool:
        ff_ok = self._ff is not None and self._ff.poll() is None
        sl_ok = self._sl is None or self._sl.poll() is None
        return ff_ok and sl_ok

    def exit_info(self) -> str:
        """Últimas linhas do log do ffmpeg/streamlink, para mostrar por que a gravação caiu."""
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            return " | ".join(lines[-3:])[-400:]
        except OSError:
            return ""

    def stop(self) -> None:
        for p in (self._sl, self._ff):
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()
        self.collect()
        if self._log:
            self._log.close()
            self._log = None
        shutil.rmtree(self.session_dir, ignore_errors=True)
        log.info("Gravação parada")


def streamlink_quality(cfg) -> str:
    """Qualidade para o streamlink: limita a resolução para não brigar com a live no navegador."""
    if cfg.stream_quality not in ("auto", ""):
        return cfg.stream_quality
    h = cfg.record_max_height
    opts = [q for q in ("1080p60", "1080p", "936p60", "720p60", "720p", "480p60", "480p", "360p")
            if int(q.split("p")[0]) <= h]
    return ",".join(opts + ["best"])


_STREAM_INF = re.compile(r"#EXT-X-STREAM-INF:(.*)")
_RES = re.compile(r"RESOLUTION=(\d+)x(\d+)")
_BW = re.compile(r"[^-]BANDWIDTH=(\d+)")


def pick_variant(master_url: str, max_height: int = 720, text: str | None = None) -> str:
    """Escolhe, numa playlist HLS mestre, a melhor qualidade com altura <= max_height."""
    from urllib.parse import urljoin
    if text is None:
        try:
            import httpx
            from .kick import BROWSER_UA
            r = httpx.get(master_url, timeout=10, headers={"User-Agent": BROWSER_UA,
                                                            "Referer": "https://kick.com/"})
            r.raise_for_status()
            text = r.text
        except Exception as e:  # noqa: BLE001
            log.debug("Não li a playlist mestre (%s); usando a padrão", e)
            return master_url
    lines = [ln.strip() for ln in text.splitlines()]
    variants = []
    for i, ln in enumerate(lines):
        m = _STREAM_INF.match(ln)
        if not m or i + 1 >= len(lines):
            continue
        uri = next((u for u in lines[i + 1:] if u and not u.startswith("#")), None)
        if not uri:
            continue
        res = _RES.search(m.group(1))
        bw = _BW.search(" " + m.group(1))
        full = urljoin(master_url, uri)
        # variantes relativas podem precisar do token da playlist mestre
        if "?" not in uri and "?" in master_url and not uri.startswith("http"):
            full += "?" + master_url.split("?", 1)[1]
        variants.append((int(res.group(2)) if res else 0, int(bw.group(1)) if bw else 0, full))
    if not variants:
        return master_url
    ok = [v for v in variants if 0 < v[0] <= max_height] or [min(variants)]
    chosen = max(ok, key=lambda v: (v[0], v[1]))
    log.info("Gravando em %sp (limite %sp) para não pesar na live", chosen[0] or "?", max_height)
    return chosen[2]


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
