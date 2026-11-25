"""Transcrição com faster-whisper (opcional). Sem o pacote instalado, o sistema segue sem legenda."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)
_model = None


@dataclass
class Line:
    start: float
    end: float
    text: str


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
               language: str = "pt") -> list[Line]:
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
        _model = WhisperModel(model_size, device=dev, compute_type=compute)
    segments, _info = _model.transcribe(str(path), language=language, vad_filter=True,
                                        beam_size=1, word_timestamps=False)
    return [Line(s.start, s.end, s.text.strip()) for s in segments if s.text.strip()]


def shift(lines: list[Line], offset: float, limit: float | None = None) -> list[Line]:
    """Desloca legendas (após um corte fino) e descarta as que ficam fora do vídeo."""
    out = []
    for ln in lines:
        s, e = ln.start - offset, ln.end - offset
        if e <= 0 or (limit is not None and s >= limit):
            continue
        out.append(Line(max(s, 0.0), min(e, limit) if limit else e, ln.text))
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
