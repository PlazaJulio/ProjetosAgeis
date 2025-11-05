# app.py
from __future__ import annotations
from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, date
import html
import json
import time
import traceback
import unicodedata
import pymysql

from config import PORT, DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
from pdf_ingest import load_knowledge_text
from llm import chat, parse_agenda_command
from db import (
    get_user_by_whatsapp,
    add_event,                        # assinatura: add_event(user_id, descricao, starts_at, ends_at) -> int/bool
    find_conflicts,                   # assinatura: find_conflicts(user_id, starts_at, ends_at) -> List[...]
    list_events_for_user_on_date,     # assinatura: list_events_for_user_on_date(user_id, day: date|str) -> List[...]
)

# =========================================================
# Base (PDF -> texto)
# =========================================================
RAW_KNOWLEDGE_TEXT = load_knowledge_text()

def _normalize_base(t: str) -> str:
    lines = [ln.strip() for ln in (t or "").splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)

KNOWLEDGE_TEXT = _normalize_base(RAW_KNOWLEDGE_TEXT)
BASE_IS_EMPTY = (len(KNOWLEDGE_TEXT.strip()) == 0)

# ---------- Prompt do sistema ----------
with open("./prompts/system_prompt.txt", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

app = Flask(
    __name__,
    static_folder="static",
    static_url_path="/static",
)
CORS(
    app,
    resources={
        r"/auth/*": {"origins": "*"},
        r"/chat": {"origins": "*"},
        r"/webhook/*": {"origins": "*"},
    },
)

# =========================================================
# Helpers
# =========================================================
def clamp(s: str, max_chars: int = 1500) -> str:
    s = (s or "").strip()
    return s[:max_chars]

def xml_escape(s: str) -> str:
    return html.escape(s or "", quote=True)

def twiml_text(text: str) -> Response:
    safe = xml_escape(clamp(text))
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe}</Message></Response>'
    resp = Response(xml)
    resp.headers["Content-Type"] = "text/xml; charset=utf-8"
    return resp

def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.lower()

def _db_conn():
    return pymysql.connect(
        host=DB_HOST,
        port=int(DB_PORT or 3306),
        user=DB_USER,
        password=DB_PASSWORD or "",
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )

def ask_llm_with_deadline(messages, deadline_sec: int = 10):
    start = time.time()
    try:
        ans = chat(messages)
    except Exception:
        print("!! LLM ERROR:\n", traceback.format_exc())
        return None
    if time.time() - start > deadline_sec:
        print("!! LLM deadline estourado (soft timeout).")
        return None
    ans = (ans or "").strip()
    return ans or None

FALLBACK_NO_BASE = "No momento, o FAQ não está carregado."
NOT_REGISTERED_MSG = (
    "Sou um assistente interno e atendo apenas assuntos da empresa. "
    "Não encontrei seu cadastro para este número. Por favor, peça habilitação ao RH com seu nome completo e e-mail corporativo."
)
OUT_OF_SCOPE_MSG = (
    "Sou um assistente interno e só consigo ajudar com informações da empresa (onboarding, setores, processos e contatos)."
)
DONT_KNOW_YET_MSG = (
    "Essa pergunta está no contexto da empresa, mas ainda não tenho essa resposta no manual. "
    "Vamos aprimorar os conhecimentos com essa dúvida. Sua dúvida foi atendida? (responda 'não' para receber o contato do responsável)"
)

# ---------- Pequena memória de confirmação por número ----------
STATE: Dict[str, Dict[str, Any]] = {}  # { from_num: {"awaiting": True, "role_like": str|None, "ts": float} }

def set_pending(from_num: str, role_like: str | None):
    STATE[from_num] = {"awaiting": True, "role_like": role_like, "ts": time.time()}

def pop_pending(from_num: str):
    return STATE.pop(from_num, None)

def get_pending(from_num: str):
    st = STATE.get(from_num)
    if st and time.time() - st.get("ts", 0) > 600:
        STATE.pop(from_num, None)
        return None
    return st

def is_negative_answer(text: str) -> bool:
    q = _norm(text)
    return any(token in q for token in ["nao", "não", "nao foi", "não foi", "nao ajudou", "não ajudou", "negativo"])

# =========================================================
# Agenda — utilitários
# =========================================================
def parse_iso(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        dt_str = dt_str.replace(" ", "T")
        return datetime.fromisoformat(dt_str)
    except Exception:
        return None

def resolve_relative(token: Optional[str]) -> Optional[datetime]:
    """
    Tokens como RELATIVE:TODAY@10:00 ou RELATIVE:TOMORROW.
    """
    if not token or not isinstance(token, str):
        return None
    token = token.strip().upper()
    if not token.startswith("RELATIVE:"):
        return None

    body = token.split(":", 1)[1]
    now = datetime.now()
    if "@" in body:
        day_part, time_part = body.split("@", 1)
        hh, mm = time_part.split(":")
        hh, mm = int(hh), int(mm)
    else:
        day_part, hh, mm = body, 9, 0

    base = now if day_part == "TODAY" else (now + timedelta(days=1) if day_part == "TOMORROW" else None)
    if not base:
        return None
    return datetime(year=base.year, month=base.month, day=base.day, hour=hh, minute=mm)

def resolve_agenda_times(parsed: Dict[str, Any]) -> Dict[str, Optional[datetime]]:
    start_raw = parsed.get("start")
    end_raw = parsed.get("end")
    start_dt = resolve_relative(start_raw) or parse_iso(start_raw)
    end_dt = resolve_relative(end_raw) or parse_iso(end_raw)
    if start_dt and not end_dt:
        end_dt = start_dt + timedelta(minutes=60)
    return {"start": start_dt, "end": end_dt}

def get_request_user_id() -> Optional[int]:
    hdr = request.headers.get("X-User-Id")
    if hdr:
        try:
            return int(hdr)
        except Exception:
            pass
    if request.is_json:
        j = request.get_json(silent=True) or {}
        if "user_id" in j and j["user_id"] is not None:
            try:
                return int(j["user_id"])
            except Exception:
                pass
    return None

# =========================================================
# Contatos — helpers simples (determinísticos)
# =========================================================
def _query(sql: str, params=()):
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        try: conn.close()
        except: pass

ROLE_SYNONYMS = [
    ("marketing", "Marketing"),
    ("recursos humanos", "Recursos Humanos"),
    ("rh", "Recursos Humanos"),
    ("financeiro", "Financeiro"),
    ("comercial", "Comercial"),
    ("vendas", "Comercial"),
    ("administrador de sistemas", "Administrador de Sistemas"),
    ("admin de sistemas", "Administrador de Sistemas"),
    ("ti", "Tecnologia"),
    ("tecnologia", "Tecnologia"),
]
CONTACT_TRIGGERS = tuple(_norm(x) for x in ["contato", "contatos", "responsável", "responsavel"])
CONTACT_SEM_TRIGGERS = tuple(_norm(x) for x in [
    "contato","contatos","responsavel","responsável","falar com","com quem falar",
    "quem cuida","quem posta","quem é o responsável","quem e o responsavel"
])

def looks_like_contact_request(text: str) -> bool:
    q = _norm(text)
    return any(t in q for t in CONTACT_SEM_TRIGGERS)

def detect_contact_intent(question: str):
    q = _norm(question)
    if not any(t in q for t in CONTACT_TRIGGERS):
        return None
    if any(x in q for x in ("todos", "todas", "lista", "listar")):
        return {"type": "all"}
    for key_norm, like in ROLE_SYNONYMS:
        if key_norm in q:
            return {"type": "role", "like": like}
    return {"type": "unknown"}

def fetch_contacts_by_role_like(role_like: str, limit: int = 10):
    like = f"%{role_like}%"
    sql = (
        "SELECT u.nome, u.email, u.telefone, c.cargo "
        "FROM users u JOIN cargos c ON u.role_id = c.role_id "
        "WHERE u.ativo = 1 AND (c.cargo LIKE %s OR c.grupo_familia LIKE %s) "
        "ORDER BY c.cargo, u.nome LIMIT %s"
    )
    return _query(sql, (like, like, limit))

def fetch_all_contacts(limit: int = 20):
    sql = (
        "SELECT u.nome, u.email, u.telefone, c.cargo "
        "FROM users u JOIN cargos c ON u.role_id = c.role_id "
        "WHERE u.ativo = 1 ORDER BY c.cargo, u.nome LIMIT %s"
    )
    return _query(sql, (limit,))

def format_contacts(contacts):
    if not contacts:
        return "Não encontrei contatos para esse perfil no momento."
    return "\n".join(
        f"• {c.get('nome','-')} — {c.get('cargo','-')} | {c.get('email','-')} | {c.get('telefone','-')}"
        for c in contacts
    )

def handle_contact_intent(intent):
    if intent["type"] == "all":
        return "Contatos (até 20):\n" + format_contacts(fetch_all_contacts(limit=20))
    if intent["type"] == "role":
        like = intent["like"]
        return f"Contatos — {like}:\n" + format_contacts(fetch_contacts_by_role_like(like, limit=10))
    return ("Você pediu contatos, mas não identifiquei o setor/cargo. "
            "Exemplos: 'contato do administrador de sistemas', 'contatos do RH', 'listar contatos de marketing'.")

# =========================================================
# Rotas de arquivos estáticos
# =========================================================
@app.get("/")
def serve_login():
    return send_from_directory(app.static_folder + "/login", "index.html")

@app.get("/static/login/<path:path>")
def serve_login_assets(path):
    return send_from_directory(app.static_folder + "/login", path)

@app.get("/static/agent/<path:path>")
def serve_agent_assets(path):
    return send_from_directory(app.static_folder + "/agent", path)

# =========================================================
# Rotas utilitárias
# =========================================================
@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "base_loaded": not BASE_IS_EMPTY, "base_chars": len(KNOWLEDGE_TEXT)}), 200

@app.get("/routes")
def routes():
    return jsonify({"ok": True, "routes": ["/", "/healthz", "/chat", "/auth/login", "/webhook/whatsapp"]})

# =========================================================
# CHAT (web)
# =========================================================
@app.post("/chat")
def chat_local():
    """
    JSON: { "body": "<texto>", "user_id": <opcional> }
    - Intenção AGENDA (create/list)
    - Intenção CONTATOS (determinística)
    - Caso contrário, FAQ (PDF)
    """
    data = request.get_json(force=True)
    question = (data.get("body") or "").strip()
    user_id = get_request_user_id()

    if not question:
        return jsonify({"error": "body vazio"}), 400

    # 0) CONTATOS (rápido/determinístico)
    if looks_like_contact_request(question):
        intent = detect_contact_intent(question)
        if intent:
            return jsonify({"answer": clamp(handle_contact_intent(intent))})

    # 1) AGENDA
    agenda = None
    try:
        agenda = parse_agenda_command(question)
    except Exception:
        agenda = None

    if agenda and agenda.get("action") in {"create", "list"}:
        if not user_id:
            return jsonify({"answer": "Para usar a agenda, faça login primeiro."}), 401

        action = agenda["action"]
        times = resolve_agenda_times(agenda)
        start_dt, end_dt = times["start"], times["end"]
        descr = (agenda.get("descricao") or "").strip()

        if action == "create":
            if not start_dt or not end_dt or not descr:
                return jsonify({"answer": "Não entendi data/horário/descrição. Ex.: 'marcar cardiologista dia 15/11 às 16h por 1h'."})
            # conflito apenas no calendário do usuário (modelo 2)
            conflicts = find_conflicts(user_id, start_dt, end_dt)
            if conflicts:
                lines = []
                for c in conflicts:
                    # suporte às duas variantes de colunas, dependendo de como você montou a view/consulta no db
                    if "data_evento" in c and "hora_inicio" in c:
                        lines.append(f"• {c['descricao']} — {c['data_evento']} {c['hora_inicio']}-{c.get('hora_fim','')}")
                    else:
                        si = c.get("starts_at") or c.get("inicio_utc")
                        ei = c.get("ends_at")   or c.get("fim_utc")
                        si_s = si.strftime("%d/%m/%Y %H:%M") if isinstance(si, datetime) else str(si)
                        ei_s = ei.strftime("%H:%M") if isinstance(ei, datetime) else str(ei)
                        lines.append(f"• {c.get('descricao','(sem descrição)')} — {si_s}-{ei_s}")
                return jsonify({"answer": "Você já tem compromisso nesse horário:\n" + "\n".join(lines)})

            # app.py — dentro do bloco action == "create"
            ok = add_event(user_id, start_dt, end_dt, descr)
            if not ok:
                return jsonify({"answer": "Não consegui salvar o evento agora. Tente novamente."}), 500

            return jsonify({"answer": f"Agendei: **{descr}** em {start_dt.strftime('%d/%m/%Y %H:%M')}–{end_dt.strftime('%H:%M')}."})

        elif action == "list":
            date_token = agenda.get("date")
            query_day: Optional[date] = None
            if date_token:
                rel_dt = resolve_relative(date_token)
                if rel_dt:
                    query_day = rel_dt.date()
                else:
                    try:
                        query_day = datetime.fromisoformat(date_token).date()
                    except Exception:
                        pass
            if not query_day and start_dt:
                query_day = start_dt.date()
            if not query_day:
                query_day = datetime.now().date()

            rows = list_events_for_user_on_date(user_id, query_day)
            if not rows:
                return jsonify({"answer": f"Você não tem compromissos em {query_day.strftime('%d/%m/%Y')}."})

            lines = []
            for r in rows:
                if "data_evento" in r and "hora_inicio" in r:
                    lines.append(f"• {r['descricao']} — {r['data_evento']} {r['hora_inicio']}-{r.get('hora_fim','')}")
                else:
                    si = r.get("starts_at") or r.get("inicio_utc")
                    ei = r.get("ends_at")   or r.get("fim_utc")
                    si_s = si.strftime("%H:%M") if isinstance(si, datetime) else str(si)[11:16]
                    ei_s = ei.strftime("%H:%M") if isinstance(ei, datetime) else str(ei)[11:16]
                    day  = (si.date().strftime("%d/%m/%Y") if isinstance(si, datetime) else str(si)[:10])
                    lines.append(f"• {r.get('descricao','(sem descrição)')} — {day} {si_s}-{ei_s}")

            return jsonify({"answer": f"Seus compromissos em {query_day.strftime('%d/%m/%Y')}:\n" + "\n".join(lines)})

    # 2) FAQ
    if BASE_IS_EMPTY:
        return jsonify({"answer": clamp(FALLBACK_NO_BASE + " " + DONT_KNOW_YET_MSG)})

    answer = ask_llm_with_deadline(
    [
        {"role": "system", "content": SYSTEM_PROMPT.strip()},
        {
            "role": "user",
            "content": (
                "Com base estrita no seguinte manual interno, responda de forma objetiva e curta:\n\n"
                f"{KNOWLEDGE_TEXT}\n\n"
                f"Pergunta: {question}\n\n"
                "Se a resposta não estiver claramente no texto, escreva apenas: CONHECIMENTO_INSUFICIENTE."
            ),
        },
    ]
)
    if not answer or "conhecimento_insuficiente" in _norm(answer):
        return jsonify({"answer": clamp(DONT_KNOW_YET_MSG)})

    return jsonify({"answer": clamp(answer)})

# =========================================================
# Webhook WhatsApp (Twilio) — mantém FAQ (pode estender agenda depois)
# =========================================================
@app.route("/webhook/whatsapp", methods=["POST", "GET"])
@app.route("/webhook/whatsapp/", methods=["POST", "GET"])
@app.route("/whatsapp", methods=["POST", "GET"])
@app.route("/whatsapp/", methods=["POST", "GET"])
def whatsapp_webhook():
    try:
        print(">> METHOD:", request.method, "| PATH:", request.path)
        print(">> FORM keys:", list(request.form.keys()))
    except Exception:
        pass

    if request.method == "GET":
        return twiml_text("OK")

    question = None
    from_num = None

    if request.form:
        question = (request.form.get("Body") or "").strip()
        from_num = request.form.get("From")
    elif request.is_json:
        data = request.get_json(silent=True) or {}
        question = (data.get("Body") or data.get("body") or "").strip()
        from_num = data.get("From") or data.get("from")

    if not question:
        return twiml_text("")

    print(">> FROM:", from_num)
    print(">> QUESTION:", question)

    user_profile = get_user_by_whatsapp(from_num)
    print(">> PROFILE:", user_profile)
    if user_profile is None:
        return twiml_text(NOT_REGISTERED_MSG)

    if BASE_IS_EMPTY:
        return twiml_text(FALLBACK_NO_BASE + " " + DONT_KNOW_YET_MSG)

    answer = ask_llm_with_deadline(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"[IDENTIDADE]\n{user_profile.get('nome','Usuário')}\n\n"
                           f"[BASE DE CONHECIMENTO]\n{KNOWLEDGE_TEXT}\n\n"
                           f"[PERGUNTA]\n{question}\n\n"
                           "Responda SOMENTE se encontrar na base; caso contrário, escreva exatamente CONHECIMENTO_INSUFICIENTE.",
            },
        ]
    )
    if not answer or "conhecimento_insuficiente" in _norm(answer):
        return twiml_text(DONT_KNOW_YET_MSG)

    return twiml_text(answer)

# =========================================================
# Login simples (sem hash — demo)
# =========================================================
@app.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    senha = (data.get("senha") or "").strip()

    if not email or not senha:
        return jsonify({"success": False, "message": "Informe email e senha."}), 400

    try:
        conn = _db_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, nome, email, telefone, ativo, role_id "
                "FROM users WHERE email=%s AND senha=%s LIMIT 1",
                (email, senha),
            )
            user = cur.fetchone()
    except Exception as e:
        print("!! DB ERROR /auth/login:", e)
        return jsonify({"success": False, "message": "Erro interno no servidor."}), 500
    finally:
        try: conn.close()
        except: pass

    if not user:
        return jsonify({"success": False, "message": "E-mail ou senha inválidos."}), 401
    if not user.get("ativo"):
        return jsonify({"success": False, "message": "Usuário inativo."}), 403

    return jsonify({"success": True, "user": {
        "id": user["id"], "nome": user["nome"], "email": user["email"], "role_id": user["role_id"]
    }}), 200

# =========================================================
# Start
# =========================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)