"""Classificação de mensagens do chat e análise de áudio."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

# --- Chat -----------------------------------------------------------------

# Risada em PT-BR + emotes de risada comuns na Twitch/7TV/BTTV
LAUGH_RE = re.compile(
    r"(k{3,}|(?:ha){2,}|(?:he){3,}|(?:rs){2,}|kekw|lulw?|omegalul|icant|pepelaugh|"
    r"lmao|xd+\b|😂|🤣|💀|aushau|ashuash|auhsu|kakaka|kekleo|emoji(?:laugh\w*|lol|rofl|joy))",
    re.IGNORECASE,
)
# Hype / choque / tensão
HYPE_RE = re.compile(
    r"(\bpog\w*|poggers|pogu|\bwtf\b|caralho|\bcrl\b|\bmds\b|meu deus|nossa|"
    r"\bno+o+\b|n[aã]o+o+|insano|absurdo|monka\w*|\bgg+\b|🔥|😱|😳|holy|wow|"
    r"\bvai+\b|\bboa+\b|lend[aá]rio|aaaa+|\buau\b|\bkreygasm\b)",
    re.IGNORECASE,
)
# Chat pedindo corte
CLIP_RE = re.compile(r"\b(clip(a|e|ou|ar|em|ped)?|clipp?\w*|corta|corte isso|cortem)\b", re.IGNORECASE)


def classify_message(text: str) -> tuple[bool, bool, bool]:
    """Retorna (risada, hype, pedido_de_clip)."""
    return (bool(LAUGH_RE.search(text)), bool(HYPE_RE.search(text)), bool(CLIP_RE.search(text)))


def normalize_for_spam(text: str) -> str:
    """Normaliza mensagem para detectar ondas de copypasta/emote repetido."""
    t = text.lower().strip()
    t = re.sub(r"(.)\1{2,}", r"\1\1", t)  # kkkkkk -> kk
    return re.sub(r"\s+", " ", t)[:40]


# --- Áudio ----------------------------------------------------------------

def audio_levels(path: Path | str, sample_rate: int = 16000, bucket: float = 1.0) -> list[float]:
    """Decodifica o áudio de um arquivo com ffmpeg e devolve o volume (dBFS) por bucket de tempo.

    Usa o pico de RMS em sub-janelas de 100ms dentro de cada bucket – gritos e risadas curtas
    aparecem melhor assim do que na média do segundo inteiro.
    """
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sample_rate),
           "-f", "s16le", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, check=True, timeout=120).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size == 0:
        return []
    sub = int(sample_rate * 0.1)
    per_bucket = int(round(bucket / 0.1))
    n_sub = samples.size // sub
    if n_sub == 0:
        return []
    frames = samples[: n_sub * sub].reshape(n_sub, sub)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    out = []
    for i in range(0, n_sub, per_bucket):
        chunk = db[i:i + per_bucket]
        out.append(float(np.max(chunk)))
    return out


# --- Filtro do chat: o que NÃO é reação a um momento ------------------------------------------
BOTS = {"botrix", "kickbot", "streamelements", "nightbot", "fossabot", "moobot", "sery_bot",
        "kicklet", "wizebot", "streamlabs", "kickbotfan", "botrixoficial"}
VOTO_RE = re.compile(r"^\s*(\d{1,2}|[a-e])\s*$", re.IGNORECASE)  # "1", "2", "a"... (votação/enquete)


def ignorar_mensagem(user: str, text: str) -> bool:
    """Bots, comandos (!sorteio) e votos de enquete enchem o chat sem ser reação a nada."""
    u = (user or "").lower().split(":")[-1]
    t = (text or "").strip()
    return (u in BOTS or u.endswith("bot") or t.startswith("!") or bool(VOTO_RE.match(t))
            or not t)
