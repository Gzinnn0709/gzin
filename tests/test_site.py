"""Envio dos cortes para a aba Cortes do site, contra um 'site' falso com as mesmas rotas."""
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from gabecuts import site
from gabecuts.config import Config
from gabecuts.db import ClipDB
from gabecuts.web import create_app

CODIGO = "gc_teste123"


@pytest.fixture()
def site_falso():
    recebido = {"arquivos": {}, "registros": {}}
    app = FastAPI()

    def auth(req: Request):
        if req.headers.get("authorization") != f"Bearer {CODIGO}":
            raise HTTPException(401, detail="x")

    @app.get("/api/cortes/ping")
    def ping(request: Request):
        auth(request)
        return {"sucesso": True}

    @app.put("/api/cortes/{cid}/arquivo/{nome}")
    async def arquivo(cid: str, nome: str, request: Request):
        auth(request)
        recebido["arquivos"][f"{cid}/{nome}"] = await request.body()
        return {"sucesso": True}

    @app.post("/api/cortes/{cid}")
    async def registrar(cid: str, request: Request):
        auth(request)
        recebido["registros"][cid] = await request.json()
        return {"sucesso": True}

    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True); t.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", recebido
    server.should_exit = True
    t.join(5)


def _clip(cfg, db, status="pendente"):
    pasta = cfg.clips_dir / "20261125_221530_7_engraçado"
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "vertical.mp4").write_bytes(b"V" * 100)
    (pasta / "corte.mp4").write_bytes(b"H" * 50)
    (pasta / "thumb.jpg").write_bytes(b"J")
    rel = "20261125_221530_7_engraçado"
    return db.insert(channel="kick:gabepeixe", t_start=0, t_end=41, ai_score=80, category="engraçado",
                     title="Gabe ri demais", description="desc", hashtags=["gabepeixe"], status=status,
                     file_h=f"{rel}/corte.mp4", file_v=f"{rel}/vertical.mp4", thumb=f"{rel}/thumb.jpg")


def test_conectar_pelo_painel_e_enviar(tmp_path, site_falso):
    url, recebido = site_falso
    cfg = Config(data_dir=tmp_path / "data"); cfg.ensure_dirs()
    web = TestClient(create_app(cfg))
    r = web.post("/api/site", json={"url": url, "codigo": "gc_errado"})
    assert r.status_code == 400
    r = web.post("/api/site", json={"url": url, "codigo": f"  {CODIGO} "})
    assert r.status_code == 200, r.text
    assert site.carregar(cfg)["codigo"] == CODIGO

    db = ClipDB(cfg.db_path)
    cid = _clip(cfg, db)
    _clip(cfg, db, status="descartado")  # descartado não vai para o site
    up = site.SiteUploader(cfg, db)
    assert up.sync() == 1
    sid = "20261125_221530_7_engraado"
    assert recebido["arquivos"][f"{sid}/vertical.mp4"] == b"V" * 100
    assert set(k.split("/")[1] for k in recebido["arquivos"]) == {"vertical.mp4", "corte.mp4", "thumb.jpg"}
    reg = recebido["registros"][sid]
    assert reg["titulo"] == "Gabe ri demais" and reg["hashtags"] == ["gabepeixe"] and reg["duracao"] == 41
    assert db.get(cid)["enviado"] == 1
    assert up.sync() == 0  # não envia de novo


def test_codigo_errado_registra_erro_sem_marcar_enviado(tmp_path, site_falso):
    url, _ = site_falso
    cfg = Config(data_dir=tmp_path / "data"); cfg.ensure_dirs()
    site.salvar(cfg, url, "gc_errado")
    db = ClipDB(cfg.db_path)
    cid = _clip(cfg, db)
    up = site.SiteUploader(cfg, db)
    assert up.sync() == 0
    assert up.ultimo_erro and not db.get(cid)["enviado"]
    assert up.status()["pendentes"] == 1


def test_id_no_site_e_url():
    assert site.id_no_site({"id": 3, "file_h": "2026_9_engraçado/corte.mp4"}) == "2026_9_engraado"
    assert site.normalizar_url("www.aamcell.com.br/") == "https://www.aamcell.com.br"
