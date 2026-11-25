"""Configuração do sistema.

Ordem de prioridade: valores padrão < arquivo gabecuts.toml < variáveis de ambiente (GABECUTS_*).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class Config:
    # Canal da Twitch
    channel: str = "gabepeixe"
    stream_quality: str = "best"

    # Pastas
    data_dir: Path = Path("data")

    # Gravação (buffer circular de segmentos)
    segment_seconds: int = 10
    buffer_minutes: int = 20
    live_check_interval: int = 60

    # Detecção de momentos
    threshold: float = 4.0          # score mínimo para abrir um momento
    window_seconds: int = 5         # janela deslizante de análise
    analysis_lag: int = 30          # atraso da análise (espera o áudio do segmento ficar pronto)
    baseline_seconds: int = 300     # memória da média móvel (~5 min)
    min_unique_chatters: int = 3    # evita que um único spammer gere corte
    chat_delay: float = 4.0         # tempo médio entre o acontecimento e a reação do chat
    pre_roll: float = 20.0          # contexto antes do momento
    post_roll: float = 8.0          # contexto depois do momento
    min_clip_seconds: float = 15.0
    max_clip_seconds: float = 60.0
    warmup_seconds: int = 60        # não corta antes de ter uma base estatística

    # Peso de cada sinal
    w_chat: float = 1.0
    w_laugh: float = 1.5
    w_hype: float = 1.0
    w_clip_call: float = 1.5
    w_audio: float = 0.7

    # Saída de vídeo
    vertical: bool = True           # gera versão 9:16 (TikTok/Shorts/Reels)
    burn_subtitles: bool = True
    video_crf: int = 23
    video_preset: str = "veryfast"

    # Transcrição (faster-whisper, opcional)
    whisper_model: str = "small"
    whisper_device: str = "auto"
    whisper_language: str = "pt"

    # IA (Claude) – revisa, dá nota, título, descrição e ajusta o corte
    ai_enabled: bool = True
    ai_model: str = "claude-opus-5-5"
    ai_min_score: int = 50          # abaixo disso o corte é descartado

    # Integrações
    discord_webhook_url: str = ""
    web_host: str = "0.0.0.0"
    web_port: int = 8000
    public_base_url: str = ""       # ex: https://cortes.meusite.com (para links no Discord)

    extra: dict = field(default_factory=dict)
    buffer_override: Path | None = None   # usado no modo VOD

    def replace(self, **changes) -> "Config":
        from dataclasses import replace as _r
        return _r(self, **changes)

    # Caminhos derivados
    @property
    def buffer_dir(self) -> Path:
        return self.buffer_override or (self.data_dir / "buffer")

    @property
    def clips_dir(self) -> Path:
        return self.data_dir / "clips"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "gabecuts.db"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.buffer_dir, self.clips_dir):
            d.mkdir(parents=True, exist_ok=True)


def _coerce(value: str, current):
    if isinstance(current, bool):
        return value.strip().lower() in ("1", "true", "yes", "sim", "on")
    if isinstance(current, int):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, Path):
        return Path(value)
    return value


def load_config(path: str | Path | None = None, **overrides) -> Config:
    cfg = Config()
    path = Path(path or os.environ.get("GABECUTS_CONFIG", "gabecuts.toml"))
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)
        for f_ in fields(cfg):
            if f_.name in data:
                setattr(cfg, f_.name, _coerce(str(data[f_.name]), getattr(cfg, f_.name))
                        if not isinstance(data[f_.name], dict) else data[f_.name])
    for f_ in fields(cfg):
        env = os.environ.get(f"GABECUTS_{f_.name.upper()}")
        if env is not None and f_.name not in ("extra", "buffer_override"):
            setattr(cfg, f_.name, _coerce(env, getattr(cfg, f_.name)))
    for k, v in overrides.items():
        if v is not None:
            setattr(cfg, k, v)
    cfg.channel = cfg.channel.lower().lstrip("#")
    return cfg
