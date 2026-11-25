"""Transcrição com faster-whisper (opcional). Sem o pacote instalado, o sistema segue sem legenda."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)
_model = None


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Line:
    start: float
    end: float
    text: str
    words: list = field(default_factory=list)  # list[Word] – legenda palavra por palavra


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 - no Windows pode faltar DLL (OSError), não só ImportError
        return False


def _has_cuda() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        return False


def transcribe(path: Path, model_size: str = "auto", device: str = "auto",
               language: str = "pt", cpu_threads: int = 2) -> list[Line]:
    global _model
    if not available():
        return []
    from faster_whisper import WhisperModel
    if _model is None:
        cuda = _has_cuda() if device == "auto" else device == "cuda"
        if model_size == "auto":
            model_size = "small" if cuda else "base"
        dev = "cuda" if cuda else "cpu"
        compute = "float16" if cuda else "int8"  # int8 deixa a CPU bem mais rápida
        log.info("Carregando Whisper '%s' (%s). Na primeira vez ele baixa o modelo, pode demorar…",
                 model_size, dev)
        _model = WhisperModel(model_size, device=dev, compute_type=compute,
                              cpu_threads=max(1, cpu_threads))
    segments, _info = _model.transcribe(str(path), language=language, vad_filter=True,
                                        beam_size=1, word_timestamps=True)
    out = []
    for seg in segments:
        if not seg.text.strip():
            continue
        words = [Word(w.start, w.end, w.word.strip()) for w in (seg.words or []) if w.word.strip()]
        out.append(Line(seg.start, seg.end, seg.text.strip(), words))
    return out


def shift(lines: list[Line], offset: float, limit: float | None = None) -> list[Line]:
    """Desloca legendas (após um corte fino) e descarta as que ficam fora do vídeo."""
    return remap(lines, lambda t: t - offset, limit)


def remap(lines: list[Line], f, limit: float | None = None) -> list[Line]:
    """Aplica uma função de tempo (ex.: depois de tirar silêncios). f devolve None p/ tempo cortado."""
    out = []
    for ln in lines:
        words = []
        for w in ln.words:
            a, b = f(w.start), f(w.end)
            if a is None and b is None:
                continue
            a = b if a is None else a
            b = a if b is None else b
            if b <= 0 or (limit is not None and a >= limit):
                continue
            words.append(Word(max(a, 0.0), min(b, limit) if limit else b, w.text))
        s, e = f(ln.start), f(ln.end)
        if words:
            s, e = words[0].start, words[-1].end
        if s is None or e is None or e <= 0 or (limit is not None and s >= limit):
            if not words:
                continue
        out.append(Line(max(s, 0.0), min(e, limit) if limit else e, ln.text, words))
    return out


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(lines: list[Line], max_chars: int = 42) -> str:
    """Gera SRT quebrando falas longas em blocos curtos (melhor leitura no celular)."""
    blocks = []
    for ln in lines:
        words = ln.text.split()
        chunks, cur = [], ""
        for w in words:
            if len(cur) + len(w) + 1 > max_chars and cur:
                chunks.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            chunks.append(cur)
        if not chunks:
            continue
        step = (ln.end - ln.start) / len(chunks)
        for i, ch in enumerate(chunks):
            blocks.append((ln.start + i * step, ln.start + (i + 1) * step, ch.upper()))
    return "".join(f"{i}\n{_ts(a)} --> {_ts(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(blocks, 1))


def to_text(lines: list[Line]) -> str:
    return "\n".join(f"[{ln.start:5.1f}s] {ln.text}" for ln in lines)


# --- Legenda estilo TikTok (ASS): 1-3 palavras por vez, a palavra falada em amarelo ---------

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Corte,Arial Black,86,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,3,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
AMARELO = r"{\c&H0000E5FF&}"  # BGR: amarelo-ouro
BRANCO = r"{\c&H00FFFFFF&}"


def _ass_ts(t: float) -> str:
    cs = int(round(max(t, 0) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _palavras(lines: list[Line]) -> list[Word]:
    """Todas as palavras com tempo; se o Whisper não deu tempo por palavra, estima."""
    out: list[Word] = []
    for ln in lines:
        if ln.words:
            out.extend(ln.words)
            continue
        ws = ln.text.split()
        if not ws:
            continue
        step = max(ln.end - ln.start, 0.2) / len(ws)
        out.extend(Word(ln.start + i * step, ln.start + (i + 1) * step, w) for i, w in enumerate(ws))
    return out


def _limpa(w: str) -> str:
    return w.strip().upper().replace("{", "(").replace("}", ")").replace("\\", "/")


def to_ass(lines: list[Line], max_words: int = 3, max_chars: int = 18, margin_v: int = 620) -> str:
    """Gera legenda .ass no estilo dos cortes do TikTok: blocos curtos, palavra atual em destaque."""
    words = [w for w in _palavras(lines) if _limpa(w.text)]
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        texto = " ".join(_limpa(x.text) for x in cur + [w])
        pausa = cur and w.start - cur[-1].end > 0.6
        if cur and (len(cur) >= max_words or len(texto) > max_chars or pausa
                    or cur[-1].text.rstrip().endswith((".", "!", "?", ","))):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    events = []
    for n, ch in enumerate(chunks):
        fim_bloco = ch[-1].end + 0.15
        if n + 1 < len(chunks):  # nunca sobrepor o próximo bloco
            fim_bloco = min(fim_bloco, chunks[n + 1][0].start)
        for i, w in enumerate(ch):
            ini = w.start
            fim = ch[i + 1].start if i + 1 < len(ch) else fim_bloco
            if fim - ini < 0.05:
                continue
            txt = " ".join((AMARELO if j == i else BRANCO) + _limpa(x.text) for j, x in enumerate(ch))
            # "pop" ao entrar: cresce de 85% para 100% nos primeiros 80 ms do bloco
            pop = r"{\fscx85\fscy85\t(0,80,\fscx100\fscy100)}" if i == 0 else ""
            events.append(f"Dialogue: 0,{_ass_ts(ini)},{_ass_ts(fim)},Corte,,0,0,0,,{pop}{txt}")
    return ASS_HEADER.replace("{margin_v}", str(margin_v)) + "\n".join(events) + "\n"
