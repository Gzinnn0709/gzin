"""Orquestração: ao vivo (chat + gravação + detecção + cortes) e modo VOD (arquivo + chat exportado)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import httpx

from . import ai, clipper, site, transcribe
from .chat import ChatMessage
from .platforms import check_live, read_all_chats
from .config import Config
from .db import ClipDB
from .detector import Moment, MomentDetector
from .recorder import (Recorder, cleanup_buffer, list_segments, pick_variant, segments_for_range,
                       split_file_to_segments, streamlink_quality)
from .signals import audio_levels

log = logging.getLogger(__name__)


def abrir_no_navegador(url: str) -> None:
    """Abre a live no navegador padrão (para ganhar os pontos/recompensas do canal)."""
    try:
        import webbrowser
        webbrowser.open(url, new=2)
        log.info("🌐 Abri a live no navegador: %s", url)
    except Exception as e:  # noqa: BLE001
        log.debug("Não abri o navegador: %s", e)


def manter_acordado(ligar: bool) -> None:
    """No Windows, impede o notebook de suspender sozinho enquanto a live está sendo gravada."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if ligar else 0))
    except Exception as e:  # noqa: BLE001
        log.debug("Não consegui mudar a suspensão: %s", e)


def _try(fn, what: str):
    """Roda uma etapa opcional; em caso de erro, registra e devolve None."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        log.warning("Etapa '%s' falhou (o corte continua): %s", what, str(e)[-300:])
        return None


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

    def enqueue(self, m: Moment) -> int:
        """Registra o momento no banco já na detecção, para aparecer no painel na hora."""
        return self.db.insert(
            channel=f"{self.cfg.platform}:{self.cfg.channel_for(self.cfg.platform)}",
            t_start=m.clip_start, t_end=m.clip_end, peak_score=m.peak_score, category=m.category,
            title=f"Momento {m.category} detectado (score {m.peak_score:.1f})",
            description="Aguardando a gravação para cortar…", status="na fila", stats=m.stats)

    def process(self, m: Moment, clip_id: int | None = None) -> int | None:
        """Gera o corte. Qualquer etapa opcional pode falhar sem perder o vídeo."""
        if clip_id is None:
            clip_id = self.enqueue(m)
        self.db.update(clip_id, status="processando", description="Cortando o vídeo…")
        try:
            self._process(m, clip_id)
        except Exception as e:  # noqa: BLE001
            log.exception("Erro ao gerar o corte #%d", clip_id)
            self.db.update(clip_id, status="erro", reason=str(e)[:500],
                           description="Falhou ao gerar o corte. Veja o motivo abaixo.")
        return clip_id

    def _process(self, m: Moment, clip_id: int) -> None:
        cfg = self.cfg
        rel = lambda p: str(p.relative_to(cfg.clips_dir)) if p else None  # noqa: E731
        segs = segments_for_range(list_segments(cfg.buffer_dir, cfg.segment_seconds, include_last=True),
                                  m.clip_start, m.clip_end)
        if not segs:
            raise clipper.ClipError("não há vídeo gravado para esse momento – a gravação está "
                                    "funcionando? Veja data/ffmpeg.log")
        t0 = max(m.clip_start, segs[0].start)
        t1 = min(m.clip_end, segs[-1].end)
        duration = t1 - t0
        stamp = datetime.fromtimestamp(m.peak_time).strftime("%Y%m%d_%H%M%S")
        folder = cfg.clips_dir / f"{stamp}_{clip_id}_{m.category.replace(' ', '-')}"
        folder.mkdir(parents=True, exist_ok=True)
        peak_offset = max(0.0, m.peak_time - cfg.chat_delay - t0)

        # 1) corte bruto 16:9 – já fica visível no painel
        tc = time.time()
        raw = clipper.cut_from_segments(segs, t0, t1, folder / "bruto.mp4", cfg.video_crf, cfg.video_preset)
        thumb = _try(lambda: clipper.thumbnail(raw, folder / "thumb.jpg", peak_offset), "miniatura")
        self.db.update(clip_id, file_h=rel(raw), thumb=rel(thumb), t_start=t0, t_end=t1,
                       description="Corte pronto. Transcrevendo e revisando…")
        log.info("✂️  Corte #%d cortado em %.0fs (%s, %.0fs de vídeo)", clip_id, time.time() - tc,
                 m.category, duration)

        # 2) transcrição (opcional)
        lines: list = []
        if cfg.whisper_model.lower() not in ("off", "none", "0", ""):
            tw = time.time()
            lines = _try(lambda: transcribe.transcribe(raw, cfg.whisper_model, cfg.whisper_device,
                                                       cfg.whisper_language, cfg.encode_threads),
                         "transcrição") or []
            if lines:
                log.info("Corte #%d transcrito em %.0fs", clip_id, time.time() - tw)
        transcript = transcribe.to_text(lines)

        # 3) revisão (IA ou heurística)
        chat = self.chat_sample(t0, t1)
        rv = None
        if cfg.ai_enabled:
            rv = _try(lambda: ai.ai_review(cfg.ai_model, duration, transcript, chat, m.stats,
                                           m.category, peak_offset, m.peak_score), "revisão da IA")
        rv = rv or ai.heuristic_review(duration, m.category, m.stats, m.peak_score)
        status = "pendente"
        # a nota mínima só vale para a IA; sem ela, todo momento detectado vira corte
        if rv.by_ai and (not rv.keep or rv.score < cfg.ai_min_score):
            status = "descartado"
            log.info("Corte #%d descartado (nota %d): %s", clip_id, rv.score, rv.reason)

        # 4) corte final (ajuste fino da IA)
        final = raw
        if rv.trim_start > 0.5 or rv.trim_end < duration - 0.5:
            final = _try(lambda: clipper.trim(raw, rv.trim_start, rv.trim_end, folder / "corte.mp4",
                                              cfg.video_crf, cfg.video_preset), "ajuste fino") or raw
        if final == raw:
            rv.trim_start, rv.trim_end = 0.0, duration
        final_dur = rv.trim_end - rv.trim_start
        lines = transcribe.shift(lines, rv.trim_start, final_dur)
        srt = None
        if lines:
            srt = folder / "legenda.srt"
            srt.write_text(transcribe.to_srt(lines), encoding="utf-8")

        # 5) vertical 9:16 (tenta com legenda+título; se falhar, sem eles)
        vertical = None
        if cfg.vertical and status != "descartado":
            self.db.update(clip_id, description="Gerando versão vertical…")
            for use_srt, use_title in ((cfg.burn_subtitles, True), (False, True), (False, False)):
                try:
                    vertical = clipper.make_vertical(final, folder / "vertical.mp4",
                                                     srt if use_srt else None,
                                                     rv.title if use_title else None,
                                                     cfg.video_crf, cfg.video_preset)
                    break
                except clipper.ClipError as e:
                    log.warning("Vertical falhou (legenda=%s, título=%s): %s", use_srt, use_title,
                                str(e)[-300:])
        if final != raw:
            thumb = _try(lambda: clipper.thumbnail(final, folder / "thumb.jpg",
                                                   max(0.0, peak_offset - rv.trim_start)), "miniatura") or thumb

        meta = dict(title=rv.title, description=rv.description, hashtags=rv.hashtags,
                    category=rv.category, score=rv.score, reason=rv.reason, by_ai=rv.by_ai,
                    clip_start=t0 + rv.trim_start, clip_end=t0 + rv.trim_end, stats=m.stats,
                    transcript=transcript)
        (folder / "info.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        self.db.update(clip_id, t_start=t0 + rv.trim_start, t_end=t0 + rv.trim_end,
                       ai_score=rv.score, category=rv.category, title=rv.title,
                       description=rv.description, hashtags=json.dumps(rv.hashtags, ensure_ascii=False),
                       reason=rv.reason, by_ai=int(rv.by_ai), status=status, file_h=rel(final),
                       file_v=rel(vertical), thumb=rel(thumb), transcript=transcript)
        if final != raw:
            _try(lambda: raw.unlink(missing_ok=True), "limpeza")  # no Windows pode estar aberto no painel
        log.info("✅ Corte #%d pronto [%s] nota %d: %s (%.0fs)", clip_id, rv.category, rv.score, rv.title,
                 final_dur)
        if status != "descartado":
            self.notify(clip_id, rv, final_dur)

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
        self.site = site.SiteUploader(cfg, self.db)
        self.recorder: Recorder | None = None
        self.live = False
        self.analyzed: set[str] = set()
        self.queue: asyncio.Queue[tuple[int, Moment]] = asyncio.Queue()
        self.stats = dict(messages=0, moments=0, started_at=time.time())
        # cortes que ficaram pela metade numa execução anterior
        for c in self.db.list(limit=500):
            if c["status"] in ("na fila", "processando"):
                self.db.update(c["id"], status="erro", reason="o programa foi fechado antes de terminar")

    def submit(self, m: Moment) -> int:
        clip_id = self.processor.enqueue(m)
        self.queue.put_nowait((clip_id, m))
        return clip_id

    def clip_now(self, seconds: float = 45.0) -> int:
        """Corte manual dos últimos `seconds` segundos (botão do painel)."""
        now = time.time()
        m = Moment(event_start=now, event_end=now, peak_time=now, peak_score=0.0,
                   clip_start=now - seconds, clip_end=now, category="manual",
                   stats={"manual": True})
        log.info("✂️  Corte manual pedido no painel (%.0fs)", seconds)
        return self.submit(m)

    async def run(self) -> None:
        await asyncio.gather(self._chat_loop(), self._live_loop(), self._segment_loop(),
                             self._detect_loop(), self._clip_worker(), self._site_loop())

    async def _site_loop(self) -> None:
        """Manda os cortes prontos e a situação para a página /cortes do site (se conectado).

        A cada minuto também pergunta se alguém pediu "Cortar agora" pela página.
        """
        ultimo_status = 0.0
        while True:
            try:
                await asyncio.to_thread(self.site.sync)
                # durante a live: a cada minuto. Fora dela: a cada 30 min, para o Render grátis
                # poder dormir (o plano grátis tem horas limitadas por mês)
                if time.time() - ultimo_status >= (60 if self.live else 1800):
                    ultimo_status = time.time()
                    dados = dict(live=self.live, gravando=bool(self.recorder and self.recorder.running()),
                                 canal=f"{self.cfg.platform}:{self.cfg.channel_for(self.cfg.platform)}",
                                 fila=self.queue.qsize(), mensagens=self.stats["messages"],
                                 momentos=self.stats["moments"])
                    segundos = await asyncio.to_thread(site.enviar_status, self.cfg, dados)
                    if segundos and self.live:
                        log.info("✂️  'Cortar agora' pedido pela página do site")
                        self.clip_now(float(segundos))
            except Exception as e:  # noqa: BLE001
                log.warning("Site: %s", str(e)[:200])
            await asyncio.sleep(20)

    async def _chat_loop(self) -> None:
        async for msg in read_all_chats(self.cfg):
            self.stats["messages"] += 1
            if self.live:  # só pontua enquanto há vídeo sendo gravado
                self.detector.add_message(msg)
                self.processor.remember(msg)

    async def _live_loop(self) -> None:
        while True:
            st = await asyncio.to_thread(check_live, self.cfg)
            live = st.live
            if live and not (self.recorder and self.recorder.running()):
                if not self.live:
                    log.info("🔴 %s está AO VIVO (%s)", self.cfg.channel_for(self.cfg.platform),
                             self.cfg.platform)
                    self.detector = MomentDetector(self.cfg)  # baseline nova a cada live
                    manter_acordado(True)
                    if self.cfg.open_live_in_browser:
                        abrir_no_navegador(st.page_url)
                else:
                    why = self.recorder.exit_info() if self.recorder else ""
                    log.warning("Gravação caiu durante a live; reiniciando. Motivo: %s",
                                why or "(veja data/ffmpeg.log)")
                if self.recorder:
                    self.recorder.stop()
                hls = st.hls_url
                if hls and self.cfg.stream_quality in ("auto", ""):
                    hls = await asyncio.to_thread(pick_variant, hls, self.cfg.record_max_height)
                self.recorder = Recorder(st.page_url, self.cfg.buffer_dir, self.cfg.segment_seconds,
                                         streamlink_quality(self.cfg), hls,
                                         log_path=self.cfg.data_dir / "ffmpeg.log")
                self.recorder.start()
                self.live = True
            elif not live and self.live:
                log.info("⚫ Live encerrada")
                for m in self.detector.flush():
                    self.submit(m)
                if self.recorder:
                    self.recorder.stop()
                self.live = False
                manter_acordado(False)
            await asyncio.sleep(self.cfg.live_check_interval if not self.live else 15)

    async def _segment_loop(self) -> None:
        """Mede o áudio de cada segmento assim que ele fica pronto e limpa o buffer."""
        while True:
            if self.recorder:
                await asyncio.to_thread(self.recorder.collect)
            for seg in list_segments(self.cfg.buffer_dir, self.cfg.segment_seconds, include_last=True):
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
                    log.info("⚡ Momento detectado: %s score %.1f (%.0fs)", m.category, m.peak_score,
                             m.duration)
                    self.submit(m)
            await asyncio.sleep(1)

    async def _clip_worker(self) -> None:
        while True:
            clip_id, m = await self.queue.get()
            # espera o vídeo até o fim do corte estar gravado
            while self.live and time.time() < m.clip_end + self.cfg.segment_seconds + 4:
                await asyncio.sleep(2)
            await asyncio.to_thread(self.processor.process, m, clip_id)


# ---------------------------------------------------------------------------
# Modo VOD: processa um vídeo já gravado + chat exportado
# ---------------------------------------------------------------------------

def load_chat_file(path: Path, base_epoch: float, vod_start: float | None = None) -> list[ChatMessage]:
    """Lê chat exportado. Formatos aceitos:

    * TwitchDownloader: {"comments": [{"content_offset_seconds", "commenter", "message"}]}
    * Kick: lista (ou {"data"/"messages": [...]}) de mensagens {"created_at", "content", "sender"};
      `vod_start` (epoch) diz quando o vídeo começou; sem ele, usa a primeira mensagem.
    * JSONL simples: {"t": segundos_desde_o_inicio, "user": ..., "text": ...} por linha
    """
    from . import kick

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
    elif isinstance(data, (list, dict)):
        items = data if isinstance(data, list) else (data.get("data") or data.get("messages") or [])
        if isinstance(items, dict):  # {"data": {"messages": [...]}}
            items = items.get("messages") or []
        parsed = []
        for it in items:
            m = kick.parse_chat_event(json.dumps({"event": kick.CHAT_EVENT, "data": it}))
            if m:
                parsed.append(m)
        if parsed:
            start = vod_start if vod_start is not None else min(m.ts for m in parsed)
            for m in parsed:
                m.ts = base_epoch + (m.ts - start)
            msgs = [m for m in parsed if m.ts >= base_epoch]
    else:
        for line in text.splitlines():
            if line.strip():
                d = json.loads(line)
                msgs.append(ChatMessage(base_epoch + float(d["t"]), d.get("user", "?"), d.get("text", "")))
    msgs.sort(key=lambda m: m.ts)
    return msgs


def process_vod(cfg: Config, video: Path, chat_file: Path | None = None,
                vod_start: float | None = None) -> list[int]:
    cfg.ensure_dirs()
    work = cfg.data_dir / "vod_buffer"
    shutil.rmtree(work, ignore_errors=True)
    base = 1_000_000_000  # epoch artificial: segundo 0 do VOD
    vcfg = cfg.replace(buffer_override=work)

    msgs = load_chat_file(chat_file, base, vod_start) if chat_file else []
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
        cid = proc.process(m)
        if cid and (proc.db.get(cid) or {}).get("status") != "erro":
            ids.append(cid)
    shutil.rmtree(work, ignore_errors=True)
    if ids and site.carregar(cfg).get("codigo"):
        up = site.SiteUploader(cfg, proc.db)
        while up.sync():
            pass
    return ids
