# llm.py
import json
import re
import unicodedata
from typing import List, Dict, Optional
from datetime import datetime

import requests
from config import OPENROUTER_API_KEY, OPENROUTER_MODEL

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


# ===============================
# Normalização e utilidades
# ===============================
def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.strip().lower()


def _extract_user_query(messages: List[Dict]) -> str:
    """Pega o último conteúdo do usuário para usar na pontuação/overlap."""
    for m in reversed(messages):
        if m.get("role") == "user":
            return m.get("content") or ""
    return ""


def _keywords(s: str) -> List[str]:
    """Palavras significativas (sem acento), >=3 chars, minúsculas."""
    t = _norm(s)
    toks = re.findall(r"[a-z0-9]{3,}", t)
    # remove tokens muito comuns em pt
    stop = {
        "que", "com", "para", "por", "uma", "uns", "nas", "nos", "dos", "das", "sob",
        "sobre", "como", "qual", "quais", "onde", "quando", "porque", "porq",
        "temos", "existe", "aqui", "isso", "esta", "esse", "essa", "ainda",
        "tenho", "algo", "hoje", "amanha", "amanhã"
    }
    return [t for t in toks if t not in stop][:20]


def _score_answer(user_text: str, answer: str) -> float:
    """
    Heurística simples para escolher a melhor de 2 respostas:
    - Penaliza 'conhecimento_insuficiente' e respostas vazias.
    - Bônus por overlap de palavras-chave da pergunta na resposta.
    - Leve bônus por resposta objetiva (não enorme).
    """
    if not answer:
        return -1.0
    ans_n = _norm(answer)
    if "conhecimento_insuficiente" in ans_n:
        return -0.5

    q_kw = set(_keywords(user_text))
    a_kw = set(_keywords(answer))
    overlap = len(q_kw & a_kw)

    length = len(answer)
    # preferir respostas não gigantes (mas ainda informativas)
    length_penalty = 0.0
    if length > 1200:
        length_penalty = -0.3
    elif length > 800:
        length_penalty = -0.15

    return overlap * 1.0 + length_penalty


def _openrouter_call(payload: Dict) -> str:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost",
        "X-Title": "Onboardly-Assistant",
    }
    resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return (data["choices"][0]["message"]["content"] or "").strip()


# ===============================
# CHAT (determinístico + best-of)
# ===============================
def chat(
    messages: List[Dict],
    temperature: float = 0.0,
    max_tokens: int = 600,
    seed: int = 7,
    top_p: float = 0.0,
) -> str:
    """
    Envia mensagens ao modelo via OpenRouter.
    Reforços:
      - Defaults estritos (temperature/top_p=0).
      - Tenta 2 vezes (seeds distintos) e escolhe a melhor via _score_answer().
      - Mantém assinatura compatível com o projeto.
    """
    base_payload = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "temperature": float(temperature),
        "top_p": float(top_p),
        "max_tokens": int(max_tokens),
        # Nem todos respeitam seed, mas ajuda quando suportado:
        "seed": int(seed),
        # Regras gerais: preferir respostas concisas e focadas no contexto.
        "presence_penalty": 0.0,
        "frequency_penalty": 0.0,
    }

    # Primeira tentativa (seed principal)
    try:
        ans1 = _openrouter_call(base_payload)
    except Exception:
        ans1 = ""

    # Segunda tentativa (seed alternativa) — só se a primeira parece fraca
    try_second = False
    norm1 = _norm(ans1)
    if not ans1 or "conhecimento_insuficiente" in norm1 or len(ans1) < 12:
        try_second = True

    user_q = _extract_user_query(messages)
    if try_second:
        payload2 = dict(base_payload)
        payload2["seed"] = 13 if base_payload["seed"] != 13 else 17
        try:
            ans2 = _openrouter_call(payload2)
        except Exception:
            ans2 = ""

        # Escolhe a melhor por heurística de overlap com a pergunta
        s1 = _score_answer(user_q, ans1)
        s2 = _score_answer(user_q, ans2)
        return ans1 if s1 >= s2 else ans2

    return ans1


# ===============================
# PARSER DE INTENÇÃO — AGENDA
# ===============================
def parse_agenda_command(user_text: str) -> Optional[Dict]:
    """
    Tenta extrair intenção de AGENDA de linguagem natural.
    Retorna dict com chaves:
      action: "create"|"list"|"cancel"|"none"
      descricao: str|None
      start: str|None   (ISO ou token RELATIVE: ex. RELATIVE:TOMORROW@11:00)
      end:   str|None
      date:  str|None   (p/ list)
      event_id: str|None
    """

    # 1) Heurística leve para casos curtos/coloquiais (robusto e rápido)
    t = _norm(user_text)

    # Listagens simples (“o que tenho hoje/amanhã?”, “tenho algo hoje?”)
    if re.search(r"\b(tenho|agenda|compromiss|reunia|reuniao|evento|compromisso).*(amanha|hoje)\b", t) or \
       re.fullmatch(r"(o que tenho (amanha|hoje)\??|tenho algo (amanha|hoje)\??)", t):
        rel = "RELATIVE:TOMORROW" if "amanha" in t else "RELATIVE:TODAY"
        return {
            "action": "list",
            "descricao": None,
            "start": None,
            "end": None,
            "date": rel,
            "event_id": None,
        }

    # Criação rápida: “marque cardio amanhã às 11”, “preciso marcar médico às 15”
    if re.search(r"\b(marca|marque|agende|agendar|preciso marcar|marcar)\b", t) and \
       (("amanha" in t) or re.search(r"\b\d{1,2}[/-]\d{1,2}\b", t) or re.search(r"\bhoje\b", t)) and \
       re.search(r"\b(\d{1,2})(h|:\d{2})?\b", t):
        # descrição: pega após o verbo se possível (ex.: cardio, medico, dentista)
        m_desc = re.search(r"(marcar|marque|agende|agendar)\s+([a-z0-9çãõáéíóú\- ]+)", t)
        descricao = None
        if m_desc:
            descricao = m_desc.group(2)
            # corta em marcadores de data/hora para não sujar a descrição
            descricao = re.split(r"\b(amanha|hoje|as|às|@|\d{1,2}[/-]\d{1,2})\b", descricao)[0].strip()
        if not descricao:
            for kw in ["cardio", "cardiologista", "medico", "dentista", "consulta", "reuniao", "exame"]:
                if kw in t:
                    descricao = kw
                    break
        if not descricao:
            descricao = "compromisso"

        # dia relativo
        rel_day = "RELATIVE:TOMORROW" if "amanha" in t else ("RELATIVE:TODAY" if "hoje" in t else None)

        # hora
        m_h = re.search(r"\b(\d{1,2})(?:[:h](\d{2}))?\b", t)
        hh, mm = "09", "00"
        if m_h:
            hh = f"{int(m_h.group(1)):02d}"
            mm = f"{int(m_h.group(2) or '00'):02d}"

        start = f"{rel_day}@{hh}:{mm}" if rel_day else None

        # data explícita (dd/mm ou dd-mm) -> monta ISO assumindo ano atual
        m_d = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", t)
        if m_d:
            day = int(m_d.group(1))
            month = int(m_d.group(2))
            year = datetime.now().year
            start = f"{year:04d}-{month:02d}-{day:02d}T{hh}:{mm}:00"

        return {
            "action": "create",
            "descricao": descricao,
            "start": start,
            "end": None,  # backend assume +60m se vazio
            "date": None,
            "event_id": None,
        }

    # Cancelamentos simples
    if re.search(r"\b(cancelar|cancela|remover|apaga)\b.*\b(reunia|reuniao|consulta|evento|compromisso)\b", t):
        m_h = re.search(r"\b(\d{1,2})(?:[:h](\d{2}))?\b", t)
        start = f"RELATIVE:TODAY@{int(m_h.group(1)):02d}:{int(m_h.group(2) or '00'):02d}" if m_h else None
        return {
            "action": "cancel",
            "descricao": "compromisso",
            "start": start,
            "end": None,
            "date": None,
            "event_id": None,
        }

    # 2) Fallback via LLM (somente JSON, determinístico)
    sys_prompt = (
        "Você é um PARSER DE AGENDA. Responda APENAS com um JSON VÁLIDO, sem explicações. "
        "Campos: action('create'|'list'|'cancel'|'none'), descricao, start, end, date, event_id. "
        "Datas/horas: use ISO local (YYYY-MM-DDTHH:MM:SS) quando souber; "
        "para referências relativas use tokens: RELATIVE:TODAY, RELATIVE:TOMORROW e, se houver hora, '@HH:MM' (ex.: RELATIVE:TODAY@10:00). "
        "Se não entender, retorne action='none' e os demais campos null."
    )

    fewshots = [
        ("preciso marcar cardiologista dia 15/11 às 16h",
         '{"action":"create","descricao":"consulta cardiologista","start":"2025-11-15T16:00:00","end":null,"date":null,"event_id":null}'),
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
        raw = chat(messages, temperature=0.0, top_p=0.0, max_tokens=220, seed=11)
    except Exception as e:
        print("!! Erro no parse_agenda_command:", e)
        return None

    raw = (raw or "").strip()

    # Extrai JSON — tolerante a lixo ao redor
    try:
        data = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except Exception:
            return None

    if not isinstance(data, dict):
        return None

    for k in ("action", "descricao", "start", "end", "date", "event_id"):
        data.setdefault(k, None)

    act = _norm(data.get("action"))
    if act not in {"create", "list", "cancel", "none"}:
        data["action"] = "none"

    return data