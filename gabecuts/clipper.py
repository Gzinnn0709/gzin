"""Geração dos arquivos de vídeo dos cortes com ffmpeg."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .recorder import Segment


class ClipError(RuntimeError):
    pass


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        raise ClipError(f"ffmpeg falhou: {r.stderr[-1500:]}")


def cut_from_segments(segs: list[Segment], t0: float, t1: float, out: Path,
                      crf: int = 23, preset: str = "veryfast") -> Path:
    """Junta os segmentos que cobrem [t0, t1] e recorta exatamente esse intervalo (re-encode)."""
    if not segs:
        raise ClipError("nenhum segmento cobre o intervalo pedido")
    out.parent.mkdir(parents=True, exist_ok=True)
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{s.path.resolve().as_posix()}'\n" for s in segs))
    offset = max(0.0, t0 - segs[0].start)
    duration = t1 - max(t0, segs[0].start)
    try:
        _run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
              "-ss", f"{offset:.2f}", "-t", f"{duration:.2f}",
              "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)])
    finally:
        lst.unlink(missing_ok=True)
    return out


def trim(src: Path, start: float, end: float, out: Path, crf: int = 23,
         preset: str = "veryfast") -> Path:
    _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-i", str(src),
          "-t", f"{end - start:.2f}", "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)])
    return out


SUB_STYLE = ("FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
             "BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=70")


def _font_opt() -> str:
    """No Windows o drawtext nem sempre acha fonte sozinho; apontamos para a Arial."""
    if os.name == "nt":
        for name in ("arialbd.ttf", "arial.ttf", "segoeuib.ttf"):
            f = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
            if f.exists():
                return "fontfile='" + f.as_posix().replace(":", "\\:") + "':"
    return ""


def wrap_title(title: str, width: int = 24, max_lines: int = 2) -> list[str]:
    lines, cur = [], ""
    for w in title.split():
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: width - 1].rstrip() + "…"
    return lines


def make_vertical(src: Path, out: Path, srt: Path | None = None, title: str | None = None,
                  crf: int = 23, preset: str = "veryfast") -> Path:
    """Formato 9:16 (1080x1920): vídeo no centro, fundo desfocado, legenda e título opcionais."""
    vf = ("[0:v]split=2[a][b];"
          "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,"
          "eq=brightness=-0.08[bg];"
          "[b]scale=1080:-2[fg];"
          "[bg][fg]overlay=(W-w)/2:(H-h)/2")
    for i, line in enumerate(wrap_title(title or "")):
        safe = (line.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")
                .replace("%", "\\%"))
        vf += (f",drawtext={_font_opt()}text='{safe}':fontcolor=white:fontsize=64:borderw=5"
               f":bordercolor=black:x=(w-text_w)/2:y={200 + i * 80}")
    if srt is not None and srt.exists():
        # o filtro subtitles é chato com caminhos; rodamos com cwd na pasta do arquivo
        vf += f",subtitles={srt.name}:force_style='{SUB_STYLE}'"
    vf += "[v]"
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(src.resolve()), "-filter_complex", vf,
          "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
          str(out.resolve())], cwd=srt.parent if srt is not None else None)
    return out


def thumbnail(src: Path, out: Path, at: float) -> Path:
    _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(at, 0):.2f}", "-i", str(src),
          "-frames:v", "1", "-vf", "scale=480:-2", str(out)])
    return out
