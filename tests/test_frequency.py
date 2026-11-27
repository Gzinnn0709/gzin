import random

from gabecuts.chat import ChatMessage
from gabecuts.config import Config
from gabecuts.detector import MomentDetector
from gabecuts.recorder import pick_variant, streamlink_quality


def _feed(det, t0, t1, rng, small_bump_at=()):
    for t in range(t0, t1):
        n = rng.randint(1, 3)
        bump = any(b <= t < b + 5 for b in small_bump_at)
        for _ in range(n + (1 if bump else 0)):
            det.add_message(ChatMessage(t + rng.random(), f"u{rng.randint(0, 3000)}",
                                        "kkkk" if bump else "salve"))


def test_adaptive_threshold_finds_moment_in_calm_live():
    # live calma: conversa normal NUNCA vira corte; uma risadinha que não passa do limiar fixo
    # vira corte depois de um tempo sem nada (o limiar adaptativo pega o melhor do período)
    cfgs = dict(analysis_lag=0, threshold=4.5, threshold_floor=1.5)

    def rodar(target, com_risada):
        det = MomentDetector(Config(target_clips_per_hour=target, **cfgs))
        rng = random.Random(4)
        for t in range(0, 1300):
            for _ in range(rng.randint(1, 3)):
                det.add_message(ChatMessage(t + rng.random(), f"u{rng.randint(0, 3000)}", "salve"))
            if com_risada and 1000 <= t < 1004:
                for i in range(3):
                    det.add_message(ChatMessage(t + i / 3, f"r{t}_{i}", "kkkkk"))
        return det.tick(1300) + det.flush()

    assert rodar(12, com_risada=False) == []          # só conversa: nada
    got = rodar(12, com_risada=True)                  # risada de verdade: vira corte
    assert len(got) == 1 and got[0].clip_start < 1000 < got[0].clip_end


def test_threshold_resets_after_moment():
    det = MomentDetector(Config(analysis_lag=0, target_clips_per_hour=12, threshold=3.0, threshold_floor=1.8))
    det.first_t = 0
    assert det.current_threshold(100) == 3.0
    assert det.current_threshold(60 + 300 + 150) < 3.0
    det.last_moment_t = 500
    assert det.current_threshold(510) == 3.0


def test_pick_variant_limits_resolution():
    master = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080
https://cdn/1080.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720
720/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=640x360
360/index.m3u8
"""
    assert pick_variant("https://h/m/master.m3u8?token=abc", 720, master) == \
        "https://h/m/720/index.m3u8?token=abc"
    assert pick_variant("https://h/m/master.m3u8", 1080, master) == "https://cdn/1080.m3u8"
    assert pick_variant("https://h/m/master.m3u8", 240, master) == "https://h/m/360/index.m3u8"
    assert pick_variant("https://h/x.m3u8", 720, "#EXTM3U\n#EXTINF:2,\nseg.ts\n") == "https://h/x.m3u8"
    assert streamlink_quality(Config()).startswith("720p60,720p,")
    assert streamlink_quality(Config(stream_quality="best")) == "best"


def test_curadoria_fica_com_os_melhores_de_cada_janela():
    from gabecuts.detector import Curador, Moment

    def m(t, score, kick=0):
        return Moment(t, t + 5, t + 2, score, t - 30, t + 30, "hype", {"kick_clips": kick})

    c = Curador(janela_min=10, por_janela=2)
    for x in (m(30, 5), m(200, 9), m(400, 6), m(500, 4.6, kick=3), m(700, 7)):
        assert c.adicionar(x) == []
    assert c.prontos(600 + 30) == []                     # janela 0-600 s ainda na folga
    saiu = c.prontos(600 + 61)
    # 2 melhores (9 e 6)... e o clipado por 3+ na Kick passa sempre
    assert sorted(x.peak_score for x in saiu) == [4.6, 9]
    assert c.descartados == 2
    assert [x.peak_score for x in c.flush()] == [7]
