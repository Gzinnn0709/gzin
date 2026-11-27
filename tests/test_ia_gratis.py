"""IAs grátis com revezamento: limite, chave ruim, modelo inexistente, intercalação e legenda."""
import json
import shutil
import socket
import subprocess
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from gabecuts import ai, ia_gratis

RESPOSTA = {"keep": True, "score": 82, "title": "Gabe perde tudo no último segundo", "gancho": "NÃO ACREDITO",
            "description": "Clássico.", "hashtags": ["#Gabepeixe", "twitch", "gta"], "category": "rage",
            "trim_start": 5, "trim_end": 70, "reason": "reação forte"}


@pytest.fixture()
def servidor():
    app = FastAPI()
    chamadas = []

    def ok(texto):
        return {"choices": [{"message": {"content": texto}}]}

    @app.post("/{nome}/chat/completions")
    async def chat(nome: str, request: Request):
        corpo = await request.json()
        chamadas.append((nome, corpo["model"], "response_format" in corpo))
        if nome == "limite":
            return JSONResponse({"error": "rate"}, status_code=429, headers={"retry-after": "120"})
        if nome == "chaveruim":
            return JSONResponse({"error": "bad key"}, status_code=401)
        if nome == "semmodelo" and corpo["model"] == "velho":
            return JSONResponse({"error": "model not found"}, status_code=404)
        if nome == "semjson" and "response_format" in corpo:
            return JSONResponse({"error": "response_format unsupported"}, status_code=400)
        return ok("Claro! ```json\n" + json.dumps(RESPOSTA) + "\n```")

    @app.post("/api/cortes/ia")
    async def ia_site(request: Request):
        assert request.headers["authorization"] == "Bearer gc_codigo"
        chamadas.append(("site", "auto", False))
        return {"sucesso": True, "texto": json.dumps(RESPOSTA)}

    @app.post("/audio")
    async def audio(request: Request):
        await request.form()
        return {"segments": [{"start": 0.0, "end": 1.6, "text": " olha isso"}],
                "words": [{"start": 0.0, "end": 0.7, "word": "olha"}, {"start": 0.8, "end": 1.6, "word": "isso"}]}

    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True); t.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", chamadas
    srv.should_exit = True
    t.join(5)


def _rev(tmp_path, url, nomes):
    rev = ia_gratis.Revezamento(tmp_path / "ia.json")
    rev.provedores = []
    for n in nomes:
        p = ia_gratis.Provedor(n, n.upper(), f"{url}/{n}", ["velho", "novo"] if n == "semmodelo" else ["m1"],
                               "X", "https://x")
        p.chave = "k"
        rev.provedores.append(p)
    return rev


def test_revezamento_pula_limite_chave_ruim_e_modelo_inexistente(tmp_path, servidor):
    url, chamadas = servidor
    rev = _rev(tmp_path, url, ["limite", "chaveruim", "semmodelo", "semjson"])
    d, quem = rev.chat_json("sys", "user")
    assert d["title"].startswith("Gabe") and quem == "SEMMODELO · novo"   # pulou 429, 401 e o modelo velho
    st = {p["id"]: p["situacao"] for p in rev.status()}
    assert st["limite"].startswith("pausada (limite") and st["chaveruim"].startswith("pausada (chave")
    assert st["semmodelo"] == "ativa"
    # intercala: o próximo pedido começa por outro provedor livre
    _, quem2 = rev.chat_json("sys", "user")
    assert quem2.startswith("SEMJSON")
    assert ("semjson", "m1", False) in chamadas          # desligou o modo JSON e tentou de novo


def test_sem_ia_disponivel(tmp_path, servidor):
    url, _ = servidor
    rev = _rev(tmp_path, url, ["limite"])
    with pytest.raises(ia_gratis.SemIA):
        rev.chat_json("s", "u")
    with pytest.raises(ia_gratis.SemIA):              # pausada: nem tenta
        rev.chat_json("s", "u")


def test_revisao_do_corte_usa_ia_gratis(tmp_path, servidor, monkeypatch):
    url, _ = servidor
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rev = _rev(tmp_path, url, ["semjson"])
    monkeypatch.setattr(ia_gratis, "_instancias", {str(tmp_path.resolve()): rev})
    rv = ai.ai_review("x", 90.0, "[0s] oi", [], {}, "hype", 10, 6, data_dir=tmp_path)
    assert rv.by_ai and rv.score == 82 and rv.category == "rage"
    assert rv.hashtags[:2] == ["gabepeixe", "gta"] and "kick" in rv.hashtags and "twitch" not in rv.hashtags
    assert (rv.trim_start, rv.trim_end) == (5.0, 70.0) and "SEMJSON" in rv.reason


def test_salvar_e_testar_chave(tmp_path, servidor):
    url, _ = servidor
    rev = _rev(tmp_path, url, ["semjson", "chaveruim"])
    rev.salvar_chave("semjson", "  nova  ")
    assert json.loads((tmp_path / "ia.json").read_text())["semjson"]["chave"] == "nova"
    assert rev.testar("semjson")[0] is True
    assert rev.testar("chaveruim") == (False, "chave recusada: confira se copiou inteira")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg ausente")
def test_legenda_pelo_groq_gratis(tmp_path, servidor, monkeypatch):
    url, _ = servidor
    from gabecuts import transcribe
    rev = _rev(tmp_path, url, ["groq"])
    monkeypatch.setattr(ia_gratis, "_instancias", {str(tmp_path.resolve()): rev})
    monkeypatch.setattr(ia_gratis, "GROQ_AUDIO_URL", f"{url}/audio")
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=2", str(video)], check=True)
    linhas = transcribe.transcribe(video, data_dir=tmp_path)
    assert linhas[0].text == "olha isso" and [w.text for w in linhas[0].words] == ["olha", "isso"]


def test_extrair_json():
    assert ia_gratis.extrair_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert ia_gratis.extrair_json('Aqui está: {"a": {"b": 2}} pronto') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        ia_gratis.extrair_json("sem json")


def test_ia_do_site_entra_sozinha_e_fica_por_ultimo(tmp_path, servidor, monkeypatch):
    url, chamadas = servidor
    for v in ("GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY",
              "GITHUB_MODELS_TOKEN", "HF_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    rev = ia_gratis.Revezamento(tmp_path / "ia.json")
    assert rev.ativos() == []                                  # nada conectado: sem IA
    (tmp_path / "site.json").write_text(json.dumps({"url": url, "codigo": "gc_codigo"}))
    d, quem = rev.chat_json("s", "u")                          # conectou ao site: já tem IA
    assert quem.startswith("Site da loja") and d["score"] == 82
    st = {p["id"]: p for p in rev.status()}
    assert st["site"]["automatica"] and st["site"]["situacao"] == "ativa"
    # com outra IA com chave, o site fica de reserva (por último)
    rev.get("mistral").chave, rev.get("mistral").base_url = "k", f"{url}/semjson"
    assert [p.id for p in rev.ordem()] == ["mistral", "site"]
