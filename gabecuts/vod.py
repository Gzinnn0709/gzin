"""Cortes de live passada (VOD da Kick) SEM baixar a live inteira.

1. Descobre os bons momentos sem vídeo: clipes que os espectadores fizeram daquela live e o chat
   (só texto, bem leve).
2. Baixa só os 1-2 minutos de cada momento direto do VOD (HLS), corta e apaga o pedaço.
   No máximo ~100 MB ficam no disco por vez.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import kick
from .chat import ChatMessage
from .config import Config
from .db import ClipDB
from .detector import MomentDetector, curar
from .recorder import pick_variant, split_file_to_segments

log = logging.getLogger(__name__)

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
VIDEO_API = "https://kick.com/api/v1/video/{uuid}"
CHAT_API = "https://kick.com/api/v2/channels/{channel_id}/messages?start_time={iso}"
CLIPS_ALL_API = "https://kick.com/api/v2/channels/{slug}/clips?cursor={cursor}&sort=date&time=all"


@dataclass
class VodInfo:
    uuid: str
    slug: str
    channel_id: int | None
    inicio: float          # epoch do começo da live
    duracao: float         # segundos
    fonte: str             # playlist HLS (.m3u8) do VOD
    titulo: str = ""


def parse_vod_url(url: str) -> str:
    m = UUID_RE.search(url or "")
    if not m or "kick.com" not in (url or "").lower():
        raise ValueError("link inválido: use o link do vídeo da Kick (kick.com/<canal>/videos/...)")
    return m.group(0).lower()


def _epoch(valor) -> float | None:
    if not valor:
        return None
    s = str(valor).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s.replace(" ", "T") if "T" not in s else s)
    except ValueError:
        return None
    if dt.tzinfo is None:  # a Kick manda "2026-11-25 22:00:00" em UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_vod_info(uuid: str, data: dict) -> VodInfo:
    ls = data.get("livestream") or {}
    canal = ls.get("channel") or data.get("channel") or {}
    inicio = _epoch(ls.get("start_time")) or _epoch(ls.get("created_at")) or _epoch(data.get("created_at"))
    dur = ls.get("duration") or data.get("duration") or 0
    dur = float(dur)
    if dur > 100_000:  # a Kick costuma mandar em milissegundos
        dur /= 1000.0
    fonte = data.get("source") or ls.get("source")
    if not inicio or not fonte:
        raise ValueError("a Kick não devolveu o endereço ou o horário desse VOD")
    return VodInfo(uuid=uuid, slug=(canal.get("slug") or "").lower(), channel_id=canal.get("id"),
                   inicio=inicio, duracao=dur, fonte=fonte, titulo=ls.get("session_title") or "")


def vod_info(url: str) -> VodInfo:
    uuid = parse_vod_url(url)
    return parse_vod_info(uuid, kick._get_json(VIDEO_API.format(uuid=uuid)))


def vod_clips(slug: str, inicio: float, fim: float, max_paginas: int = 40) -> list[float]:
    """Horários (epoch) dos clipes que os espectadores fizeram durante aquela live."""
    out: list[float] = []
    cursor = "0"
    for _ in range(max_paginas):
        data = kick._get_json(CLIPS_ALL_API.format(slug=slug, cursor=cursor))
        clips = kick.parse_clips(data)
        if not clips:
            break
        out += [c["ts"] for c in clips if inicio <= c["ts"] <= fim + 300]
        if min(c["ts"] for c in clips) < inicio:  # já passou do começo da live (ordem: mais novos antes)
            break
        cursor = (data.get("nextCursor") or data.get("next_cursor") or "") if isinstance(data, dict) else ""
        if not cursor:
            break
        time.sleep(0.3)
    return sorted(out)


def vod_chat(channel_id, inicio: float, fim: float, progresso: Callable[[float], None] | None = None,
             max_pedidos: int = 6000, max_minutos: float = 45.0) -> list[ChatMessage]:
    """Replay do chat (só texto). Para quando passar do limite de pedidos/tempo e segue com o que tiver."""
    msgs: list[ChatMessage] = []
    t, pedidos, t0 = inicio, 0, time.time()
    erros = 0
    while t < fim and pedidos < max_pedidos and time.time() - t0 < max_minutos * 60:
        iso = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        try:
            data = kick._get_json(CHAT_API.format(channel_id=channel_id, iso=iso))
            erros = 0
        except Exception as e:  # noqa: BLE001
            erros += 1
            if erros >= 5:
                log.warning("Replay do chat parou (%s); sigo só com os clipes", str(e)[:120])
                break
            time.sleep(2)
            continue
        pedidos += 1
        bloco = ((data or {}).get("data") or {}).get("messages") or []
        ultimo = t
        for m in bloco:
            ts = _epoch(m.get("created_at"))
            if ts is None or ts < t - 1:
                continue
            texto = re.sub(r"\[emote:\d+:([^\]]+)\]", r"\1", m.get("content") or "")
            msgs.append(ChatMessage(ts, (m.get("sender") or {}).get("username") or "?", texto))
            ultimo = max(ultimo, ts)
        t = max(t + 5.0, ultimo + 0.5)  # avança pelo menos 5 s por pedido
        if progresso and pedidos % 50 == 0:
            progresso((t - inicio) / max(fim - inicio, 1))
        time.sleep(0.15)
    vistos, unicos = set(), []
    for m in sorted(msgs, key=lambda x: x.ts):
        chave = (round(m.ts, 2), m.user, m.text)
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(m)
    return unicos


def baixar_trecho(fonte: str, offset: float, duracao: float, out: Path) -> Path:
    """Baixa só um pedaço do VOD (o ffmpeg pula direto para o trecho na playlist HLS)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    rede = (["-user_agent", kick.BROWSER_UA,
             "-headers", "Referer: https://kick.com/\r\nOrigin: https://kick.com\r\n"]
            if str(fonte).startswith("http") else [])
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *rede,
                        "-ss", f"{max(offset, 0):.2f}", "-i", fonte, "-t", f"{duracao:.2f}",
                        "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-f", "mpegts", str(out)],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not out.exists() or out.stat().st_size < 10_000:
        raise RuntimeError(f"não consegui baixar o trecho: {r.stderr[-300:]}")
    return out


def cortar_vod(cfg: Config, url: str, db: ClipDB, progresso: Callable[[str, float], None] | None = None,
               pausar: Callable[[], bool] | None = None) -> list[int]:
    """Faz os cortes de uma live passada. `pausar()` True = espera (ex.: a live ao vivo começou)."""
    from .pipeline import ClipProcessor  # evita import circular

    def etapa(nome: str, frac: float = 0.0) -> None:
        log.info("VOD: %s (%.0f%%)", nome, frac * 100)
        if progresso:
            progresso(nome, frac)

    def esperar_se_preciso() -> None:
        while pausar and pausar():
            etapa("pausado: a live ao vivo tem prioridade", 0)
            time.sleep(30)

    etapa("lendo o VOD")
    info = vod_info(url)
    slug = info.slug or cfg.channel_for("kick")
    fim = info.inicio + (info.duracao or 12 * 3600)
    etapa("procurando os clipes dos espectadores")
    try:
        clipes = vod_clips(slug, info.inicio, fim)
    except Exception as e:  # noqa: BLE001
        log.warning("Não consegui ler os clipes desse VOD (%s)", str(e)[:150])
        clipes = []
    etapa("lendo o chat da live (só texto)")
    msgs: list[ChatMessage] = []
    if info.channel_id:
        try:
            msgs = vod_chat(info.channel_id, info.inicio, fim,
                            progresso=lambda f: etapa("lendo o chat da live (só texto)", f))
        except Exception as e:  # noqa: BLE001
            log.warning("Replay do chat indisponível (%s)", str(e)[:150])
    log.info("VOD: %d clipes de espectadores e %d mensagens de chat", len(clipes), len(msgs))

    vcfg = cfg.replace(analysis_lag=0, warmup_seconds=120)
    if not msgs:  # só com os clipes: 2 clipes perto já contam como momento
        vcfg = vcfg.replace(threshold=min(cfg.threshold, 3.0), threshold_floor=min(cfg.threshold_floor, 3.0))
    det = MomentDetector(vcfg)
    det.first_t = int(info.inicio)
    for m in msgs:
        det.add_message(m)
    for ts in clipes:
        det.add_kick_clip(ts)
    momentos = [m for m in det.tick(fim + 60) + det.flush()
                if m.clip_end > info.inicio and m.clip_start < fim]
    momentos = curar(momentos, cfg.best_window_minutes, cfg.best_per_window)
    etapa(f"{len(momentos)} momentos encontrados", 0)
    if not momentos:
        return []

    work = cfg.data_dir / "vod_trecho"
    proc = ClipProcessor(vcfg.replace(buffer_override=work / "buf"), db)
    for m in msgs:
        proc.remember(m)
    fonte = pick_variant(info.fonte, cfg.record_max_height)
    ids = []
    for i, m in enumerate(momentos, 1):
        esperar_se_preciso()
        etapa(f"cortando momento {i} de {len(momentos)}", (i - 1) / len(momentos))
        shutil.rmtree(work, ignore_errors=True)
        ini = max(m.clip_start - 2, info.inicio)
        dur = m.clip_end - ini + 2
        try:
            trecho = baixar_trecho(fonte, ini - info.inicio, dur, work / "trecho.ts")
            split_file_to_segments(trecho, work / "buf", int(round(ini)), cfg.segment_seconds)
            trecho.unlink(missing_ok=True)
            cid = proc.process(m)
            if cid:
                ids.append(cid)
        except Exception as e:  # noqa: BLE001
            log.warning("VOD: momento %d falhou: %s", i, str(e)[:200])
        finally:
            shutil.rmtree(work, ignore_errors=True)  # nunca deixa vídeo da live no disco
    etapa(f"pronto: {len(ids)} cortes", 1.0)
    return ids
