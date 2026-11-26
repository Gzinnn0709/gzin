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

0. **Clipes da Kick:** durante a live, a cada 30 s o GabeCuts olha os clipes que os espectadores criam no canal. Quando várias pessoas clipam o mesmo momento, isso vira um sinal forte, e o corte sai mesmo que o chat não tenha explodido.

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

## Windows: 100% automático

Rode **uma vez** o **`ligar_inicio_automatico.bat`** (clique duas vezes no Explorer). A partir daí:

1. **Quando o Windows inicia, o GabeCuts liga sozinho,** minimizado e com prioridade baixa. Antes, ele baixa a versão nova, se houver.
2. **Fica esperando a live.** Quando ela começa:
   - abre a live no navegador, para ganhar os pontos do canal;
   - começa a gravar e cortar;
   - impede o notebook de suspender enquanto grava.
3. **Cada corte pronto vai sozinho para a página `/cortes` do site**, se estiver conectado.

| arquivo | o que faz |
|---|---|
| `ligar_inicio_automatico.bat` | liga o início automático (só uma vez) e já liga o GabeCuts agora |
| `desligar_inicio_automatico.bat` | desfaz o início automático |
| `iniciar_cortes.bat` | liga na mão (minimizado) |
| `parar_cortes.bat` | para tudo agora |
| `atualizar.bat` | atualiza na mão. O início automático já atualiza sozinho. |

Para não abrir a live no navegador sozinho, coloque `open_live_in_browser = false` no `gabecuts.toml`.

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

### Ver os cortes no site (página `/cortes`, só para o admin)

Os cortes prontos podem ir sozinhos para a página **`/cortes`** do site da loja. Ela funciona como este painel: mostra se a live está sendo gravada e tem os botões de assistir, baixar, copiar a legenda, apagar e **✂️ Cortar agora**. Funciona de qualquer lugar, inclusive pelo celular.

Tudo cabe nos planos grátis do Render e do Supabase:
- **Só uma versão leve de cada corte vai para o site:** 720p, uns 2 a 5 MB por corte.
- **O site guarda até 300 MB e apaga os mais antigos sozinho.** No notebook, todos os cortes continuam.
- **O GabeCuts avisa o site a cada minuto durante a live.** Fora dela, avisa a cada 30 minutos, e assim o Render grátis pode dormir.

1. No site, entre com a conta de admin e abra **`/cortes`** (ou **Administração → ✂️ Cortes**). Clique em **🔗 Conectar GabeCuts**: o código já sai copiado.
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

## Estilo TikTok

Os cortes verticais seguem o que funciona nos cortes de streamer que viralizam:

- **Cortes com história:** de 40 s a 2 min (o alvo é 50 s a 1 min 30 s), com contexto antes do momento e a reação completa. Momentos a menos de 25 s um do outro viram um corte só.
- **Texto de gancho no topo (desligado por padrão):** com `hook_text = true`, aparecem 2 a 5 palavras em caixa alta no topo, por exemplo "ELE NÃO ESPERAVA ISSO". O título continua no site e no painel.
- **Legenda palavra por palavra:** caixa alta, fonte grossa e a palavra falada em amarelo. É gerada em `legenda.ass`, e a `legenda.srt` continua sendo gerada.
- **Ritmo (desligado por padrão):** `cut_silences = true` tira os silêncios parados. Em vídeo de jogo, os cortes no meio da conversa parecem "travadas", por isso vem desligado.
- **Zoom rápido** na hora da reação.
- **Música opcional** (`music_dir`): fica baixinha e abaixa sozinha quando alguém fala. O recomendado é pôr o **som em alta pelo próprio app do TikTok** na hora de postar: ajuda o alcance e evita problema de direitos autorais.

Tudo isso pode ser ligado ou desligado no `gabecuts.toml` (`cut_silences`, `zoom_on_reaction`, `burn_subtitles`).

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
