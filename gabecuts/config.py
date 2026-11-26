"""Configuração do sistema.

Ordem de prioridade: valores padrão < arquivo gabecuts.toml < variáveis de ambiente (GABECUTS_*).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


PLATFORMS = ("twitch", "kick")


@dataclass
class Config:
    # Canal e plataforma
    channel: str = "gabepeixe"
    platform: str = "twitch"        # de onde vem o VÍDEO: "twitch" ou "kick"
    chat_sources: str = ""          # chats lidos, separados por vírgula (padrão: a plataforma).
                                    # Em multistream use "twitch,kick" para somar os dois chats.
    twitch_channel: str = ""        # se o nome do canal for diferente em cada plataforma
    kick_channel: str = ""
    kick_chatroom_id: int = 0       # opcional: evita consultar a API da Kick (Cloudflare)
    kick_pusher_key: str = ""       # opcional: chave do Pusher do chat da Kick
    stream_quality: str = "auto"    # auto = até record_max_height; ou "best", "720p60", "480p"...
    record_max_height: int = 720    # grava em 720p: metade da internet e da CPU de 1080p

    # Pastas
    data_dir: Path = Path("data")

    # Gravação (buffer circular de segmentos)
    segment_seconds: int = 10
    buffer_minutes: int = 20
    live_check_interval: int = 60

    # Detecção de momentos
    threshold: float = 3.0          # score mínimo para abrir um momento (maior = menos cortes)
    target_clips_per_hour: int = 12 # se ficar muito tempo sem corte, o limiar desce sozinho (0 = desliga)
    threshold_floor: float = 1.8    # limite mínimo dessa descida
    window_seconds: int = 5         # janela deslizante de análise
    analysis_lag: int = 30          # atraso da análise (espera o áudio do segmento ficar pronto)
    baseline_seconds: int = 300     # memória da média móvel (~5 min)
    min_unique_chatters: int = 3    # evita que um único spammer gere corte
    chat_delay: float = 4.0         # tempo médio entre o acontecimento e a reação do chat
    pre_roll: float = 12.0          # contexto antes do momento (TikTok: gancho rápido)
    post_roll: float = 8.0          # contexto depois do momento
    min_clip_seconds: float = 15.0
    max_clip_seconds: float = 45.0  # cortes de TikTok: 15–45 s retêm melhor
    warmup_seconds: int = 60        # não corta antes de ter uma base estatística

    # Peso de cada sinal
    w_chat: float = 1.0
    w_laugh: float = 1.5
    w_hype: float = 1.0
    w_clip_call: float = 1.5
    w_audio: float = 0.7

    # Saída de vídeo
    vertical: bool = True           # gera versão 9:16 (TikTok/Shorts/Reels)
    burn_subtitles: bool = True     # legenda palavra por palavra (estilo TikTok)
    cut_silences: bool = False      # tira silêncios parados. Desligado: em vídeo de jogo os cortes
                                    # no meio da conversa parecem "travadas" (pulos na imagem)
    zoom_on_reaction: bool = True   # zoom rápido na hora da reação
    music_dir: str = ""             # pasta com músicas de fundo (opcional; use só o que pode usar)
    video_crf: int = 23
    video_preset: str = "veryfast"
    encode_threads: int = 2         # núcleos usados pelo ffmpeg ao gerar cortes (deixa o PC livre)
    open_live_in_browser: bool = True  # abre a live no navegador quando ela começa (pontos do canal)
    low_priority: bool = True       # roda com prioridade baixa: o navegador/live vem primeiro

    # Transcrição (faster-whisper, opcional)
    whisper_model: str = "auto"     # auto = "small" com GPU NVIDIA, "base" só com CPU; "off" desliga
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

    def channel_for(self, platform: str) -> str:
        override = {"twitch": self.twitch_channel, "kick": self.kick_channel}.get(platform, "")
        return (override or self.channel).lower().lstrip("#@")

    @property
    def chats(self) -> list[str]:
        items = [c.strip().lower() for c in (self.chat_sources or self.platform).split(",")]
        return [c for c in dict.fromkeys(items) if c]

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
    cfg.channel = cfg.channel.lower().lstrip("#@")
    cfg.platform = cfg.platform.lower()
    for p_ in [cfg.platform, *cfg.chats]:
        if p_ not in PLATFORMS:
            raise ValueError(f"plataforma desconhecida: {p_!r} (use {', '.join(PLATFORMS)})")
    return cfg
