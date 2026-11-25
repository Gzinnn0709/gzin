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
    # live calma: com limiar fixo não sai nada; o adaptativo pega o melhor trecho do período
    rng = random.Random(4)
    fixed = MomentDetector(Config(analysis_lag=0, target_clips_per_hour=0, threshold=4.0))
    adaptive = MomentDetector(Config(analysis_lag=0, target_clips_per_hour=12, threshold=4.0,
                                     threshold_floor=1.5))
    for det in (fixed, adaptive):
        _feed(det, 0, 1000, random.Random(4))
    fixed_got = fixed.tick(1000) + fixed.flush()
    got = adaptive.tick(1000) + adaptive.flush()
    assert fixed_got == []
    assert len(got) >= 1


def test_threshold_resets_after_moment():
    det = MomentDetector(Config(analysis_lag=0, target_clips_per_hour=12, threshold=3.0))
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
