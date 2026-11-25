import asyncio
import contextlib
import json

import pytest

from gabecuts import kick
from gabecuts.config import Config, load_config
from gabecuts.pipeline import load_chat_file


def _event(content, user="gabefã", created="2026-11-25T23:00:05+00:00"):
    data = {"id": "x", "chatroom_id": 1, "content": content, "type": "message",
            "created_at": created, "sender": {"id": 9, "username": user, "slug": user.lower()}}
    return json.dumps({"event": kick.CHAT_EVENT, "data": json.dumps(data), "channel": "chatrooms.1.v2"})


def test_parse_chat_event_with_emotes():
    m = kick.parse_chat_event(_event("[emote:37226:KEKW] [emote:37226:KEKW] kkkk"))
    assert m.user == "gabefã"
    assert m.text == "KEKW KEKW kkkk"
    assert m.emotes == ["KEKW", "KEKW"]
    from datetime import datetime, timezone
    assert m.ts == datetime(2026, 11, 25, 23, 0, 5, tzinfo=timezone.utc).timestamp()


def test_parse_ignores_other_events():
    assert kick.parse_chat_event(json.dumps({"event": "pusher:connection_established", "data": "{}"})) is None
    assert kick.parse_chat_event("lixo") is None


def test_parse_channel_info():
    live = kick.parse_channel_info("gabepeixe", {"id": 10, "chatroom": {"id": 20},
                                                 "playback_url": "https://x/m.m3u8",
                                                 "livestream": {"is_live": True}})
    assert (live.channel_id, live.chatroom_id, live.live, live.playback_url) == (10, 20, True, "https://x/m.m3u8")
    off = kick.parse_channel_info("gabepeixe", {"id": 10, "chatroom": {"id": 20},
                                                "playback_url": "https://x/m.m3u8", "livestream": None})
    assert not off.live and off.playback_url is None


def test_read_kick_chat_over_fake_pusher(monkeypatch):
    import websockets
    sent = []
    frames = [json.dumps({"event": "pusher:connection_established", "data": "{}"}),
              json.dumps({"event": "pusher:ping", "data": {}}),
              _event("KEKW", "ana"), _event("pog", "bia")]

    class FakeWS:
        async def send(self, m):
            sent.append(json.loads(m))

        async def recv(self):
            if frames:
                return frames.pop(0)
            await asyncio.sleep(3600)

    @contextlib.asynccontextmanager
    async def fake_connect(url, **k):
        assert "ws-us2.pusher.com/app/chave" in url
        yield FakeWS()

    monkeypatch.setattr(websockets, "connect", fake_connect)

    async def run():
        out = []
        async for m in kick.read_chat("gabepeixe", chatroom_id=123, pusher_key="chave", pusher_cluster="us2"):
            out.append(m)
            if len(out) == 2:
                return out

    msgs = asyncio.run(run())
    assert [m.user for m in msgs] == ["ana", "bia"]
    assert sent[0] == {"event": "pusher:subscribe", "data": {"auth": "", "channel": "chatrooms.123.v2"}}
    assert {"event": "pusher:pong", "data": {}} in sent


def test_multi_chat_merge(monkeypatch):
    from gabecuts import platforms
    from gabecuts.chat import ChatMessage

    async def fake(cfg, platform):
        for i in range(2):
            yield ChatMessage(float(i), "mesmo_nick", f"{platform} {i}")

    monkeypatch.setattr(platforms, "_chat_for", fake)
    cfg = Config(chat_sources="twitch,kick")

    async def run():
        out = []
        async for m in platforms.read_all_chats(cfg):
            out.append(m)
            if len(out) == 4:
                return out

    users = {m.user for m in asyncio.run(run())}
    assert users == {"t:mesmo_nick", "k:mesmo_nick"}


def test_config_platforms(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(platform="kick", chat_sources="kick, twitch", kick_channel="@GabePeixe")
    assert cfg.chats == ["kick", "twitch"]
    assert cfg.channel_for("kick") == "gabepeixe"
    with pytest.raises(ValueError):
        load_config(platform="youtube")


def test_load_kick_vod_chat(tmp_path):
    msgs = [{"content": "salve", "created_at": "2026-11-25T23:00:00Z", "sender": {"username": "a"}},
            {"content": "[emote:1:KEKW]", "created_at": "2026-11-25T23:01:30Z", "sender": {"username": "b"}}]
    f = tmp_path / "kick.json"
    f.write_text(json.dumps({"data": {"messages": msgs}}))
    out = load_chat_file(f, base_epoch=1000, vod_start=None)
    assert [(m.ts, m.user, m.text) for m in out] == [(1000.0, "a", "salve"), (1090.0, "b", "KEKW")]
