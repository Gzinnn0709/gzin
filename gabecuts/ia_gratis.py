"""IAs grátis (não-Claude) com revezamento automático.

Todas usam o formato de chat "compatível com OpenAI", então um cliente só serve para todas.
Cada pedido começa num provedor diferente (intercala, para nenhum estourar o limite) e, se um
der limite (429), chave inválida ou erro, ele fica "de castigo" um tempo e o próximo assume.

As chaves ficam em data/ia.json (o painel local tem o botão 🤖 IA para colar) ou em variáveis
de ambiente (GEMINI_API_KEY, GROQ_API_KEY, ...). Nada disso vai para o git.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


@dataclass
class Provedor:
    id: str
    nome: str
    base_url: str               # .../chat/completions é acrescentado
    modelos: list[str]
    env: str                    # variável de ambiente com a chave
    onde_criar: str             # link para criar a chave grátis
    json_mode: bool = True      # aceita response_format={"type":"json_object"}
    chave: str = ""
    # estado do revezamento
    castigo_ate: float = 0.0
    motivo: str = ""
    usos: int = 0
    falhas: int = 0
    modelos_ruins: set = field(default_factory=set)


# Modelos grátis conhecidos (a lista de cada um pode ser trocada no data/ia.json).
PROVEDORES_PADRAO = [
    Provedor("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
             ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"], "GEMINI_API_KEY",
             "https://aistudio.google.com/apikey"),
    Provedor("groq", "Groq", "https://api.groq.com/openai/v1",
             ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "llama-3.1-8b-instant"], "GROQ_API_KEY",
             "https://console.groq.com/keys"),
    Provedor("cerebras", "Cerebras", "https://api.cerebras.ai/v1",
             ["gpt-oss-120b", "llama-3.3-70b", "llama3.1-8b"], "CEREBRAS_API_KEY",
             "https://cloud.cerebras.ai/"),
    Provedor("openrouter", "OpenRouter (modelos :free)", "https://openrouter.ai/api/v1",
             ["google/gemma-4-31b-it:free", "meta-llama/llama-3.3-70b-instruct:free",
              "deepseek/deepseek-chat-v3.1:free", "qwen/qwen3-235b-a22b:free"], "OPENROUTER_API_KEY",
             "https://openrouter.ai/settings/keys", json_mode=False),
    Provedor("mistral", "Mistral", "https://api.mistral.ai/v1",
             ["mistral-small-latest", "open-mistral-nemo"], "MISTRAL_API_KEY",
             "https://console.mistral.ai/api-keys"),
    Provedor("github", "GitHub Models", "https://models.github.ai/inference",
             ["openai/gpt-4.1-mini", "meta/Llama-3.3-70B-Instruct"], "GITHUB_MODELS_TOKEN",
             "https://github.com/settings/personal-access-tokens", json_mode=False),
    Provedor("huggingface", "Hugging Face", "https://router.huggingface.co/v1",
             ["meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen2.5-72B-Instruct"], "HF_TOKEN",
             "https://huggingface.co/settings/tokens", json_mode=False),
]


class SemIA(RuntimeError):
    """Nenhuma IA grátis disponível agora (sem chave ou todas no limite)."""


class Revezamento:
    def __init__(self, arquivo: Path | None = None):
        self.arquivo = arquivo
        self.provedores = [copy.deepcopy(p) for p in PROVEDORES_PADRAO]
        self._vez = 0
        self._lock = threading.Lock()
        self.carregar()

    # --- chaves ------------------------------------------------------------
    def carregar(self) -> None:
        dados = {}
        if self.arquivo and self.arquivo.exists():
            try:
                dados = json.loads(self.arquivo.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                dados = {}
        for p in self.provedores:
            d = dados.get(p.id) or {}
            p.chave = (d.get("chave") or os.environ.get(p.env, "")).strip()
            if d.get("modelos"):
                p.modelos = [m for m in d["modelos"] if isinstance(m, str) and m.strip()]

    def salvar_chave(self, pid: str, chave: str) -> None:
        dados = {}
        if self.arquivo and self.arquivo.exists():
            try:
                dados = json.loads(self.arquivo.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                dados = {}
        dados.setdefault(pid, {})["chave"] = chave.strip()
        if self.arquivo:
            self.arquivo.parent.mkdir(parents=True, exist_ok=True)
            self.arquivo.write_text(json.dumps(dados, indent=2), encoding="utf-8")
        for p in self.provedores:
            if p.id == pid:
                p.chave, p.castigo_ate, p.motivo, p.modelos_ruins = chave.strip(), 0.0, "", set()

    def testar(self, pid: str) -> tuple[bool, str]:
        """Faz um pedido bem pequeno só nesse provedor para conferir a chave."""
        p = self.get(pid)
        if not p or not p.chave:
            return False, "sem chave"
        ultimo = ""
        for modelo in p.modelos:
            try:
                extrair_json(self._pedir(p, modelo, "Responda só JSON.", 'Responda {"ok": true}', 20))
                return True, f"funcionando ({modelo})"
            except _ModeloRuim as e:
                ultimo = str(e)
                continue
            except _ChaveRuim:
                return False, "chave recusada: confira se copiou inteira"
            except _Limite:
                return True, "chave certa, mas está no limite agora (volta sozinha)"
            except Exception as e:  # noqa: BLE001
                ultimo = str(e)[:150]
                continue
        return False, ultimo or "não respondeu"

    def get(self, pid: str) -> Provedor | None:
        return next((p for p in self.provedores if p.id == pid), None)

    def ativos(self) -> list[Provedor]:
        return [p for p in self.provedores if p.chave]

    def status(self) -> list[dict]:
        agora = time.time()
        return [{"id": p.id, "nome": p.nome, "tem_chave": bool(p.chave), "onde_criar": p.onde_criar,
                 "situacao": ("sem chave" if not p.chave else
                              f"pausada ({p.motivo}, volta em {int((p.castigo_ate - agora) / 60) + 1} min)"
                              if p.castigo_ate > agora else "ativa"),
                 "usos": p.usos, "falhas": p.falhas} for p in self.provedores]

    # --- revezamento -------------------------------------------------------
    def _castigar(self, p: Provedor, segundos: float, motivo: str) -> None:
        p.castigo_ate = time.time() + segundos
        p.motivo = motivo
        p.falhas += 1
        log.warning("IA %s pausada por %d min: %s", p.nome, int(segundos / 60) or 1, motivo)

    def ordem(self) -> list[Provedor]:
        """Provedores disponíveis, começando por um diferente a cada pedido (intercala)."""
        agora = time.time()
        livres = [p for p in self.provedores if p.chave and p.castigo_ate <= agora]
        if not livres:
            return []
        with self._lock:
            i = self._vez % len(livres)
            self._vez += 1
        return livres[i:] + livres[:i]

    def chat_json(self, system: str, user: str, max_tokens: int = 1200) -> tuple[dict, str]:
        """Pede uma resposta em JSON. Devolve (dados, "Provedor/modelo")."""
        ordem = self.ordem()
        if not ordem:
            raise SemIA("nenhuma IA grátis com chave disponível agora")
        ultimo_erro = ""
        for p in ordem:
            for modelo in [m for m in p.modelos if m not in p.modelos_ruins]:
                try:
                    texto = self._pedir(p, modelo, system, user, max_tokens)
                    dados = extrair_json(texto)
                    p.usos += 1
                    return dados, f"{p.nome} · {modelo}"
                except _ModeloRuim as e:
                    p.modelos_ruins.add(modelo)
                    ultimo_erro = str(e)
                    continue
                except _Limite as e:
                    self._castigar(p, e.espera, "limite grátis atingido")
                    ultimo_erro = str(e)
                    break
                except _ChaveRuim as e:
                    self._castigar(p, 6 * 3600, "chave recusada")
                    ultimo_erro = str(e)
                    break
                except (_Temporario, httpx.HTTPError, ValueError) as e:
                    ultimo_erro = f"{p.nome}: {str(e)[:150]}"
                    continue  # tenta o próximo modelo / provedor
            else:
                if p.modelos and all(m in p.modelos_ruins for m in p.modelos):
                    self._castigar(p, 3600, "nenhum modelo da lista funcionou")
        raise SemIA(f"todas as IAs grátis falharam agora ({ultimo_erro})")

    def _pedir(self, p: Provedor, modelo: str, system: str, user: str, max_tokens: int) -> str:
        corpo = {"model": modelo, "temperature": 0.4, "max_tokens": max_tokens,
                 "messages": [{"role": "system", "content": system},
                              {"role": "user", "content": user}]}
        if p.json_mode:
            corpo["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {p.chave}", "Content-Type": "application/json"}
        if p.id == "openrouter":
            headers.update({"HTTP-Referer": "https://github.com/Gzinnn0709/gzin", "X-Title": "GabeCuts"})
        r = httpx.post(f"{p.base_url.rstrip('/')}/chat/completions", json=corpo, headers=headers, timeout=90)
        if r.status_code == 429:
            espera = _retry_after(r) or 15 * 60
            raise _Limite(f"{p.nome}: limite (429)", espera)
        if r.status_code in (401, 403):
            raise _ChaveRuim(f"{p.nome}: chave recusada ({r.status_code})")
        if r.status_code in (400, 404, 422) and re.search(r"model|not found|does not exist|unsupported|invalid",
                                                           r.text, re.I):
            if p.json_mode and "response_format" in r.text:
                p.json_mode = False  # esse provedor não aceita modo JSON: tenta de novo sem
                return self._pedir(p, modelo, system, user, max_tokens)
            raise _ModeloRuim(f"{p.nome}: modelo {modelo} indisponível")
        if r.status_code >= 500 or r.status_code == 408:
            raise _Temporario(f"{p.nome}: erro {r.status_code}")
        if r.status_code >= 400:
            raise _Temporario(f"{p.nome}: erro {r.status_code}: {r.text[:120]}")
        dados = r.json()
        try:
            return dados["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise _Temporario(f"{p.nome}: resposta estranha") from e


class _Limite(Exception):
    def __init__(self, msg: str, espera: float):
        super().__init__(msg)
        self.espera = espera


class _ChaveRuim(Exception):
    pass


class _ModeloRuim(Exception):
    pass


class _Temporario(Exception):
    pass


def _retry_after(r: httpx.Response) -> float | None:
    v = r.headers.get("retry-after")
    if not v:
        return None
    try:
        return max(float(v), 30.0)
    except ValueError:
        return None


def extrair_json(texto: str) -> dict:
    """Acha o JSON na resposta (alguns modelos põem texto ou ```json em volta)."""
    t = (texto or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I).strip()
    try:
        d = json.loads(t)
        if isinstance(d, dict):
            return d
    except ValueError:
        pass
    ini, fim = t.find("{"), t.rfind("}")
    if ini >= 0 and fim > ini:
        d = json.loads(t[ini:fim + 1])
        if isinstance(d, dict):
            return d
    raise ValueError("a IA não respondeu em JSON")


# --- Transcrição grátis (Groq Whisper) --------------------------------------------------------

GROQ_AUDIO_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

def transcrever_groq(chave: str, audio: Path, idioma: str = "pt") -> list[dict]:
    """Whisper large-v3-turbo de graça no Groq. Devolve [{start, end, text, words:[...]}]."""
    with open(audio, "rb") as f:
        r = httpx.post(GROQ_AUDIO_URL,
                       headers={"Authorization": f"Bearer {chave}"},
                       data={"model": "whisper-large-v3-turbo", "language": idioma,
                             "response_format": "verbose_json",
                             "timestamp_granularities[]": ["word", "segment"]},
                       files={"file": (audio.name, f, "audio/mpeg")}, timeout=180)
    if r.status_code == 429:
        raise _Limite("Groq Whisper: limite (429)", _retry_after(r) or 15 * 60)
    if r.status_code in (401, 403):
        raise _ChaveRuim("Groq Whisper: chave recusada")
    r.raise_for_status()
    d = r.json()
    palavras = d.get("words") or []
    segs = []
    for s in d.get("segments") or []:
        ws = [w for w in palavras if s.get("start", 0) - 0.05 <= w.get("start", 0) < s.get("end", 0) + 0.05]
        segs.append({"start": float(s.get("start", 0)), "end": float(s.get("end", 0)),
                     "text": (s.get("text") or "").strip(),
                     "words": [{"start": float(w.get("start", 0)), "end": float(w.get("end", 0)),
                                "text": (w.get("word") or "").strip()} for w in ws]})
    return [s for s in segs if s["text"]]


# --- um revezamento por pasta de dados (compartilhado entre cortes, lives passadas e painel) ---
_instancias: dict[str, Revezamento] = {}
_whisper_castigo = {"ate": 0.0}


def revezamento(data_dir: Path) -> Revezamento:
    k = str(Path(data_dir).resolve())
    if k not in _instancias:
        _instancias[k] = Revezamento(Path(data_dir) / "ia.json")
    return _instancias[k]


def groq_whisper_livre(rev: Revezamento) -> str:
    """Chave do Groq se a transcrição grátis estiver disponível agora; senão ''."""
    g = rev.get("groq")
    if not g or not g.chave or _whisper_castigo["ate"] > time.time():
        return ""
    return g.chave


def castigar_whisper(segundos: float) -> None:
    _whisper_castigo["ate"] = time.time() + segundos
