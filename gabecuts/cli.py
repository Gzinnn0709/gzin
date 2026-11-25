"""Linha de comando.

  gabecuts live            # monitora o canal, grava, detecta e corta (com painel web)
  gabecuts vod VIDEO [--chat chat.json]   # processa um VOD já baixado
  gabecuts web             # só o painel de revisão
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from .config import load_config


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="gabecuts", description="Cortes automáticos da live do Gabepeixe")
    ap.add_argument("--config", help="arquivo TOML (padrão: gabecuts.toml)")
    ap.add_argument("--channel", help="canal da Twitch (padrão: gabepeixe)")
    ap.add_argument("--data-dir", type=Path)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_live = sub.add_parser("live", help="modo ao vivo")
    p_live.add_argument("--no-web", action="store_true", help="não sobe o painel web")

    p_vod = sub.add_parser("vod", help="processa um vídeo local")
    p_vod.add_argument("video", type=Path)
    p_vod.add_argument("--chat", type=Path, help="chat exportado (TwitchDownloader JSON ou JSONL)")

    sub.add_parser("web", help="apenas o painel web")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    cfg = load_config(args.config, channel=args.channel, data_dir=args.data_dir)

    if args.cmd == "vod":
        from .pipeline import process_vod
        ids = process_vod(cfg, args.video, args.chat)
        print(f"{len(ids)} cortes gerados em {cfg.clips_dir}")
    elif args.cmd == "web":
        import uvicorn
        from .web import create_app
        uvicorn.run(create_app(cfg), host=cfg.web_host, port=cfg.web_port)
    else:
        asyncio.run(_live(cfg, web=not args.no_web))


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
