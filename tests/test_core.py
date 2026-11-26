import random

from gabecuts.chat import ChatMessage, parse_irc_line
from gabecuts.config import Config
from gabecuts.detector import MomentDetector
from gabecuts.recorder import Segment, segments_for_range
from gabecuts.signals import classify_message
from gabecuts.transcribe import Line, shift, to_srt


def test_parse_irc_privmsg_with_tags():
    line = ("@badge-info=;display-name=Fulano;emotes=25:0-4;tmi-sent-ts=1700000000123 "
            ":fulano!fulano@fulano.tmi.twitch.tv PRIVMSG #gabepeixe :Kappa kkkkkkk")
    m = parse_irc_line(line)
    assert m.user == "Fulano"
    assert m.text == "Kappa kkkkkkk"
    assert m.emotes == ["Kappa"]
    assert abs(m.ts - 1700000000.123) < 1e-6


def test_parse_ignores_non_privmsg():
    assert parse_irc_line(":tmi.twitch.tv 001 justinfan123 :Welcome") is None


def test_classify():
    assert classify_message("KKKKKKKKKK")[0]
    assert classify_message("KEKW")[0]
    assert classify_message("MEU DEUS QUE ISSO")[1]
    assert classify_message("clipa isso")[2]
    assert classify_message("boa tarde chat") == (False, False, False) or classify_message("boa tarde chat")[1]
    assert classify_message("qual o jogo hoje?") == (False, False, False)


def _cfg(**kw):
    return Config(analysis_lag=0, warmup_seconds=60, **kw)


def _feed_normal(det, t0, t1, rate=2, rng=None):
    rng = rng or random.Random(1)
    chatter = ["qual jogo", "salve gabe", "bom dia", "que horas acaba", "lol"]
    for t in range(t0, t1):
        for _ in range(rng.randint(max(rate - 1, 0), rate + 1)):
            det.add_message(ChatMessage(t + rng.random(), f"u{rng.randint(1, 400)}", rng.choice(chatter)))


def test_detects_laugh_burst_with_context():
    det = MomentDetector(_cfg())
    _feed_normal(det, 0, 300)
    # explosão de risada entre 300 e 310
    for t in range(300, 310):
        for i in range(25):
            det.add_message(ChatMessage(t + i / 25, f"r{i}_{t}", "KKKKKKKKKKKKK"))
    _feed_normal(det, 310, 400, rng=random.Random(2))
    moments = det.tick(400) + det.flush()
    assert len(moments) == 1
    m = moments[0]
    assert m.category == "engraçado"
    # o corte começa antes da reação do chat e cobre o pico
    assert m.clip_start < 300 - Config().chat_delay
    assert m.clip_end > 300
    assert 15 <= m.duration <= 60


def test_single_spammer_does_not_trigger():
    det = MomentDetector(_cfg())
    _feed_normal(det, 0, 300)
    for t in range(300, 310):
        for i in range(25):
            det.add_message(ChatMessage(t + i / 25, "spammer", "KKKKKKKK"))
    _feed_normal(det, 310, 400, rng=random.Random(3))
    assert det.tick(400) + det.flush() == []


def test_audio_spike_alone_can_trigger_when_weighted():
    det = MomentDetector(_cfg(w_audio=1.6, threshold=3.5))
    det.add_audio(0, [-35.0 + (i % 3) for i in range(400)])
    det.add_audio(200, [-5.0] * 6)
    moments = det.tick(400) + det.flush()
    assert len(moments) == 1
    assert moments[0].clip_start < 200 < moments[0].clip_end


def test_no_trigger_during_warmup():
    det = MomentDetector(_cfg())
    for t in range(0, 10):
        for i in range(30):
            det.add_message(ChatMessage(t, f"u{i}", "KKKKKK"))
    assert det.tick(100) + det.flush() == []


def test_segments_for_range():
    segs = [Segment(None, float(s), 10.0) for s in range(0, 100, 10)]
    got = segments_for_range(segs, 25, 41)
    assert [s.start for s in got] == [20.0, 30.0, 40.0]


def test_srt_and_shift():
    lines = [Line(1.0, 3.0, "olha isso"), Line(10.0, 12.0, "fora do corte")]
    sh = shift(lines, 0.5, 5.0)
    assert len(sh) == 1 and sh[0].start == 0.5
    srt = to_srt(sh)
    assert "OLHA ISSO" in srt and "00:00:00,500 --> 00:00:02,500" in srt


def test_read_chat_over_fake_websocket(monkeypatch):
    import asyncio
    import contextlib

    import websockets

    from gabecuts.chat import read_chat

    sent = []
    frames = ["PING :tmi.twitch.tv",
              "@display-name=Ana;tmi-sent-ts=1700000000000 :ana!ana@ana.tmi.twitch.tv PRIVMSG #gabepeixe :KKKK\r\n"
              "@display-name=Bia;tmi-sent-ts=1700000001000 :bia!bia@bia.tmi.twitch.tv PRIVMSG #gabepeixe :pog"]

    class FakeWS:
        async def send(self, m):
            sent.append(m)

        async def recv(self):
            if frames:
                return frames.pop(0)
            await asyncio.sleep(3600)

    @contextlib.asynccontextmanager
    async def fake_connect(*a, **k):
        yield FakeWS()

    monkeypatch.setattr(websockets, "connect", fake_connect)

    async def run():
        out = []
        async for m in read_chat("gabepeixe"):
            out.append(m)
            if len(out) == 2:
                return out

    msgs = asyncio.run(run())
    assert [m.user for m in msgs] == ["Ana", "Bia"]
    assert "JOIN #gabepeixe" in sent and "PONG :tmi.twitch.tv" in sent


def test_parte_com_erro_reinicia_sozinha(monkeypatch, tmp_path):
    import asyncio

    from gabecuts import pipeline
    runner = pipeline.LiveRunner(Config(data_dir=tmp_path))
    chamadas = []

    async def falha_uma_vez():
        chamadas.append(1)
        if len(chamadas) == 1:
            raise RuntimeError("ffmpeg sumiu")
        raise asyncio.CancelledError

    async def rapido(_):
        return None

    monkeypatch.setattr(pipeline.asyncio, "sleep", rapido)
    try:
        asyncio.run(runner._supervisionar("teste", falha_uma_vez))
    except asyncio.CancelledError:
        pass
    assert len(chamadas) == 2  # deu erro, reiniciou, e só parou quando foi cancelado
