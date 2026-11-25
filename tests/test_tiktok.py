"""Estilo TikTok: legenda palavra por palavra, ritmo (sem silêncios), zoom, gancho e música."""
import shutil
import subprocess

import pytest

from gabecuts import ai, clipper
from gabecuts.pipeline import escolher_musica
from gabecuts.transcribe import Line, Word, remap, to_ass

FF = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg ausente")


def _linhas():
    return [Line(0.5, 4.4, "olha isso aqui que absurdo",
                 [Word(0.5, 1.0, "olha"), Word(1.0, 1.5, "isso"), Word(1.5, 2.0, "aqui"),
                  Word(3.0, 3.5, "que"), Word(3.5, 4.4, "absurdo!")])]


def test_legenda_palavra_por_palavra():
    eventos = [l for l in to_ass(_linhas()).splitlines() if l.startswith("Dialogue")]
    assert len(eventos) == 5                          # um evento por palavra
    assert all("Arial" not in e for e in eventos)
    assert "{\\c&H0000E5FF&}OLHA" in eventos[0]       # palavra falada em amarelo
    assert "ISSO" in eventos[0] and "QUE" not in eventos[0]  # no máximo 3 palavras por bloco
    # blocos nunca se sobrepõem
    fins = [e.split(",")[2] for e in eventos]
    inicios = [e.split(",")[1] for e in eventos]
    assert all(fins[i] <= inicios[i + 1] for i in range(len(eventos) - 1))


def test_legenda_sem_tempo_por_palavra_estima():
    eventos = [l for l in to_ass([Line(0, 2, "uma frase qualquer aqui")]).splitlines() if l.startswith("Dialogue")]
    assert len(eventos) == 4


def test_tira_so_silencio_sem_fala():
    niveis = [-20.0] * 30 + [-45.0] * 20 + [-20.0] * 30        # 8 s, silêncio de 3 a 5 s
    keep = clipper.keep_intervals(niveis, [Word(0.5, 1.0, "oi")], 8.0)
    assert keep == [(0.0, 3.2), (4.8, 8.0)]
    # se alguém fala no "silêncio", não corta
    assert clipper.keep_intervals(niveis, [Word(3.5, 4.5, "psiu")], 8.0) == [(0.0, 8.0)]
    # nunca corta demais
    assert clipper.keep_intervals([-20.0] * 10 + [-60.0] * 70, [], 8.0) == [(0.0, 8.0)]
    f = clipper.time_map(keep)
    assert f(1.0) == 1.0 and f(4.0) is None and f(6.0) == pytest.approx(4.4)
    fala = [Line(0.5, 6.0, "x", [Word(0.5, 1.0, "antes"), Word(5.0, 6.0, "depois")])]
    novas = remap(fala, f, 6.4)
    assert [w.text for w in novas[0].words] == ["antes", "depois"]
    assert novas[0].words[1].start == pytest.approx(3.4)   # 5.0 - 1.6 s de silêncio tirado


def test_gancho_heuristico_e_musica(tmp_path):
    assert ai.heuristic_review(30, "engraçado", {}, 5).gancho == "O CHAT NÃO AGUENTOU"
    assert escolher_musica("", "hype") is None
    (tmp_path / "hype").mkdir()
    (tmp_path / "hype" / "a.mp3").write_bytes(b"x")
    (tmp_path / "geral.mp3").write_bytes(b"x")
    assert escolher_musica(str(tmp_path), "hype").name == "a.mp3"
    assert escolher_musica(str(tmp_path), "susto").name == "geral.mp3"


@FF
def test_render_completo(tmp_path):
    src, mus = tmp_path / "in.mp4", tmp_path / "m.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:d=6",
                    "-f", "lavfi", "-i", "sine=d=6", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-shortest", str(src)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=600:d=2", str(mus)], check=True)
    ass = tmp_path / "legenda.ass"
    ass.write_text(to_ass(_linhas()), encoding="utf-8")
    out = clipper.make_vertical(src, tmp_path / "v.mp4", ass, "ELE NÃO ESPERAVA: 100% 'REAL'",
                                preset="ultrafast", zoom_at=2.0, music=mus)
    info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                           "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout
    assert "1080,1920" in info and "audio" in info
    # a música em loop não estica o vídeo
    dur = float([l for l in info.splitlines() if l and l[0].isdigit() and "," not in l][0])
    assert 5.5 < dur < 6.5
    keep = [(0.0, 2.0), (3.0, 6.0)]
    curto = clipper.cut_intervals(src, tmp_path / "c.mp4", keep, preset="ultrafast")
    d2 = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                               str(curto)], capture_output=True, text=True).stdout)
    assert 4.7 < d2 < 5.3
