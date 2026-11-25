"""Orquestração: ao vivo (chat + gravação + detecção + cortes) e modo VOD (arquivo + chat exportado)."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import httpx

from . import ai, clipper, transcribe
from .chat import ChatMessage, read_chat
from .config import Config
from .db import ClipDB
from .detector import Moment, MomentDetector
from .recorder import (Recorder, cleanup_buffer, is_live, list_segments, segments_for_range,
                       split_file_to_segments)
from .signals import audio_levels

log = logging.getLogger(__name__)


class ClipProcessor:
    """Transforma um Moment em arquivos de vídeo + metadados no banco."""

    def __init__(self, cfg: Config, db: ClipDB):
        self.cfg = cfg
        self.db = db
        self.chat_history: deque[ChatMessage] = deque(maxlen=50_000)

    def remember(self, msg: ChatMessage) -> None:
        self.chat_history.append(msg)

    def chat_sample(self, t0: float, t1: float) -> list[str]:
        # o chat reage com atraso: olha um pouco além do fim do corte
        return [f"[{m.ts - t0:5.1f}] {m.user}: {m.text[:200]}"
                for m in self.chat_history if t0 <= m.ts <= t1 + self.cfg.chat_delay + 5]

    def process(self, m: Moment) -> int | None:
        cfg = self.cfg
        segs = segments_for_range(list_segments(cfg.buffer_dir, cfg.segment_seconds, include_last=True),
                                  m.clip_start, m.clip_end)
        if not segs:
            log.warning("Momento %.0f sem vídeo no buffer (gravação ativa?)", m.peak_time)
            return None
        t0 = max(m.clip_start, segs[0].start)
        t1 = min(m.clip_end, segs[-1].end)
        stamp = datetime.fromtimestamp(m.peak_time).strftime("%Y%m%d_%H%M%S")
        folder = cfg.clips_dir / f"{stamp}_{m.category.replace(' ', '-')}"
        folder.mkdir(parents=True, exist_ok=True)

        raw = clipper.cut_from_segments(segs, t0, t1, folder / "raw.mp4", cfg.video_crf, cfg.video_preset)
        duration = t1 - t0

        lines = transcribe.transcribe(raw, cfg.whisper_model, cfg.whisper_device, cfg.whisper_language)
        transcript = transcribe.to_text(lines)

        chat = self.chat_sample(t0, t1)
        peak_offset = m.peak_time - cfg.chat_delay - t0
        if cfg.ai_enabled:
            rv = ai.ai_review(cfg.ai_model, duration, transcript, chat, m.stats, m.category,
                              peak_offset, m.peak_score)
        else:
            rv = ai.heuristic_review(duration, m.category, m.stats, m.peak_score)

        status = "pendente"
        if not rv.keep or rv.score < cfg.ai_min_score:
            status = "descartado"
            log.info("Corte descartado (%s, nota %d): %s", m.category, rv.score, rv.reason)

        final = folder / "corte.mp4"
        if rv.trim_start > 0.5 or rv.trim_end < duration - 0.5:
            clipper.trim(raw, rv.trim_start, rv.trim_end, final, cfg.video_crf, cfg.video_preset)
            raw.unlink(missing_ok=True)
        else:
            raw.rename(final)
        final_dur = rv.trim_end - rv.trim_start

        lines = transcribe.shift(lines, rv.trim_start, final_dur)
        srt = None
        if lines:
            srt = folder / "legenda.srt"
            srt.write_text(transcribe.to_srt(lines), encoding="utf-8")

        vertical = None
        if cfg.vertical and status != "descartado":
            try:
                vertical = clipper.make_vertical(final, folder / "vertical.mp4",
                                                 srt if cfg.burn_subtitles else None, rv.title,
                                                 cfg.video_crf, cfg.video_preset)
            except clipper.ClipError as e:
                log.error("Falha ao gerar vertical: %s", e)
        thumb = clipper.thumbnail(final, folder / "thumb.jpg", max(0.0, peak_offset - rv.trim_start))

        meta = dict(title=rv.title, description=rv.description, hashtags=rv.hashtags,
                    category=rv.category, score=rv.score, reason=rv.reason, by_ai=rv.by_ai,
                    clip_start=t0 + rv.trim_start, clip_end=t0 + rv.trim_end, stats=m.stats,
                    transcript=transcript)
        (folder / "info.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        rel = lambda p: str(p.relative_to(cfg.clips_dir)) if p else None  # noqa: E731
        clip_id = self.db.insert(
            channel=cfg.channel, t_start=t0 + rv.trim_start, t_end=t0 + rv.trim_end,
            peak_score=m.peak_score, ai_score=rv.score, category=rv.category, title=rv.title,
            description=rv.description, hashtags=rv.hashtags, reason=rv.reason, by_ai=int(rv.by_ai),
            status=status, file_h=rel(final), file_v=rel(vertical), thumb=rel(thumb),
            transcript=transcript, stats=m.stats)
        log.info("✂️  Corte #%d [%s] nota %d: %s (%.0fs)", clip_id, rv.category, rv.score, rv.title,
                 final_dur)
        if status != "descartado":
            self.notify(clip_id, rv, final_dur)
        return clip_id

    def notify(self, clip_id: int, rv: ai.Review, duration: float) -> None:
        if not self.cfg.discord_webhook_url:
            return
        link = f"{self.cfg.public_base_url.rstrip('/')}/#clip-{clip_id}" if self.cfg.public_base_url else ""
        content = (f"✂️ **Novo corte #{clip_id}** — {rv.title}\n"
                   f"Nota {rv.score} · {rv.category} · {duration:.0f}s\n{rv.description}\n"
                   + " ".join(f"#{h}" for h in rv.hashtags) + (f"\n{link}" if link else ""))
        try:
            httpx.post(self.cfg.discord_webhook_url, json={"content": content[:1900]}, timeout=10)
        except httpx.HTTPError as e:
            log.warning("Falha no webhook do Discord: %s", e)


class LiveRunner:
    """Roda indefinidamente: espera a live abrir, grava, analisa e corta."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        cfg.ensure_dirs()
        self.db = ClipDB(cfg.db_path)
        self.detector = MomentDetector(cfg)
        self.processor = ClipProcessor(cfg, self.db)
        self.recorder: Recorder | None = None
        self.live = False
        self.analyzed: set[str] = set()
        self.queue: asyncio.Queue[Moment] = asyncio.Queue()
        self.stats = dict(messages=0, moments=0, started_at=time.time())

    async def run(self) -> None:
        await asyncio.gather(self._chat_loop(), self._live_loop(), self._segment_loop(),
                             self._detect_loop(), self._clip_worker())

    async def _chat_loop(self) -> None:
        async for msg in read_chat(self.cfg.channel):
            self.stats["messages"] += 1
            if self.live:  # só pontua enquanto há vídeo sendo gravado
                self.detector.add_message(msg)
                self.processor.remember(msg)

    async def _live_loop(self) -> None:
        while True:
            live = await asyncio.to_thread(is_live, self.cfg.channel)
            if live and not (self.recorder and self.recorder.running()):
                if not self.live:
                    log.info("🔴 %s está AO VIVO", self.cfg.channel)
                    self.detector = MomentDetector(self.cfg)  # baseline nova a cada live
                else:
                    log.warning("Gravação caiu durante a live; reiniciando")
                self.recorder = Recorder(self.cfg.channel, self.cfg.buffer_dir,
                                         self.cfg.segment_seconds, self.cfg.stream_quality)
                self.recorder.start()
                self.live = True
            elif not live and self.live:
                log.info("⚫ Live encerrada")
                for m in self.detector.flush():
                    await self.queue.put(m)
                if self.recorder:
                    self.recorder.stop()
                self.live = False
            await asyncio.sleep(self.cfg.live_check_interval if not self.live else 30)

    async def _segment_loop(self) -> None:
        """Mede o áudio de cada segmento assim que ele fica pronto e limpa o buffer."""
        while True:
            for seg in list_segments(self.cfg.buffer_dir, self.cfg.segment_seconds):
                if seg.path.name in self.analyzed:
                    continue
                levels = await asyncio.to_thread(audio_levels, seg.path)
                self.detector.add_audio(seg.start, levels)
                self.analyzed.add(seg.path.name)
            keep = max(self.cfg.buffer_minutes * 60, self.cfg.analysis_lag + self.cfg.max_clip_seconds * 3)
            await asyncio.to_thread(cleanup_buffer, self.cfg.buffer_dir, keep)
            existing = {p.name for p in self.cfg.buffer_dir.glob("seg_*.ts")}
            self.analyzed &= existing
            await asyncio.sleep(2)

    async def _detect_loop(self) -> None:
        while True:
            if self.live:
                for m in self.detector.tick(time.time()):
                    self.stats["moments"] += 1
                    log.info("⚡ Momento detectado: %s score %.1f", m.category, m.peak_score)
                    await self.queue.put(m)
            await asyncio.sleep(1)

    async def _clip_worker(self) -> None:
        while True:
            m = await self.queue.get()
            # espera o vídeo até o fim do corte estar gravado
            while self.live and time.time() < m.clip_end + self.cfg.segment_seconds + 2:
                await asyncio.sleep(2)
            try:
                await asyncio.to_thread(self.processor.process, m)
            except Exception:  # noqa: BLE001
                log.exception("Erro ao processar momento")


# ---------------------------------------------------------------------------
# Modo VOD: processa um vídeo já gravado + chat exportado
# ---------------------------------------------------------------------------

def load_chat_file(path: Path, base_epoch: float) -> list[ChatMessage]:
    """Aceita JSON do TwitchDownloader ({"comments":[...]}) ou JSONL {"t": seg, "user":..., "text":...}."""
    text = path.read_text(encoding="utf-8")
    msgs: list[ChatMessage] = []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and "comments" in data:
        for c in data["comments"]:
            body = (c.get("message") or {}).get("body", "")
            user = (c.get("commenter") or {}).get("display_name", "?")
            msgs.append(ChatMessage(base_epoch + float(c["content_offset_seconds"]), user, body))
    else:
        for line in text.splitlines():
            if line.strip():
                d = json.loads(line)
                msgs.append(ChatMessage(base_epoch + float(d["t"]), d.get("user", "?"), d.get("text", "")))
    msgs.sort(key=lambda m: m.ts)
    return msgs


def process_vod(cfg: Config, video: Path, chat_file: Path | None = None) -> list[int]:
    cfg.ensure_dirs()
    work = cfg.data_dir / "vod_buffer"
    shutil.rmtree(work, ignore_errors=True)
    base = 1_000_000_000  # epoch artificial: segundo 0 do VOD
    vcfg = cfg.replace(buffer_override=work)

    msgs = load_chat_file(chat_file, base) if chat_file else []
    if not msgs:
        log.warning("Sem chat: detecção apenas pelo áudio (menos precisa)")
        vcfg = vcfg.replace(w_audio=max(cfg.w_audio, 1.6), threshold=min(cfg.threshold, 3.5),
                            chat_delay=0.0)

    log.info("Segmentando %s ...", video)
    segs = split_file_to_segments(video, work, base, cfg.segment_seconds)
    det = MomentDetector(vcfg)
    proc = ClipProcessor(vcfg, ClipDB(cfg.db_path))

    log.info("Analisando áudio de %d segmentos ...", len(segs))
    for s in segs:
        det.add_audio(s.start, audio_levels(s.path))
    for m in msgs:
        det.add_message(m)
        proc.remember(m)

    end = segs[-1].end if segs else base
    moments = det.tick(end + vcfg.analysis_lag) + det.flush()
    log.info("%d momentos encontrados", len(moments))
    ids = []
    for m in moments:
        try:
            cid = proc.process(m)
        except clipper.ClipError as e:
            log.error("Falha no corte: %s", e)
            continue
        if cid:
            ids.append(cid)
    shutil.rmtree(work, ignore_errors=True)
    return ids
