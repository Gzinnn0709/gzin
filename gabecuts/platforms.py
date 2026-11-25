"""Camada única para Twitch e Kick: status da live, origem do vídeo e chat."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import AsyncIterator

from . import kick
from .chat import ChatMessage
from .chat import read_chat as read_twitch_chat
from .config import Config

log = logging.getLogger(__name__)


@dataclass
class LiveStatus:
    live: bool
    page_url: str            # URL da live (usada pelo streamlink)
    hls_url: str | None = None  # quando conhecida, o ffmpeg lê direto sem streamlink


def page_url(platform: str, channel: str) -> str:
    return f"https://kick.com/{channel}" if platform == "kick" else f"https://twitch.tv/{channel}"


def check_live(cfg: Config) -> LiveStatus:
    platform = cfg.platform
    channel = cfg.channel_for(platform)
    url = page_url(platform, channel)
    if platform == "kick":
        try:
            live, hls = kick.is_live(channel)
        except LookupError as e:
            log.error("%s", e)
            return LiveStatus(False, url)
        return LiveStatus(live, url, hls)
    try:
        import streamlink
        session = streamlink.Streamlink()
        session.set_option("twitch-disable-ads", True)
        return LiveStatus(bool(session.streams(url)), url)
    except Exception as e:  # noqa: BLE001
        log.debug("Falha ao checar live: %s", e)
        return LiveStatus(False, url)


def _chat_for(cfg: Config, platform: str) -> AsyncIterator[ChatMessage]:
    channel = cfg.channel_for(platform)
    if platform == "kick":
        return kick.read_chat(channel, cfg.kick_chatroom_id or None, cfg.kick_pusher_key or None)
    return read_twitch_chat(channel)


async def read_all_chats(cfg: Config) -> AsyncIterator[ChatMessage]:
    """Junta os chats configurados num fluxo só (ex.: multistream Twitch + Kick)."""
    sources = cfg.chats
    if len(sources) == 1:
        async for m in _chat_for(cfg, sources[0]):
            yield m
        return

    queue: asyncio.Queue[ChatMessage] = asyncio.Queue(maxsize=10_000)

    async def pump(platform: str) -> None:
        try:
            async for m in _chat_for(cfg, platform):
                # mesmo nick em plataformas diferentes = pessoas diferentes
                m.user = f"{platform[0]}:{m.user}"
                await queue.put(m)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Chat da %s parou; seguindo só com os outros chats", platform)

    tasks = [asyncio.create_task(pump(p)) for p in sources]
    try:
        while True:
            yield await queue.get()
    finally:
        for t in tasks:
            t.cancel()
