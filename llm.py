# llm.py
import requests
import json
from typing import List, Dict, Optional
from config import OPENROUTER_API_KEY, OPENROUTER_MODEL

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


# ===============================
# FUNÇÃO PRINCIPAL DE CHAT
# ===============================
def chat(messages: List[Dict], temperature: float = 0.2, max_tokens: int = 500) -> str:
    """Envia lista de mensagens para o modelo no OpenRouter e retorna o texto da resposta."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost",   # pode deixar fixo
        "X-Title": "OnboardingAgent-P1",
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


# ===============================
# PARSER DE INTENÇÃO — AGENDA
# ===============================
def parse_agenda_command(user_text: str) -> Optional[Dict]:
    """
    Extrai intenção de agenda a partir de linguagem natural.
    Retorno JSON (ou None em caso de falha), no formato:
    {
      "action": "create" | "list" | "cancel" | "none",
      "descricao": "consulta cardiologista",
      "start": "2025-11-15T16:00:00",
      "end": "2025-11-15T17:00:00",
      "date": "2025-11-15",           # opcional (para list)
      "event_id": null                # opcional (para cancel)
    }

    Regras:
    - Se não souber 'end', usar +60 minutos após 'start'.
    - Datas devem estar no formato ISO local (YYYY-MM-DDTHH:MM:SS).
    - Se faltar dado essencial, definir action="none".
    """
    sys_prompt = (
        "Você é um parser de intenções de AGENDA. "
        "Responda APENAS em JSON VÁLIDO, sem texto fora do JSON. "
        "Campos esperados: action('create'|'list'|'cancel'|'none'), descricao, start, end, date, event_id. "
        "Entenda pedidos como: "
        "'marcar médico dia 15/11 às 16h' → create, descricao='médico', start/end em ISO. "
        "'o que tenho amanhã?' → list, date='RELATIVE:TOMORROW'. "
        "'cancele minha consulta das 10h' → cancel, descricao='consulta', start='RELATIVE:TODAY@10:00'. "
        "Se não conseguir entender, devolva action='none'."
    )

    fewshots = [
        ("preciso marcar cardiologista dia 15/11 às 16h",
         '{"action":"create","descricao":"consulta cardiologista","start":"2025-11-15T16:00:00","end":"2025-11-15T17:00:00","date":null,"event_id":null}'),
        ("o que tenho amanhã?",
         '{"action":"list","descricao":null,"start":null,"end":null,"date":"RELATIVE:TOMORROW","event_id":null}'),
        ("cancela minha reunião das 10h",
         '{"action":"cancel","descricao":"reunião","start":"RELATIVE:TODAY@10:00","end":null,"date":null,"event_id":null}'),
        ("quanto tá o dólar?",
         '{"action":"none","descricao":null,"start":null,"end":null,"date":null,"event_id":null}')
    ]

    messages = [{"role": "system", "content": sys_prompt}]
    for u, a in fewshots:
        messages.append({"role": "user", "content": u})
        messages.append({"role": "assistant", "content": a})
    messages.append({"role": "user", "content": user_text})

    try:
        raw = chat(messages)
    except Exception as e:
        print("!! Erro no parse_agenda_command:", e)
        return None

    raw = (raw or "").strip()
    # sanitiza e tenta extrair JSON
    try:
        data = json.loads(raw)
    except Exception:
        import re
        m = re.search(r'\{.*\}', raw, re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except Exception:
            return None

    if not isinstance(data, dict):
        return None

    # normaliza campos obrigatórios
    for k in ("action", "descricao", "start", "end", "date", "event_id"):
        data.setdefault(k, None)

    return data