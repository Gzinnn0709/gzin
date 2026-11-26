"""Envio dos cortes prontos para a aba escondida "✂️ Cortes" do site (painel do admin).

O código de conexão é gerado no site (Administração → Cortes → 🔗 Conectar GabeCuts) e colado no
painel local (🌐 Site). Fica salvo em data/site.json, que nunca vai para o git.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

import httpx

from .config import Config
from .db import ClipDB

log = logging.getLogger(__name__)

SITE_PADRAO = "https://www.aamcell.com.br"
ENVIAR_STATUS = ("pendente", "aprovado", "postado")


def _arquivo(cfg: Config) -> Path:
    return cfg.data_dir / "site.json"


def carregar(cfg: Config) -> dict:
    d = {"url": SITE_PADRAO, "codigo": ""}
    try:
        d.update(json.loads(_arquivo(cfg).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return d


def salvar(cfg: Config, url: str, codigo: str) -> None:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    _arquivo(cfg).write_text(json.dumps({"url": url, "codigo": codigo}), encoding="utf-8")


def normalizar_url(url: str) -> str:
    url = (url or SITE_PADRAO).strip().rstrip("/")
    if not re.match(r"^https?://", url):
        url = "https://" + url
    return url


def conferir(url: str, codigo: str) -> tuple[bool, str]:
    """Pergunta ao site se o código vale. Devolve (ok, mensagem)."""
    try:
        r = httpx.get(f"{normalizar_url(url)}/api/cortes/ping", timeout=20,
                      headers={"Authorization": f"Bearer {codigo.strip()}"})
    except httpx.HTTPError as e:
        return False, f"não consegui falar com o site ({e.__class__.__name__})"
    if r.status_code == 200:
        try:
            if r.json().get("sucesso") is True:
                return True, "conectado"
        except ValueError:
            pass
        return False, "esse endereço não parece ser o site certo"
    if r.status_code == 404:
        return False, "o site ainda não tem a aba Cortes (precisa publicar a versão nova)"
    try:
        return False, r.json().get("erro") or f"erro {r.status_code}"
    except ValueError:
        return False, f"erro {r.status_code}"


def id_no_site(clip: dict) -> str:
    """Id seguro (só letras, números, _ e -) a partir da pasta do corte."""
    pasta = Path(clip.get("file_h") or clip.get("file_v") or f"corte_{clip['id']}").parent.name
    base = re.sub(r"[^A-Za-z0-9_-]", "", pasta) or f"corte_{clip['id']}"
    return base[:80]


class SiteUploader:
    def __init__(self, cfg: Config, db: ClipDB):
        self.cfg = cfg
        self.db = db
        self.ultimo_erro: str = ""
        self.ultimo_envio: float = 0.0
        self._lock = threading.Lock()

    def configurado(self) -> dict:
        return carregar(self.cfg)

    def status(self) -> dict:
        c = carregar(self.cfg)
        pend = sum(1 for x in self.db.list(limit=500) if x["status"] in ENVIAR_STATUS and not x.get("enviado"))
        return {"url": c["url"], "conectado": bool(c.get("codigo")), "erro": self.ultimo_erro,
                "ultimo_envio": self.ultimo_envio, "pendentes": pend}

    def _enviar(self, url: str, codigo: str, clip: dict) -> None:
        cid = id_no_site(clip)
        h = {"Authorization": f"Bearer {codigo}"}
        base = f"{normalizar_url(url)}/api/cortes/{cid}"
        # só UMA versão leve do vídeo (o vertical, se houver) + miniatura: economiza o 1 GB grátis
        from . import clipper
        arquivos = []
        fonte = None
        if clip.get("file_v") and (self.cfg.clips_dir / clip["file_v"]).exists():
            fonte, nome_site, vertical = self.cfg.clips_dir / clip["file_v"], "vertical.mp4", True
        elif clip.get("file_h") and (self.cfg.clips_dir / clip["file_h"]).exists():
            fonte, nome_site, vertical = self.cfg.clips_dir / clip["file_h"], "corte.mp4", False
        if fonte is not None:
            leve = fonte.parent / "site.mp4"
            if not leve.exists():
                try:
                    clipper.web_version(fonte, leve, vertical)
                except Exception as e:  # noqa: BLE001
                    log.warning("Versão leve falhou (%s); enviando o original", str(e)[-200:])
                    leve = fonte
            arquivos.append((nome_site, leve))
        if clip.get("thumb"):
            arquivos.append(("thumb.jpg", self.cfg.clips_dir / clip["thumb"]))
        enviados = 0
        with httpx.Client(timeout=httpx.Timeout(30, write=300, read=120)) as cli:
            for nome, caminho in arquivos:
                if not caminho.exists():
                    continue
                if caminho.stat().st_size > 49 * 1024 * 1024:
                    log.warning("Corte #%d: %s grande demais para o site, pulando", clip["id"], nome)
                    continue
                r = cli.put(f"{base}/arquivo/{nome}", content=caminho.read_bytes(),
                            headers={**h, "Content-Type": "application/octet-stream"})
                _checar(r)
                enviados += nome != "thumb.jpg"
            if not enviados:
                raise RuntimeError("nenhum vídeo para enviar")
            r = cli.post(base, headers=h, json={
                "titulo": clip.get("title"), "descricao": clip.get("description"),
                "hashtags": clip.get("hashtags") or [], "categoria": clip.get("category"),
                "nota": clip.get("ai_score"), "duracao": (clip.get("t_end") or 0) - (clip.get("t_start") or 0),
                "canal": clip.get("channel"),
                "criado_em": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(clip.get("created_at") or time.time())),
            })
            _checar(r)

    def sync(self, max_por_vez: int = 5) -> int:
        """Envia os cortes prontos que ainda não foram para o site. Devolve quantos enviou."""
        c = carregar(self.cfg)
        if not c.get("codigo"):
            return 0
        if not self._lock.acquire(blocking=False):
            return 0
        enviados = 0
        try:
            fila = [x for x in self.db.list(limit=200) if x["status"] in ENVIAR_STATUS and not x.get("enviado")]
            for clip in reversed(fila[:max_por_vez * 4]):  # mais antigos primeiro
                if enviados >= max_por_vez:
                    break
                try:
                    self._enviar(c["url"], c["codigo"], clip)
                except Exception as e:  # noqa: BLE001
                    self.ultimo_erro = str(e)[:300]
                    log.warning("Não enviei o corte #%d para o site: %s", clip["id"], self.ultimo_erro)
                    if "inválido" in self.ultimo_erro or "desligado" in self.ultimo_erro:
                        break  # código errado: não adianta tentar os outros
                    continue
                self.db.update(clip["id"], enviado=1)
                self.ultimo_envio = time.time()
                self.ultimo_erro = ""
                enviados += 1
                log.info("🌐 Corte #%d enviado para o site", clip["id"])
        finally:
            self._lock.release()
        return enviados


def enviar_status(cfg: Config, dados: dict) -> dict:
    """Manda a situação para a página /cortes do site.

    Devolve os pedidos feitos pela página: {"cortar_agora": segundos, "vods": [links]}.
    """
    c = carregar(cfg)
    if not c.get("codigo"):
        return {}
    r = httpx.post(f"{normalizar_url(c['url'])}/api/cortes/status", json=dados, timeout=60,
                   headers={"Authorization": f"Bearer {c['codigo']}"})
    _checar(r)
    try:
        j = r.json()
    except ValueError:
        return {}
    return {"cortar_agora": int(j.get("cortar_agora") or 0), "vods": list(j.get("vods") or [])}


def _checar(r: httpx.Response) -> None:
    if r.status_code < 400:
        return
    try:
        msg = r.json().get("erro")
    except ValueError:
        msg = None
    raise RuntimeError(msg or f"o site respondeu {r.status_code}")
