"""Suporte à Kick: status da live, URL do vídeo (HLS) e chat em tempo real (Pusher/WebSocket).

A API da Kick (kick.com/api/v2/...) fica atrás do Cloudflare. Tentamos primeiro uma requisição
normal com cabeçalhos de navegador. Se o Cloudflare bloquear, usamos o `curl_cffi`, que imita a
assinatura TLS do Chrome (instale com `pip install curl_cffi`, ou `pip install -e ".[kick]"`).
Se nada funcionar, configure `kick_chatroom_id` manualmente: abra
https://kick.com/api/v2/channels/<canal> no navegador e procure "chatroom": {"id": ...}.

O chat usa o Pusher público da Kick e não precisa de login nem de Cloudflare.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator

import httpx

from .chat import ChatMessage

log = logging.getLogger(__name__)

CHANNEL_API = "https://kick.com/api/v2/channels/{slug}"
REALTIME_CONFIG_URL = "https://web.kick.com/api/v1/realtime/channels/{channel_id}/chat/connection"
DEFAULT_PUSHER_KEY = "32cbd69e4b950bf97679"
DEFAULT_PUSHER_CLUSTER = "us2"
CHAT_EVENT = "App\\Events\\ChatMessageEvent"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0.0.0 Safari/537.36")
EMOTE_RE = re.compile(r"\[emote:(\d+):([^\]]+)\]")


@dataclass
class KickChannel:
    slug: str
    channel_id: int | None
    chatroom_id: int | None
    live: bool
    playback_url: str | None   # HLS (.m3u8) da live, quando disponível


class KickBlocked(RuntimeError):
    """A API da Kick recusou o acesso (Cloudflare)."""


def _get_json(url: str) -> dict:
    headers = {"User-Agent": BROWSER_UA, "Accept": "application/json",
               "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8", "Referer": "https://kick.com/"}
    try:
        r = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        if r.status_code == 404:
            raise LookupError(f"canal não encontrado na Kick: {url}")
        log.debug("Kick API respondeu %s; tentando curl_cffi", r.status_code)
    except httpx.HTTPError as e:
        log.debug("Kick API falhou via httpx: %s", e)
    try:
        from curl_cffi import requests as creq
    except ImportError as e:
        raise KickBlocked("a API da Kick bloqueou a requisição (Cloudflare). Instale `curl_cffi` "
                          "ou defina kick_chatroom_id no gabecuts.toml") from e
    r = creq.get(url, impersonate="chrome", timeout=20)
    if r.status_code == 404:
        raise LookupError(f"canal não encontrado na Kick: {url}")
    if r.status_code != 200:
        raise KickBlocked(f"a API da Kick respondeu {r.status_code} mesmo com curl_cffi")
    return r.json()


def channel_info(slug: str) -> KickChannel:
    data = _get_json(CHANNEL_API.format(slug=slug))
    return parse_channel_info(slug, data)


def parse_channel_info(slug: str, data: dict) -> KickChannel:
    ls = data.get("livestream")
    live = bool(ls) and bool(ls.get("is_live", True))
    return KickChannel(
        slug=slug,
        channel_id=data.get("id"),
        chatroom_id=(data.get("chatroom") or {}).get("id"),
        live=live,
        playback_url=data.get("playback_url") if live else None,
    )


def is_live(slug: str) -> tuple[bool, str | None]:
    """(ao vivo?, URL HLS). Se a API estiver bloqueada, cai no plugin de Kick do streamlink."""
    try:
        info = channel_info(slug)
        return info.live, info.playback_url
    except LookupError:
        raise
    except Exception as e:  # noqa: BLE001
        log.debug("Status via API da Kick falhou (%s); tentando streamlink", e)
    try:
        import streamlink
        return bool(streamlink.Streamlink().streams(f"https://kick.com/{slug}")), None
    except Exception as e:  # noqa: BLE001
        log.debug("streamlink também falhou para a Kick: %s", e)
        return False, None


CLIPS_API = "https://kick.com/api/v2/channels/{slug}/clips?cursor=0&sort=date&time=day"


def parse_clips(data) -> list[dict]:
    """Clipes do canal: [{id, ts (epoch de quando foi criado), duracao, views, titulo}]."""
    if isinstance(data, dict):
        itens = data.get("clips") or data.get("data") or []
    else:
        itens = data or []
    out = []
    for c in itens if isinstance(itens, list) else []:
        cid, criado = c.get("id"), c.get("created_at")
        if not cid or not criado:
            continue
        try:
            ts = datetime.fromisoformat(str(criado).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        out.append({"id": str(cid), "ts": ts, "duracao": c.get("duration"), "views": c.get("views"),
                    "titulo": c.get("title") or ""})
    return out


def recent_clips(slug: str) -> list[dict]:
    """Clipes mais recentes que os espectadores criaram no canal (mais novos primeiro)."""
    return parse_clips(_get_json(CLIPS_API.format(slug=slug)))


def pusher_config(channel_id: int | None) -> tuple[str, str]:
    """Pega a chave/cluster do Pusher que o site da Kick está usando; usa o padrão se falhar."""
    if channel_id:
        try:
            r = httpx.post(REALTIME_CONFIG_URL.format(channel_id=channel_id), timeout=10,
                           headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
                           json={"client": {"id": str(uuid.uuid4()), "type": "web"},
                                 "capabilities": {"accepted_providers": [{"provider": "pusher"}]}})
            if r.status_code == 200:
                for conn in (r.json().get("data") or {}).get("connections") or []:
                    creds = conn.get("credentials") or {}
                    if conn.get("provider") == "pusher" and creds.get("app_key"):
                        return str(creds["app_key"]), str(creds.get("cluster") or DEFAULT_PUSHER_CLUSTER)
        except (httpx.HTTPError, ValueError) as e:
            log.debug("Config do Pusher indisponível: %s", e)
    return DEFAULT_PUSHER_KEY, DEFAULT_PUSHER_CLUSTER


def pusher_url(key: str, cluster: str) -> str:
    return f"wss://ws-{cluster}.pusher.com/app/{key}?protocol=7&client=js&version=8.4.0&flash=false"


def parse_chat_event(raw: str) -> ChatMessage | None:
    """Converte um frame do Pusher em ChatMessage (ou None se não for mensagem de chat)."""
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if frame.get("event") != CHAT_EVENT:
        return None
    data = frame.get("data")
    if isinstance(data, str):  # o Pusher manda o payload como string JSON
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    content = data.get("content") or ""
    emotes = [name for _id, name in EMOTE_RE.findall(content)]
    # [emote:37226:KEKW] -> KEKW, para os detectores de risada/hype enxergarem o emote
    text = EMOTE_RE.sub(lambda m: m.group(2), content).strip()
    sender = data.get("sender") or {}
    ts = None
    if data.get("created_at"):
        try:
            ts = datetime.fromisoformat(str(data["created_at"]).replace("Z", "+00:00")).timestamp()
        except ValueError:
            ts = None
    return ChatMessage(ts=ts or time.time(), user=sender.get("username") or sender.get("slug") or "?",
                       text=text, emotes=emotes)


async def read_chat(slug: str, chatroom_id: int | None = None,
                    pusher_key: str | None = None, pusher_cluster: str | None = None
                    ) -> AsyncIterator[ChatMessage]:
    """Gera mensagens do chat da Kick indefinidamente, reconectando em caso de queda."""
    import websockets

    channel_id = None
    backoff = 2.0
    while chatroom_id is None:
        try:
            info = await asyncio.to_thread(channel_info, slug)
            chatroom_id, channel_id = info.chatroom_id, info.channel_id
        except LookupError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("Não consegui descobrir o chatroom da Kick (%s). Tentando de novo em %.0fs",
                        e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)

    if not pusher_key:
        pusher_key, pusher_cluster = await asyncio.to_thread(pusher_config, channel_id)
    url = pusher_url(pusher_key, pusher_cluster or DEFAULT_PUSHER_CLUSTER)

    backoff = 1.0
    while True:
        try:
            async with websockets.connect(url, ping_interval=None, max_size=2**20,
                                          user_agent_header=BROWSER_UA,
                                          origin="https://kick.com") as ws:
                await ws.send(json.dumps({"event": "pusher:subscribe",
                                          "data": {"auth": "", "channel": f"chatrooms.{chatroom_id}.v2"}}))
                log.info("Conectado ao chat da Kick de %s (chatroom %s)", slug, chatroom_id)
                backoff = 1.0
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=120)
                    except asyncio.TimeoutError:
                        await ws.send(json.dumps({"event": "pusher:ping", "data": {}}))
                        continue
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    if '"pusher:ping"' in raw:
                        await ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
                        continue
                    if '"pusher:error"' in raw:
                        log.warning("Erro do Pusher da Kick: %s", raw[:200])
                        continue
                    msg = parse_chat_event(raw)
                    if msg:
                        yield msg
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("Chat da Kick desconectado (%s). Reconectando em %.0fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
