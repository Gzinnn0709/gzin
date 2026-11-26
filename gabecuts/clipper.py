"""Geração dos arquivos de vídeo dos cortes com ffmpeg."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .recorder import Segment


class ClipError(RuntimeError):
    pass


# núcleos que o ffmpeg pode usar ao codificar cortes (definido pela config na inicialização)
THREADS = 2


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    if "libx264" in cmd and THREADS > 0:
        i = cmd.index("libx264") + 1
        cmd = [*cmd[:i], "-threads", str(THREADS), *cmd[i:]]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, encoding="utf-8",
                       errors="replace")
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


def make_vertical(src: Path, out: Path, subs: Path | None = None, title: str | None = None,
                  crf: int = 23, preset: str = "veryfast", zoom_at: float | None = None,
                  music: Path | None = None, music_volume: float = 0.22) -> Path:
    """Formato 9:16 (1080x1920) no estilo dos cortes do TikTok.

    * vídeo no centro com fundo desfocado
    * texto de gancho no topo
    * legenda (.ass palavra por palavra ou .srt) logo abaixo do vídeo
    * zoom rápido ("punch-in") na hora da reação, se `zoom_at` for informado
    * música de fundo opcional, que abaixa sozinha quando alguém fala
    """
    fg = "[b]scale=1080:608:force_original_aspect_ratio=decrease,pad=1080:608:(ow-iw)/2:(oh-ih)/2"
    if zoom_at is not None:
        # Zoom sem mexer no tempo do vídeo: uma cópia ampliada (118%) cobre a imagem só durante a
        # reação. (O filtro zoompan reescreve os tempos dos quadros e deixava o vídeo em câmera lenta.)
        z0, z1 = max(zoom_at - 0.15, 0.0), zoom_at + 1.6
        fg += (",split=2[fgn][fgz0];[fgz0]scale=1274:718,crop=1080:608[fgz];"
               f"[fgn][fgz]overlay=0:0:enable='between(t,{z0:.2f},{z1:.2f})'")
    vf = ("[0:v]split=2[a][b];"
          "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,"
          "eq=brightness=-0.08[bg];"
          + fg + "[fg];"
          "[bg][fg]overlay=(W-w)/2:(H-h)/2")
    for i, line in enumerate(wrap_title(title or "")):
        safe = (line.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")
                .replace("%", "\\%"))
        vf += (f",drawtext={_font_opt()}text='{safe}':fontcolor=white:fontsize=64:borderw=5"
               f":bordercolor=black:x=(w-text_w)/2:y={230 + i * 80}")
    if subs is not None and subs.exists():
        # o filtro subtitles é chato com caminhos; rodamos com cwd na pasta do arquivo
        if subs.suffix == ".ass":
            vf += f",subtitles={subs.name}"
        else:
            vf += f",subtitles={subs.name}:force_style='{SUB_STYLE}'"
    vf += "[v]"
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src.resolve())]
    amap = ["-map", "0:a?"]
    if music is not None and music.exists():
        cmd += ["-stream_loop", "-1", "-i", str(music.resolve())]
        vf += (f";[0:a]asplit=2[voz1][voz2];[1:a]volume={music_volume:.2f}[mus];"
               "[mus][voz1]sidechaincompress=threshold=0.03:ratio=10:attack=15:release=400[musd];"
               "[voz2][musd]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]")
        amap = ["-map", "[aout]"]
    cmd += ["-filter_complex", vf, "-map", "[v]", *amap, "-c:v", "libx264", "-preset", preset,
            "-crf", str(crf), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest",
            "-movflags", "+faststart", str(out.resolve())]
    _run(cmd, cwd=subs.parent if subs is not None else None)
    return out


def keep_intervals(levels_db: list[float], words: list, duration: float, step: float = 0.1,
                   min_gap: float = 0.8, pad: float = 0.2, max_cut_frac: float = 0.35
                   ) -> list[tuple[float, float]]:
    """Trechos a MANTER depois de tirar os silêncios parados (ritmo de TikTok).

    Um trecho só sai se for quieto (bem abaixo do volume típico do corte) E sem fala.
    Nunca tira mais que `max_cut_frac` do vídeo.
    """
    if not levels_db or duration <= 0:
        return [(0.0, duration)]
    ordenado = sorted(levels_db)
    tipico = ordenado[len(ordenado) // 2]
    limite = tipico - 10.0
    falando = [False] * len(levels_db)
    for w in words:
        for i in range(max(0, int(w.start / step) - 1), min(len(levels_db), int(w.end / step) + 2)):
            falando[i] = True
    quieto = [db < limite and not falando[i] for i, db in enumerate(levels_db)]
    cortes = []
    i = 0
    while i < len(quieto):
        if quieto[i]:
            j = i
            while j < len(quieto) and quieto[j]:
                j += 1
            ini, fim = i * step, min(j * step, duration)
            if fim - ini >= min_gap:
                cortes.append((ini + pad, fim - pad))
            i = j
        else:
            i += 1
    total = sum(b - a for a, b in cortes)
    if not cortes or total > duration * max_cut_frac:
        return [(0.0, duration)]
    keep, t = [], 0.0
    for a, b in cortes:
        if a > t:
            keep.append((t, a))
        t = b
    if t < duration:
        keep.append((t, duration))
    return keep


def time_map(keep: list[tuple[float, float]]):
    """Função tempo-antigo -> tempo-novo para um vídeo cortado com `keep` (None se cortado)."""
    def f(t: float):
        acc = 0.0
        for a, b in keep:
            if t < a:
                return None
            if t <= b:
                return acc + (t - a)
            acc += b - a
        return None
    return f


def cut_intervals(src: Path, out: Path, keep: list[tuple[float, float]], crf: int = 23,
                  preset: str = "veryfast") -> Path:
    """Junta só os trechos `keep` do vídeo (tira os silêncios)."""
    parts = []
    for i, (a, b) in enumerate(keep):
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}];"
                     f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}];")
    concat = "".join(f"[v{i}][a{i}]" for i in range(len(keep))) + f"concat=n={len(keep)}:v=1:a=1[v][a]"
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", "".join(parts) + concat,
          "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)])
    return out


def web_version(src: Path, out: Path, vertical: bool = True) -> Path:
    """Versão para o site (cabe no plano grátis sem ficar borrada).

    720x1280, 30 fps (sobra mais qualidade para cada quadro), CRF 23 com preset "medium"
    (comprime melhor que o veryfast), teto de 2 Mbps e áudio 128k: ~6-15 MB por corte de 1 a 2 min.
    """
    scale = "scale=720:-2:flags=lanczos" if vertical else "scale=-2:720:flags=lanczos"
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", f"{scale},fps=30", "-c:v", "libx264",
          "-preset", "medium", "-crf", "23", "-maxrate", "2000k", "-bufsize", "4000k",
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)])
    return out


def thumbnail(src: Path, out: Path, at: float) -> Path:
    _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(at, 0):.2f}", "-i", str(src),
          "-frames:v", "1", "-vf", "scale=480:-2", str(out)])
    return out
