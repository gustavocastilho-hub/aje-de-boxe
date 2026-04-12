"""
Rotas de observabilidade/logs para painel externo.
Prefixo: /ajeboxe/logs/
"""
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.services.redis_service import get_redis

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ajeboxe")

# Sufixo base das chaves Redis deste projeto
_KEY_SUFFIX = "--aje"


def _phone_from_lead_key(key: str) -> str:
    return key.replace(f"{_KEY_SUFFIX}:lead", "")


def _phone_from_history_key(key: str) -> str:
    return key.replace(f"{_KEY_SUFFIX}:history", "")


@router.get("/logs/leads")
async def logs_leads():
    """Retorna todos os leads com dados de CRM."""
    r = await get_redis()

    lead_keys = await r.keys(f"*{_KEY_SUFFIX}:lead")
    history_keys = await r.keys(f"*{_KEY_SUFFIX}:history")

    phones: set[str] = set()
    for k in lead_keys:
        phones.add(_phone_from_lead_key(k))
    for k in history_keys:
        phones.add(_phone_from_history_key(k))

    leads = []
    for phone in sorted(phones):
        crm = await r.hgetall(f"{phone}{_KEY_SUFFIX}:lead")
        msg_count = await r.llen(f"{phone}{_KEY_SUFFIX}:history")
        has_followup = await r.exists(f"{phone}{_KEY_SUFFIX}:followup:active") == 1
        leads.append({
            "phone": phone,
            "nome": crm.get("name", ""),
            "nicho": crm.get("nicho", ""),
            "resumo": crm.get("resumo", ""),
            "event_id": crm.get("event_id", ""),
            "msg_count": msg_count,
            "has_followup": has_followup,
        })

    leads.sort(key=lambda x: x["msg_count"], reverse=True)
    return leads


@router.get("/logs/history/{phone}")
async def logs_history(phone: str):
    """Retorna o histórico de mensagens de um lead."""
    r = await get_redis()

    raw = await r.lrange(f"{phone}{_KEY_SUFFIX}:history", 0, -1)
    messages = []
    for item in raw:
        try:
            entry = json.loads(item)
            messages.append({
                "role": entry.get("type", ""),
                "content": entry.get("data", {}).get("content", ""),
            })
        except Exception:
            pass
    return messages


@router.get("/logs/events")
async def logs_events(limit: int = 100):
    """Retorna os últimos N eventos de execução do worker."""
    r = await get_redis()

    raw = await r.lrange("aje:logs", 0, limit - 1)
    events = []
    for item in raw:
        try:
            events.append(json.loads(item))
        except Exception:
            pass
    return events


@router.get("/painel", response_class=HTMLResponse)
async def painel():
    """Painel de logs em tempo real."""
    html = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>AJE DE BOXE — Painel</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #111; color: #e0e0e0; font-family: 'Courier New', monospace; padding: 20px; }
  h1 { color: #e67e22; font-size: 18px; margin-bottom: 4px; }
  #status { font-size: 11px; color: #666; margin-bottom: 16px; }
  .event { border: 1px solid #2a2a2a; border-radius: 6px; padding: 10px 14px; margin-bottom: 10px; background: #1a1a1a; }
  .event-header { color: #555; font-size: 11px; margin-bottom: 8px; border-bottom: 1px solid #2a2a2a; padding-bottom: 5px; }
  .event-header .phone { color: #3498db; font-weight: bold; }
  .log-line { margin: 3px 0; font-size: 12px; line-height: 1.5; }
  .new-badge { display: inline-block; background: #27ae60; color: #fff; font-size: 10px; padding: 1px 5px; border-radius: 3px; margin-left: 8px; }
</style>
</head>
<body>
<h1>🥊 AJE DE BOXE — Execuções</h1>
<div id="status">Carregando...</div>
<div id="events"></div>
<script>
let lastTs = null;

function fmt(ts) {
  return new Date(ts * 1000).toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo' });
}

async function refresh() {
  try {
    const res = await fetch('/ajeboxe/logs/events?limit=50');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const events = await res.json();
    const container = document.getElementById('events');
    const status = document.getElementById('status');

    if (!events.length) {
      status.textContent = 'Nenhuma execução registrada ainda.';
      return;
    }

    const newest = events[0].ts;
    const isNew = newest !== lastTs;

    if (isNew) {
      container.innerHTML = '';
      for (let i = 0; i < events.length; i++) {
        const ev = events[i];
        const div = document.createElement('div');
        div.className = 'event';

        const header = document.createElement('div');
        header.className = 'event-header';
        header.innerHTML = fmt(ev.ts) + ' &nbsp;—&nbsp; <span class="phone">' + (ev.phone || '') + '</span>'
          + (i === 0 && lastTs !== null ? '<span class="new-badge">NOVO</span>' : '');
        div.appendChild(header);

        for (const line of (ev.lines || [])) {
          const p = document.createElement('p');
          p.className = 'log-line';
          p.innerHTML = line;
          div.appendChild(p);
        }
        container.appendChild(div);
      }
      lastTs = newest;
    }

    const now = new Date().toLocaleTimeString('pt-BR');
    status.textContent = 'Atualizado: ' + now + ' · ' + events.length + ' execução(ões)';
  } catch (e) {
    document.getElementById('status').textContent = 'Erro: ' + e.message;
  }
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""
    return html
