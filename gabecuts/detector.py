"""Detector de momentos: combina sinais do chat e do áudio em um score por segundo.

Ideia geral
-----------
* Cada segundo vira um *bucket* com: nº de mensagens, risadas, hype, pedidos de clip,
  usuários únicos, mensagem mais repetida e volume do áudio.
* Mantemos uma linha de base móvel (média/variância exponencial) da taxa de mensagens e do
  volume. O que importa é o quanto o momento foge do normal (z-score) – assim o mesmo sistema
  funciona com 500 ou 50.000 pessoas no chat.
* Quando o score passa do limiar, abrimos um "momento"; ele fecha quando o score cai.
* O corte começa um pouco ANTES do pico do chat (o chat reage com atraso) e inclui contexto.

O detector é determinístico e recebe o tempo explicitamente (`tick(now)`), o que permite
rodar ao vivo ou sobre um VOD gravado com o chat exportado.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field

from .chat import ChatMessage
from .config import Config
from .signals import classify_message, normalize_for_spam


@dataclass
class Bucket:
    msgs: int = 0
    laughs: int = 0
    hype: int = 0
    clip_calls: int = 0
    # contagens por usuário: permitem limitar o peso de quem floda o chat
    users: Counter = field(default_factory=Counter)
    user_laughs: Counter = field(default_factory=Counter)
    user_hype: Counter = field(default_factory=Counter)
    user_clips: Counter = field(default_factory=Counter)
    spam: Counter = field(default_factory=Counter)
    audio_db: float | None = None

# máximo de mensagens de um mesmo usuário que contam dentro de uma janela
PER_USER_CAP = 2


def _capped(c: Counter, cap: int = PER_USER_CAP) -> int:
    return sum(min(v, cap) for v in c.values())


class Ewma:
    """Média e variância exponenciais."""

    def __init__(self, span: float, init_mean: float = 0.0, init_var: float = 1.0):
        self.alpha = 2.0 / (span + 1.0)
        self.mean = init_mean
        self.var = init_var
        self.n = 0

    def update(self, x: float) -> None:
        if self.n == 0:
            self.mean = x
        else:
            d = x - self.mean
            self.mean += self.alpha * d
            self.var = (1 - self.alpha) * (self.var + self.alpha * d * d)
        self.n += 1

    def z(self, x: float, min_std: float) -> float:
        return (x - self.mean) / max(math.sqrt(self.var), min_std)


@dataclass
class Moment:
    """Um momento detectado, já com os limites sugeridos para o corte (epoch, segundos)."""
    event_start: float
    event_end: float
    peak_time: float
    peak_score: float
    clip_start: float
    clip_end: float
    category: str
    stats: dict

    @property
    def duration(self) -> float:
        return self.clip_end - self.clip_start


@dataclass
class _Open:
    start: int
    peak_t: int
    peak_s: float
    last_above: int
    scores: list = field(default_factory=list)
    totals: Counter = field(default_factory=Counter)


class MomentDetector:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.buckets: dict[int, Bucket] = defaultdict(Bucket)
        self.rate = Ewma(cfg.baseline_seconds, init_mean=0.0, init_var=1.0)
        self.audio = Ewma(cfg.baseline_seconds, init_mean=-30.0, init_var=25.0)
        self.cursor: int | None = None     # último segundo já avaliado
        self.first_t: int | None = None
        self.open: _Open | None = None
        self.last_clip_end: float = 0.0
        self.recent_scores: deque = deque(maxlen=600)  # (t, score, componentes) para o painel

    # --- entrada de dados ------------------------------------------------
    def add_message(self, msg: ChatMessage) -> None:
        t = int(msg.ts)
        b = self.buckets[t]
        laugh, hype, clip = classify_message(msg.text)
        b.msgs += 1
        b.laughs += laugh
        b.hype += hype
        b.clip_calls += clip
        u = msg.user.lower()
        b.users[u] += 1
        b.user_laughs[u] += laugh
        b.user_hype[u] += hype
        b.user_clips[u] += clip
        b.spam[normalize_for_spam(msg.text)] += 1
        if self.first_t is None or t < self.first_t:
            self.first_t = t

    def add_audio(self, start_ts: float, levels: list[float], bucket: float = 1.0) -> None:
        for i, db in enumerate(levels):
            t = int(start_ts + i * bucket)
            self.buckets[t].audio_db = db
            if self.first_t is None or t < self.first_t:
                self.first_t = t

    # --- avaliação -------------------------------------------------------
    def _window(self, t: int) -> dict:
        w = self.cfg.window_seconds
        users: Counter = Counter()
        laughs: Counter = Counter()
        hype: Counter = Counter()
        clips: Counter = Counter()
        spam: Counter = Counter()
        raw = 0
        audio_vals = []
        for s in range(t - w + 1, t + 1):
            b = self.buckets.get(s)
            if not b:
                continue
            raw += b.msgs
            users.update(b.users)
            laughs.update(b.user_laughs)
            hype.update(b.user_hype)
            clips.update(b.user_clips)
            spam.update(b.spam)
            if b.audio_db is not None:
                audio_vals.append(b.audio_db)
        top_spam = spam.most_common(1)[0][1] if spam else 0
        return dict(msgs=_capped(users), raw_msgs=raw, laughs=_capped(laughs), hype=_capped(hype),
                    clip_calls=sum(1 for v in clips.values() if v), unique=len(users),
                    spam_ratio=(top_spam / raw) if raw else 0.0,
                    audio_db=max(audio_vals) if audio_vals else None)

    def score_second(self, t: int) -> tuple[float, dict]:
        c = self.cfg
        win = self._window(t)
        rate = win["msgs"] / c.window_seconds
        z_rate = self.rate.z(rate, min_std=max(0.5, 0.15 * self.rate.mean))
        msgs = max(win["msgs"], 1)
        laugh_frac = win["laughs"] / msgs
        hype_frac = win["hype"] / msgs

        chat = max(0.0, z_rate) * (c.w_chat + c.w_laugh * laugh_frac + c.w_hype * hype_frac)
        # onda de emote/copypasta (todo mundo mandando a mesma coisa) reforça o sinal
        chat *= 1.0 + 0.5 * min(win["spam_ratio"], 0.8)
        if win["unique"] < c.min_unique_chatters:
            chat *= 0.2
        clip = c.w_clip_call * min(win["clip_calls"], 4)

        audio = 0.0
        z_audio = 0.0
        if win["audio_db"] is not None:
            z_audio = self.audio.z(win["audio_db"], min_std=2.0)
            audio = c.w_audio * max(0.0, z_audio)

        score = chat + clip + audio
        comps = dict(win, z_rate=round(z_rate, 2), z_audio=round(z_audio, 2),
                     laugh_frac=round(laugh_frac, 2), hype_frac=round(hype_frac, 2),
                     chat=round(chat, 2), clip=round(clip, 2), audio=round(audio, 2))
        return score, comps

    def _update_baselines(self, t: int) -> None:
        b = self.buckets.get(t)
        # a média do chat usa a taxa da janela, para ser comparável ao score
        self.rate.update(self._window(t)["msgs"] / self.cfg.window_seconds)
        if b is not None and b.audio_db is not None:
            self.audio.update(b.audio_db)

    def tick(self, now: float) -> list[Moment]:
        """Avalia todos os segundos até `now - analysis_lag`. Retorna momentos fechados."""
        if self.first_t is None:
            return []
        target = int(now - self.cfg.analysis_lag)
        if self.cursor is None:
            self.cursor = self.first_t - 1
        out: list[Moment] = []
        while self.cursor < target:
            self.cursor += 1
            t = self.cursor
            m = self._step(t)
            if m:
                out.append(m)
        self._gc(target)
        return out

    def flush(self) -> list[Moment]:
        """Fecha o momento aberto (fim da live / fim do arquivo)."""
        if self.open:
            m = self._close(self.open)
            self.open = None
            return [m] if m else []
        return []

    def _step(self, t: int) -> Moment | None:
        c = self.cfg
        score, comps = self.score_second(t)
        self.recent_scores.append((t, round(score, 2), comps))
        warm = self.first_t is not None and t - self.first_t >= c.warmup_seconds
        above = warm and score >= c.threshold

        result = None
        if self.open is None:
            if above and t - c.chat_delay - c.pre_roll >= self.last_clip_end - 5:
                self.open = _Open(start=t, peak_t=t, peak_s=score, last_above=t)
        if self.open is not None:
            o = self.open
            o.scores.append(score)
            b = self.buckets.get(t)
            if b is not None:
                o.totals.update(msgs=b.msgs, laughs=b.laughs, hype=b.hype, clip_calls=b.clip_calls)
            if score >= c.threshold * 0.5:
                o.last_above = t
            if score > o.peak_s:
                o.peak_s, o.peak_t = score, t
            too_long = t - o.start >= c.max_clip_seconds
            if t - o.last_above >= 4 or too_long:
                result = self._close(o)
                self.open = None

        # não deixa picos "contaminarem" a linha de base
        if self.open is None:
            self._update_baselines(t)
        return result

    def _close(self, o: _Open) -> Moment | None:
        c = self.cfg
        end = o.last_above
        clip_start = o.start - c.chat_delay - c.pre_roll
        clip_end = end - c.chat_delay + c.post_roll
        # garante tamanho mínimo/máximo, centrado no pico
        center = o.peak_t - c.chat_delay
        if clip_end - clip_start < c.min_clip_seconds:
            half = c.min_clip_seconds / 2
            clip_start, clip_end = min(clip_start, center - half), max(clip_end, center + half)
        if clip_end - clip_start > c.max_clip_seconds:
            clip_start = max(clip_start, center - c.max_clip_seconds * 0.7)
            clip_end = clip_start + c.max_clip_seconds
        clip_start = max(clip_start, self.last_clip_end)
        if clip_end - clip_start < c.min_clip_seconds * 0.6:
            return None
        t = o.totals
        laughs, hype = t["laughs"], t["hype"]
        if laughs >= max(hype, 1) and laughs >= 0.15 * max(t["msgs"], 1):
            category = "engraçado"
        elif hype >= 0.15 * max(t["msgs"], 1):
            category = "hype"
        elif t["clip_calls"] >= 2:
            category = "pedido do chat"
        else:
            category = "reação"
        self.last_clip_end = clip_end
        return Moment(event_start=float(o.start), event_end=float(end), peak_time=float(o.peak_t),
                      peak_score=round(o.peak_s, 2), clip_start=float(clip_start),
                      clip_end=float(clip_end), category=category,
                      stats=dict(t, mean_score=round(sum(o.scores) / len(o.scores), 2)))

    def _gc(self, target: int) -> None:
        keep_from = target - 15 * 60
        for k in [k for k in self.buckets if k < keep_from]:
            del self.buckets[k]
