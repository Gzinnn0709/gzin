"""Linha de comando.

  gabecuts live            # monitora o canal, grava, detecta e corta (com painel web)
  gabecuts vod VIDEO [--chat chat.json]   # processa um VOD já baixado
  gabecuts web             # só o painel de revisão
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from .config import load_config


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="gabecuts", description="Cortes automáticos da live do Gabepeixe")
    ap.add_argument("--config", help="arquivo TOML (padrão: gabecuts.toml)")
    ap.add_argument("--channel", help="canal (padrão: gabepeixe)")
    ap.add_argument("--platform", choices=["twitch", "kick"], help="de onde vem o vídeo (padrão: twitch)")
    ap.add_argument("--chat-sources", help='chats lidos, ex: "twitch,kick" em multistream')
    ap.add_argument("--data-dir", type=Path)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_live = sub.add_parser("live", help="modo ao vivo")
    p_live.add_argument("--no-web", action="store_true", help="não sobe o painel web")

    p_vod = sub.add_parser("vod", help="processa um vídeo local")
    p_vod.add_argument("video", type=Path)
    p_vod.add_argument("--chat", type=Path, help="chat exportado (TwitchDownloader JSON, Kick JSON ou JSONL)")

    p_vod.add_argument("--vod-start", help="início do vídeo (ISO, ex: 2026-11-25T20:00:00-03:00); "
                       "alinha chats da Kick que usam horário absoluto")

    sub.add_parser("web", help="apenas o painel web")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    cfg = load_config(args.config, channel=args.channel, data_dir=args.data_dir,
                      platform=args.platform, chat_sources=args.chat_sources)
    cfg.ensure_dirs()
    # log também em arquivo (útil rodando minimizado / em segundo plano)
    from logging.handlers import RotatingFileHandler
    fh = RotatingFileHandler(cfg.data_dir / "gabecuts.log", maxBytes=5_000_000, backupCount=2,
                             encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    logging.getLogger().addHandler(fh)
    for noisy in ("httpx", "httpcore", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    from . import clipper
    clipper.THREADS = cfg.encode_threads
    if cfg.low_priority and args.cmd in ("live", "vod"):
        lower_priority()

    if args.cmd == "vod":
        from .pipeline import process_vod
        start = None
        if args.vod_start:
            from datetime import datetime
            start = datetime.fromisoformat(args.vod_start.replace("Z", "+00:00")).timestamp()
        ids = process_vod(cfg, args.video, args.chat, start)
        print(f"{len(ids)} cortes gerados em {cfg.clips_dir}")
    elif args.cmd == "web":
        import uvicorn
        from .web import create_app
        uvicorn.run(create_app(cfg), host=cfg.web_host, port=cfg.web_port)
    else:
        asyncio.run(_live(cfg, web=not args.no_web))


def lower_priority() -> None:
    """Prioridade baixa para este processo e para os ffmpeg/streamlink que ele abrir.

    O Windows/Linux passam a dar preferência ao navegador: a live não trava e os pontos
    continuam contando, enquanto os cortes são feitos com a CPU que sobrar.
    """
    try:
        if os.name == "nt":
            import ctypes
            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(10)
        logging.getLogger(__name__).info("Rodando com prioridade baixa (a live no navegador vem primeiro)")
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).debug("Não consegui baixar a prioridade: %s", e)


async def _live(cfg, web: bool) -> None:
    from .pipeline import LiveRunner
    runner = LiveRunner(cfg)
    tasks = [runner.run()]
    if web:
        import uvicorn
        from .web import create_app
        server = uvicorn.Server(uvicorn.Config(create_app(cfg, runner), host=cfg.web_host,
                                               port=cfg.web_port, log_level="warning"))
        tasks.append(server.serve())
        logging.getLogger(__name__).info("Painel em http://%s:%d", cfg.web_host, cfg.web_port)
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    main()
