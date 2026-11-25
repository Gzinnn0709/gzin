# ✂️ GabeCuts — cortes automáticos e inteligentes da live do Gabepeixe

O GabeCuts fica de olho no canal do Gabepeixe na **Twitch** ou na **Kick** (ou nas duas, em multistream). Quando a live abre, ele começa a gravar e lê o chat em tempo real. A partir do chat e do áudio, ele encontra os melhores momentos e gera os cortes prontos para postar:

- **16:9** (YouTube) e **9:16** (TikTok, Shorts, Reels), com fundo desfocado, título no topo e legenda queimada
- **título, descrição e hashtags** escritos por IA (Claude), que também **descarta** picos sem conteúdo e **ajusta o início e o fim** de cada corte
- **painel web** para assistir, aprovar, rejeitar e baixar os cortes
- **aviso no Discord** quando sai um corte novo (opcional)

```
 Twitch/Kick ──► streamlink ou HLS ──► ffmpeg ──► buffer circular (segmentos de 10s)
   │                                                 │
   └─► chat Twitch (IRC) + chat Kick (Pusher)        ├─► volume do áudio por segundo
             │                              │
             ▼                              ▼
        ┌────────────── Detector de momentos ──────────────┐
        │ score/s = picos do chat (z-score) × risada/hype   │
        │          + pedidos de "clipa" + picos de áudio    │
        └───────────────────────┬──────────────────────────┘
                                ▼
          corte bruto ─► transcrição (Whisper) ─► IA (Claude)
                                ▼
           corte final 16:9 + 9:16 legendado ─► SQLite ─► painel / Discord
```

## Como o "inteligente" funciona

1. **Linha de base dinâmica.** O sistema não usa números fixos como "50 mensagens por segundo". Ele aprende o ritmo normal do chat e do áudio nos últimos ~5 minutos e mede o quanto cada segundo foge disso (z-score). Assim funciona igual com o chat calmo do começo da live ou com 40 mil pessoas num pico.
2. **O que o chat diz conta.** Uma onda de `KKKKKK`, `KEKW` ou `ICANT` pesa como risada. `POG`, `CARALHO`, `MDS` e `NOOO` pesam como hype. Pedidos de "clipa" e "corta" somam pontos, e uma onda de copypasta ou emote repetido reforça o sinal.
3. **Uma pessoa não decide sozinha.** Cada usuário conta no máximo 2 mensagens por janela, então um spammer sozinho não gera corte.
4. **O áudio também conta.** Gritos, risadas e explosões aparecem como picos de volume.
5. **O corte começa antes da reação.** O chat reage alguns segundos depois do que aconteceu, por isso o corte começa `chat_delay + pre_roll` antes do pico e inclui o contexto.
6. **A IA faz a curadoria.** O Claude lê a transcrição e o chat do trecho e decide se o corte vale a pena. Ele dá uma nota de 0 a 100, escreve título, descrição e hashtags e sugere um recorte mais justo. Cortes com nota abaixo de `ai_min_score` ficam marcados como `descartado` e não viram vídeo vertical.

Sem `ANTHROPIC_API_KEY`, o sistema funciona em modo heurístico: corta tudo que o detector encontra, com títulos genéricos.

## Instalação

Requisitos: Python 3.10+, `ffmpeg` no PATH e uma máquina que fique ligada durante a live.

```bash
git clone <este repo> && cd gzin
python -m venv .venv && source .venv/bin/activate
pip install -e ".[whisper]"        # sem legenda/transcrição: pip install -e .
cp gabecuts.example.toml gabecuts.toml
export ANTHROPIC_API_KEY=sk-ant-...  # opcional, mas é o que deixa os cortes "inteligentes"
```

### Com Docker

```bash
cp gabecuts.example.toml gabecuts.toml
ANTHROPIC_API_KEY=sk-ant-... docker compose up -d --build
```

## Windows: rodar em segundo plano (com dois cliques)

Depois de instalado, abra a pasta do projeto no Explorer e clique duas vezes no arquivo:

| arquivo | o que faz |
|---|---|
| `iniciar_cortes.bat` | **um clique faz as duas coisas**: abre a live no navegador (para ganhar pontos) e liga os cortes minimizados, com prioridade baixa. |
| `parar_cortes.bat` | para tudo |
| `atualizar.bat` | baixa a versão mais nova e atualiza as dependências |

A plataforma (Kick ou Twitch) fica na linha `set PLATAFORMA=kick` do `iniciar_cortes.bat`. O log fica em `data\gabecuts.log`.

Para não pesar na live aberta no navegador:
- **Gravação em 720p** (`record_max_height`). Usa metade da internet e da CPU de 1080p.
- **Prioridade baixa** (`low_priority`). O Windows dá preferência ao navegador.
- **Cortes codificados com 2 núcleos** (`encode_threads`).

## Uso

```bash
gabecuts live              # espera a live abrir, grava, detecta e corta; painel em http://localhost:8000
gabecuts live --no-web     # sem painel
gabecuts web               # só o painel (para revisar cortes já feitos)
gabecuts vod live.mp4 --chat chat.json   # processa uma live antiga
gabecuts --channel outro_canal live      # funciona com qualquer canal
gabecuts --platform kick live            # live na Kick
gabecuts --platform kick --chat-sources kick,twitch live   # multistream: vídeo da Kick, chat das duas
```

### Kick

Use `platform = "kick"` no `gabecuts.toml` ou `--platform kick` na linha de comando.

- **Chat:** chega em tempo real pelo WebSocket público da Kick (Pusher), sem login. Os emotes (`[emote:123:KEKW]`) viram texto, então risada e hype são detectados igual à Twitch.
- **Vídeo:** o GabeCuts pega a URL HLS da live na API da Kick e grava direto com o ffmpeg. Se a API falhar, ele usa o plugin de Kick do streamlink, que precisa do Chrome ou Chromium instalado para passar pelo desafio do Cloudflare.
- **Cloudflare:** a API da Kick (`kick.com/api/v2/channels/<canal>`) fica atrás do Cloudflare. Se aparecer erro de bloqueio:
  1. Instale o `curl_cffi`, que imita o Chrome: `pip install -e ".[kick]"`.
  2. Se ainda falhar, abra `https://kick.com/api/v2/channels/gabepeixe` no navegador, procure `"chatroom":{"id":...}` e coloque esse número em `kick_chatroom_id`. Isso destrava o chat. O vídeo continua vindo pelo streamlink.
- **Multistream:** com `chat_sources = "twitch,kick"`, as mensagens das duas plataformas entram no mesmo detector. O mesmo nick em plataformas diferentes conta como duas pessoas. O vídeo vem só de `platform`.
- **Nomes diferentes:** se o canal tiver nome diferente em cada site, use `twitch_channel` e `kick_channel`.

### Modo VOD (lives antigas)

1. Baixe o vídeo e o chat com o [TwitchDownloader](https://github.com/lay295/TwitchDownloader), por exemplo `TwitchDownloaderCLI videodownload` e `chatdownload` com saída `.json`.
2. Rode `gabecuts vod video.mp4 --chat chat.json`.

Para a Kick, o chat pode ser uma lista JSON de mensagens no formato da Kick (`created_at`, `content`, `sender`). Passe `--vod-start 2026-11-25T20:00:00-03:00` com o horário em que o vídeo começa, para alinhar o chat ao vídeo.

O chat também pode ser um JSONL simples, com uma linha por mensagem: `{"t": 123.4, "user": "fulano", "text": "KKKK"}`. Sem chat, a detecção usa só o áudio e fica menos precisa.

### Ver os cortes no site (aba escondida, só para o admin)

Os cortes prontos podem ir sozinhos para a aba **Administração → ✂️ Cortes** do site da loja.
Lá você assiste, baixa e copia a legenda de qualquer lugar, inclusive pelo celular.

1. No site, entre com a conta de admin e vá em **Administração → ✂️ Cortes → 🔗 Conectar GabeCuts**. O código já sai copiado.
2. No painel do GabeCuts (http://localhost:8000), clique em **🌐 Site**, cole o código e clique em **Conectar**.

A partir daí, cada corte pronto (exceto os descartados) é enviado em segundo plano. O código fica em `data/site.json`, que não vai para o git.

### Saída

Cada corte fica numa pasta própria em `data/clips/AAAAMMDD_HHMMSS_categoria/`:

| arquivo | conteúdo |
|---|---|
| `corte.mp4` | corte 16:9 |
| `vertical.mp4` | 1080×1920 com título e legenda |
| `legenda.srt` | legenda (se o Whisper estiver instalado) |
| `thumb.jpg` | miniatura do pico |
| `info.json` | título, descrição, hashtags, nota, motivo, transcrição e métricas |

## Ajuste fino

As opções estão em `gabecuts.example.toml`, e cada uma também pode ser definida por variável de ambiente (`GABECUTS_THRESHOLD=5`).

- **Frequência automática.** `target_clips_per_hour` (padrão 12). Se a live fica calma e passa muito tempo sem corte, o limiar desce sozinho até `threshold_floor` e pega o melhor momento do período. Quando sai um corte, ele volta ao normal.
- **Cortes demais?** Aumente `threshold` (ex.: 4–5), diminua `target_clips_per_hour` ou aumente `ai_min_score`.
- **Cortes de menos?** Diminua `threshold` (ex.: 2.5) ou aumente `target_clips_per_hour` (ex.: 20).
- **Corte manual.** O botão **✂️ Cortar agora** no painel corta os últimos 45 segundos.
- **O corte começa tarde ou cedo demais?** Ajuste `chat_delay` e `pre_roll`.
- **Live com muito barulho de jogo?** Diminua `w_audio`.

O painel mostra um gráfico ao vivo do score com a linha do limiar, o que ajuda a calibrar.

## Testes

```bash
pip install -e ".[dev]"
pytest
```

Os testes cobrem o parser do chat da Twitch e da Kick, a junção de chats em multistream, a classificação das mensagens, o detector (risada coletiva, spammer sozinho, pico de áudio, aquecimento), a revisão da IA (simulada), a geração do vídeo vertical com legenda e um teste ponta a ponta do modo VOD com vídeo sintético.

## Observações

- O chat é lido de forma anônima nas duas plataformas, sem conta nem token.
- A integração com a Kick usa endpoints não oficiais, os mesmos do site. Se a Kick mudar a chave do Pusher, o GabeCuts tenta descobrir a nova sozinho. Se não conseguir, defina `kick_pusher_key`.
- Respeite os termos da Twitch e da Kick e as regras de cortes do próprio Gabepeixe antes de repostar e monetizar o conteúdo.
- A transcrição roda localmente. Numa GPU, o modelo `small` acompanha a live com folga. Só com CPU, use `base` ou `tiny`.
