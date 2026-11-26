import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from gabecuts import ai, clipper
from gabecuts.transcribe import Line, to_srt


def test_wrap_title():
    assert clipper.wrap_title("curto") == ["curto"]
    lines = clipper.wrap_title("GABE PERDE A PACIÊNCIA com o chat e explode ao vivo de novo hoje")
    assert len(lines) == 2 and lines[1].endswith("…")


def test_ai_review_parses_structured_output(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "teste")
    payload = {"keep": True, "score": 87, "title": "Gabe TOMA SUSTO no meio da run",
               "description": "Susto clássico.", "hashtags": ["#Gabepeixe", "susto"],
               "category": "susto", "trim_start": 3.0, "trim_end": 58.0, "reason": "reação forte"}
    calls = {}

    class FakeMessages:
        def create(self, **kw):
            calls.update(kw)
            return SimpleNamespace(stop_reason="end_turn",
                                   content=[SimpleNamespace(type="text", text=json.dumps(payload))])

    class FakeClient:
        def __init__(self, *a, **k):
            self.beta = SimpleNamespace(messages=FakeMessages())

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    rv = ai.ai_review("claude-opus-5-5", 70.0, "[0.0s] oi", ["[1.0] a: KKKK"], {}, "engraçado", 20.0, 9.0)
    assert rv.by_ai and rv.score == 87 and rv.category == "susto"
    assert rv.hashtags == ["gabepeixe", "susto", "kick"]   # sempre kick, nunca twitch
    assert (rv.trim_start, rv.trim_end) == (3.0, 58.0)
    # corte curto demais (< 40 s) sugerido pela IA: mantém o trecho inteiro
    payload.update(trim_start=10.0, trim_end=30.0)
    rv2 = ai.ai_review("claude-opus-5-5", 70.0, "", [], {}, "hype", 20.0, 9.0)
    assert (rv2.trim_start, rv2.trim_end) == (0.0, 70.0)
    assert calls["model"] == "claude-opus-5-5"
    assert calls["output_config"]["format"]["type"] == "json_schema"


def test_ai_refusal_falls_back(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "teste")

    class FakeClient:
        def __init__(self, *a, **k):
            self.beta = SimpleNamespace(messages=SimpleNamespace(
                create=lambda **kw: SimpleNamespace(stop_reason="refusal", content=[])))

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    rv = ai.ai_review("claude-opus-5-5", 30.0, "", [], {}, "hype", 10.0, 6.0)
    assert not rv.by_ai and rv.keep


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg ausente")
def test_vertical_with_subtitles_and_tricky_title(tmp_path):
    src = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15:d=4",
                    "-f", "lavfi", "-i", "sine=d=4", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    srt = tmp_path / "legenda.srt"
    srt.write_text(to_srt([Line(0.0, 3.0, "isso aqui é uma legenda")]), encoding="utf-8")
    out = clipper.make_vertical(src, tmp_path / "v.mp4", srt, "Gabe: 'não acredito' 100% real",
                                preset="ultrafast")
    assert out.exists() and out.stat().st_size > 1000
