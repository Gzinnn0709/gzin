"""Leitura do chat da Twitch via IRC anônimo (não precisa de token)."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

log = logging.getLogger(__name__)

IRC_WS_URL = "wss://irc-ws.chat.twitch.tv:443"


@dataclass
class ChatMessage:
    ts: float                 # epoch em segundos
    user: str
    text: str
    emotes: list[str] = field(default_factory=list)


def _unescape_tag(v: str) -> str:
    return (v.replace(r"\s", " ").replace(r"\:", ";").replace(r"\\", "\\")
             .replace(r"\r", "\r").replace(r"\n", "\n"))


def parse_irc_line(line: str, now: float | None = None) -> ChatMessage | None:
    """Converte uma linha IRC (com tags IRCv3) em ChatMessage. Retorna None se não for PRIVMSG."""
    tags: dict[str, str] = {}
    rest = line
    if rest.startswith("@"):
        raw_tags, _, rest = rest[1:].partition(" ")
        for kv in raw_tags.split(";"):
            k, _, v = kv.partition("=")
            tags[k] = _unescape_tag(v)
    if not rest.startswith(":"):
        return None
    prefix, _, rest = rest[1:].partition(" ")
    command, _, rest = rest.partition(" ")
    if command != "PRIVMSG":
        return None
    _channel, _, text = rest.partition(" :")
    user = tags.get("display-name") or prefix.split("!", 1)[0]

    emotes: list[str] = []
    # formato: emote_id:0-4,6-10/emote_id2:12-16
    if tags.get("emotes"):
        for part in tags["emotes"].split("/"):
            _eid, _, positions = part.partition(":")
            for pos in positions.split(","):
                try:
                    a, b = (int(x) for x in pos.split("-"))
                    emotes.append(text[a:b + 1])
                except ValueError:
                    continue

    ts = now if now is not None else time.time()
    if "tmi-sent-ts" in tags:
        try:
            ts = int(tags["tmi-sent-ts"]) / 1000.0
        except ValueError:
            pass
    return ChatMessage(ts=ts, user=user, text=text.rstrip("\r\n"), emotes=emotes)


async def read_chat(channel: str) -> AsyncIterator[ChatMessage]:
    """Gera mensagens do chat indefinidamente, reconectando sozinho em caso de queda.

    Usa o IRC da Twitch via WebSocket (porta 443), que passa pela maioria dos firewalls.
    """
    import websockets

    backoff = 1.0
    while True:
        try:
            async with websockets.connect(IRC_WS_URL, ping_interval=60, max_size=2**20) as ws:
                nick = f"justinfan{random.randint(10000, 99999)}"
                await ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                await ws.send("PASS SCHMOOPIIE")
                await ws.send(f"NICK {nick}")
                await ws.send(f"JOIN #{channel}")
                log.info("Conectado ao chat de #%s como %s", channel, nick)
                backoff = 1.0
                while True:
                    frame = await asyncio.wait_for(ws.recv(), timeout=400)
                    if isinstance(frame, bytes):
                        frame = frame.decode("utf-8", errors="replace")
                    for line in frame.split("\r\n"):
                        if not line:
                            continue
                        if line.startswith("PING"):
                            await ws.send(line.replace("PING", "PONG", 1))
                            continue
                        if " RECONNECT" in line:
                            raise ConnectionError("Twitch pediu reconexão")
                        msg = parse_irc_line(line)
                        if msg:
                            yield msg
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - qualquer erro de rede => reconecta
            log.warning("Chat desconectado (%s). Reconectando em %.0fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
