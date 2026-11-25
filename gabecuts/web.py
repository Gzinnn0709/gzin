"""Painel web para revisar, aprovar e baixar os cortes."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import Config
from .db import STATUSES, ClipDB


class StatusIn(BaseModel):
    status: str


class SiteIn(BaseModel):
    url: str = ""
    codigo: str


class EditIn(BaseModel):
    title: str | None = None
    description: str | None = None


def create_app(cfg: Config, runner=None) -> FastAPI:
    cfg.ensure_dirs()
    db = runner.db if runner else ClipDB(cfg.db_path)
    app = FastAPI(title="GabeCuts")
    app.mount("/media", StaticFiles(directory=str(cfg.clips_dir)), name="media")

    @app.get("/api/clips")
    def clips(status: str | None = None, limit: int = 100):
        return db.list(status, limit)

    @app.get("/api/clips/{clip_id}")
    def clip(clip_id: int):
        c = db.get(clip_id)
        if not c:
            raise HTTPException(404, "corte não encontrado")
        return c

    @app.post("/api/clips/{clip_id}/status")
    def set_status(clip_id: int, body: StatusIn):
        if body.status not in STATUSES:
            raise HTTPException(400, f"status deve ser um de {STATUSES}")
        if not db.get(clip_id):
            raise HTTPException(404, "corte não encontrado")
        db.update(clip_id, status=body.status)
        return {"ok": True}

    @app.patch("/api/clips/{clip_id}")
    def edit(clip_id: int, body: EditIn):
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        if changes:
            db.update(clip_id, **changes)
        return {"ok": True}

    @app.post("/api/clip-now")
    async def clip_now(seconds: float = 45):
        if not runner:
            raise HTTPException(400, "corte manual só funciona com o 'gabecuts live' rodando")
        if not runner.live:
            raise HTTPException(409, "a live não está sendo gravada agora")
        return {"id": runner.clip_now(max(10.0, min(seconds, 120.0)))}

    @app.get("/api/site")
    def site_status():
        from . import site
        if runner:
            return runner.site.status()
        c = site.carregar(cfg)
        return {"url": c["url"], "conectado": bool(c.get("codigo")), "erro": "", "pendentes": None}

    @app.post("/api/site")
    def site_conectar(body: SiteIn):
        from . import site
        url = site.normalizar_url(body.url)
        codigo = body.codigo.strip()
        if not codigo:
            site.salvar(cfg, url, "")
            return {"ok": True, "mensagem": "desconectado"}
        ok, msg = site.conferir(url, codigo)
        if not ok:
            raise HTTPException(400, msg)
        site.salvar(cfg, url, codigo)
        return {"ok": True, "mensagem": "Conectado! Os cortes prontos vão aparecer na aba Cortes do site."}

    @app.get("/api/status")
    def status():
        if not runner:
            return {"mode": "painel", "channel": cfg.channel}
        recent = list(runner.detector.recent_scores)[-120:]
        thr = recent[-1][2].get("thr", cfg.threshold) if recent else cfg.threshold
        return {"mode": "ao vivo", "channel": cfg.channel, "live": runner.live,
                "recording": bool(runner.recorder and runner.recorder.running()),
                "queue": runner.queue.qsize(), "threshold": thr, **runner.stats,
                "scores": [[t, s] for t, s, _ in recent]}

    @app.get("/", response_class=HTMLResponse)
    def index():
        label = " + ".join(cfg.chats) if len(cfg.chats) > 1 else cfg.platform
        return PAGE.replace("{{CHANNEL}}", f"{cfg.channel_for(cfg.platform)} · {label}")

    return app


PAGE = """<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GabeCuts</title>
<style>
:root{--bg:#0e0e10;--card:#18181b;--fg:#efeff1;--mut:#adadb8;--acc:#9147ff;--ok:#00c853;--bad:#eb0400}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px system-ui,sans-serif}
header{display:flex;gap:16px;align-items:center;padding:14px 20px;border-bottom:1px solid #2a2a2d;flex-wrap:wrap}
h1{font-size:20px;margin:0}.pill{padding:3px 10px;border-radius:99px;background:#2a2a2d;color:var(--mut);font-size:13px}
.live{background:var(--bad);color:#fff}
canvas{width:100%;height:70px;display:block;background:var(--card);border-radius:8px}
main{padding:20px;display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
.card{background:var(--card);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
video{width:100%;aspect-ratio:16/9;background:#000}
.body{padding:12px;display:flex;flex-direction:column;gap:6px;flex:1}
.title{font-weight:700;font-size:16px}.meta{color:var(--mut);font-size:13px}
.tags{color:var(--acc);font-size:13px}.btns{display:flex;gap:6px;flex-wrap:wrap;margin-top:auto}
button,a.btn{border:0;border-radius:6px;padding:6px 10px;cursor:pointer;background:#2a2a2d;color:var(--fg);font-size:13px;text-decoration:none}
.ap{background:var(--ok);color:#000}.wait{aspect-ratio:16/9;display:flex;align-items:center;justify-content:center;background:#000;color:var(--mut);text-align:center;padding:12px}
.err{color:#ff6b6b;font-size:13px;word-break:break-word}.rj{background:var(--bad);color:#fff}
select{background:#2a2a2d;color:var(--fg);border:0;border-radius:6px;padding:6px}
dialog{background:var(--card);color:var(--fg);border:1px solid #333;border-radius:12px;max-width:min(92vw,460px)}
dialog::backdrop{background:#000a}dialog input{background:#0e0e10;color:var(--fg);border:1px solid #444;border-radius:6px;padding:8px;font-size:14px}
#graph{padding:0 20px}
</style></head><body>
<header><h1>✂️ GabeCuts</h1><span class="pill">{{CHANNEL}}</span><span id="st" class="pill">…</span>
<select id="f"><option value="">todos</option><option>pendente</option><option>aprovado</option>
<option>postado</option><option>rejeitado</option><option>descartado</option><option>processando</option>
<option>na fila</option><option>erro</option></select>
<button class="ap" id="now" title="Corta os últimos 45 segundos da live">✂️ Cortar agora</button>
<button id="sitebtn" title="Enviar os cortes para a aba Cortes do site">🌐 Site</button><span id="sitest" class="pill"></span></header>
<dialog id="sitedlg"><form method="dialog" id="siteform">
<h3 style="margin-top:0">🌐 Enviar cortes para o site</h3>
<p class="meta">No site, entre com sua conta de admin → <b>Administração → ✂️ Cortes → 🔗 Conectar GabeCuts</b>. Copie o código e cole aqui.</p>
<label class="meta">Endereço do site<br><input id="siteurl" style="width:100%"></label><br><br>
<label class="meta">Código<br><input id="sitecod" placeholder="gc_..." style="width:100%" autocomplete="off"></label>
<p id="sitemsg" class="meta"></p>
<div class="btns"><button class="ap" id="sitesave" type="button">Conectar</button><button value="cancel">Fechar</button></div>
</form></dialog>
<div id="graph"><canvas id="cv" width="1200" height="70"></canvas></div>
<main id="list"></main>
<script>
const $=s=>document.querySelector(s);
const esc=s=>(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function load(){
  const f=$('#f').value; const r=await fetch('/api/clips'+(f?'?status='+f:'')); const txt=await r.text();
  if(txt===window._last)return; window._last=txt; const cs=JSON.parse(txt);
  $('#list').innerHTML=cs.map(c=>{const busy=c.status==='na fila'||c.status==='processando';const f=c.file_v||c.file_h;
   const media=f?`<video controls preload="none" poster="/media/${c.thumb||''}" src="/media/${f}"></video>`:
     `<div class="wait">${c.status==='erro'?'❌ erro':'⏳ '+esc(c.description)}</div>`;
   return `<div class="card" id="clip-${c.id}">${media}
   <div class="body"><div class="title">#${c.id} · ${esc(c.title)}</div>
   <div class="meta">${new Date(c.created_at*1000).toLocaleString('pt-BR')} · ${esc(c.category)} · nota ${c.ai_score??'-'} · ${(c.t_end-c.t_start).toFixed(0)}s · <b>${c.status}</b>${c.by_ai?' · 🤖':''}${c.enviado?' · ☁️ no site':''}</div>
   <div class="meta">${busy?'':esc(c.description)}</div>
   ${c.status==='erro'?`<div class="err">${esc(c.reason)}</div>`:''}
   <div class="tags">${(c.hashtags||[]).map(h=>'#'+esc(h)).join(' ')}</div>
   ${busy||c.status==='erro'?'':`<div class="btns"><button class="ap" onclick="st(${c.id},'aprovado')">Aprovar</button>
   <button class="rj" onclick="st(${c.id},'rejeitado')">Rejeitar</button>
   <button onclick="st(${c.id},'postado')">Postado</button>
   ${c.file_h?`<a class="btn" href="/media/${c.file_h}" download>⬇ 16:9</a>`:''}
   ${c.file_v?`<a class="btn" href="/media/${c.file_v}" download>⬇ 9:16</a>`:''}
   <button onclick="cp(${c.id})">Copiar legenda</button></div>`}</div></div>`}).join('')||'<p>Nenhum corte ainda. Quando o chat explodir, o momento aparece aqui na hora — ou clique em ✂️ Cortar agora.</p>';
  window._busy=cs.some(c=>c.status==='na fila'||c.status==='processando');
  window._cs=cs;
}
async function st(id,s){await fetch(`/api/clips/${id}/status`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:s})});load()}
function cp(id){const c=_cs.find(x=>x.id===id);navigator.clipboard.writeText(`${c.title}\\n\\n${c.description}\\n\\n${(c.hashtags||[]).map(h=>'#'+h).join(' ')}`)}
async function status(){
  try{const s=await (await fetch('/api/status')).json();
  $('#st').textContent=s.mode==='ao vivo'?(s.live?(s.recording?'🔴 AO VIVO · gravando':'🟠 AO VIVO · gravação reiniciando'):'⚫ offline')+` · ${s.messages} msgs · ${s.moments} momentos · ${s.queue} na fila`:'painel';
  $('#st').className='pill'+(s.live?' live':'');
  const cv=$('#cv'),x=cv.getContext('2d');x.clearRect(0,0,cv.width,cv.height);
  if(s.scores&&s.scores.length){const mx=Math.max(s.threshold*1.5,...s.scores.map(p=>p[1]));const w=cv.width/s.scores.length;
   x.fillStyle='#9147ff';s.scores.forEach((p,i)=>{const h=p[1]/mx*cv.height;x.fillStyle=p[1]>=s.threshold?'#eb0400':'#9147ff';x.fillRect(i*w,cv.height-h,w-1,h)});
   const ty=cv.height-s.threshold/mx*cv.height;x.fillStyle='#ffffff55';x.fillRect(0,ty,cv.width,1)}
  else $('#graph').style.display='none';}catch(e){}
}
$('#now').onclick=async()=>{const b=$('#now');b.disabled=true;const r=await fetch('/api/clip-now',{method:'POST'});
 const j=await r.json();b.disabled=false;if(!r.ok){alert(j.detail||'erro');return}load()};
async function site(){try{const s=await (await fetch('/api/site')).json();window._site=s;
  $('#sitest').textContent=s.conectado?(s.erro?'🌐 erro no envio':'🌐 conectado'+(s.pendentes?` · enviando ${s.pendentes}`:'')):'🌐 site desligado';
  $('#sitest').title=s.erro||s.url;}catch(e){}}
$('#sitebtn').onclick=()=>{const s=window._site||{};$('#siteurl').value=s.url||'https://www.aamcell.com.br';$('#sitecod').value='';
  $('#sitemsg').textContent=s.conectado?('Já conectado. '+(s.erro?'Último erro: '+s.erro:'')):'';$('#sitedlg').showModal()};
$('#sitesave').onclick=async()=>{const b=$('#sitesave');b.disabled=true;$('#sitemsg').textContent='Conferindo o código…';
  const r=await fetch('/api/site',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:$('#siteurl').value,codigo:$('#sitecod').value})});
  const j=await r.json();b.disabled=false;$('#sitemsg').textContent=r.ok?'✅ '+j.mensagem:'❌ '+(j.detail||'erro');site()};
site();setInterval(site,15000);
let tick=0;$('#f').onchange=()=>{window._last=null;load()};load();status();setInterval(status,3000);
setInterval(()=>{tick++;if(window._busy||tick%5===0)load()},4000);
</script></body></html>"""
