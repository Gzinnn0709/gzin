"""Cortes de live passada (VOD da Kick) baixando só os trechos dos momentos."""
import shutil
import subprocess
from datetime import datetime, timezone

import pytest

from gabecuts import kick, vod
from gabecuts.config import Config
from gabecuts.db import ClipDB

UUID = "0a1b2c3d-1111-2222-3333-444455556666"
INICIO = datetime(2026, 11, 25, 22, 0, 0, tzinfo=timezone.utc).timestamp()


def test_link_e_info():
    assert vod.parse_vod_url(f"https://kick.com/gabepeixe/videos/{UUID}?t=10") == UUID
    with pytest.raises(ValueError):
        vod.parse_vod_url("https://youtube.com/watch?v=x")
    info = vod.parse_vod_info(UUID, {"source": "https://x/master.m3u8", "livestream": {
        "start_time": "2026-11-25 22:00:00", "duration": 7_200_000, "session_title": "GTA RP",
        "channel": {"id": 63354224, "slug": "gabepeixe"}}})
    assert info.inicio == INICIO and info.duracao == 7200 and info.channel_id == 63354224


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg ausente")
def test_corta_vod_baixando_so_os_trechos(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # "VOD" de 10 min em disco, fingindo ser a playlist da Kick
    fonte = tmp_path / "vod.ts"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15:d=600",
                    "-f", "lavfi", "-i", "sine=d=600", "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
                    "-c:a", "aac", "-shortest", "-f", "mpegts", str(fonte)], check=True)
    iso = lambda s: datetime.fromtimestamp(INICIO + s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731

    def falso_get_json(url):
        if "/api/v1/video/" in url:
            return {"source": str(fonte), "livestream": {"start_time": "2026-11-25 22:00:00", "duration": 600_000,
                                                          "channel": {"id": 99, "slug": "gabepeixe"}}}
        if "/clips" in url:   # 3 espectadores clipando por volta dos 300 s
            return {"clips": [{"id": f"c{i}", "created_at": iso(300 + i * 4)} for i in range(3)]}
        if "/messages" in url:
            return {"data": {"messages": []}}  # replay do chat indisponível: só clipes
        raise AssertionError(url)

    baixados = []
    real = vod.baixar_trecho

    def baixar(fonte_, offset, dur, out):
        baixados.append((offset, dur))
        return real(fonte_, offset, dur, out)

    monkeypatch.setattr(kick, "_get_json", falso_get_json)
    monkeypatch.setattr(vod, "pick_variant", lambda u, h: u)
    monkeypatch.setattr(vod, "baixar_trecho", baixar)
    monkeypatch.setattr(vod.time, "sleep", lambda s: None)
    cfg = Config(data_dir=tmp_path / "data", ai_enabled=False, whisper_model="off", video_preset="ultrafast")
    cfg.ensure_dirs()
    db = ClipDB(cfg.db_path)
    etapas = []
    ids = vod.cortar_vod(cfg, f"https://kick.com/gabepeixe/videos/{UUID}", db, lambda e, f: etapas.append(e))
    assert len(ids) == 1
    c = db.get(ids[0])
    assert c["status"] == "pendente" and c["category"] == "clipado na kick"
    assert c["t_start"] - INICIO < 300 < c["t_end"] - INICIO
    assert 40 <= c["t_end"] - c["t_start"] <= 120
    # baixou só o trecho do momento, nunca a live inteira
    assert len(baixados) == 1 and baixados[0][1] < 150
    assert not (cfg.data_dir / "vod_trecho").exists()
    assert etapas[-1].startswith("pronto")
