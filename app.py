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
    add_event,
    find_conflicts,
    list_events_for_user_on_date,
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
    xml = (
        f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe}</Message></Response>'
    )
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
    return any(
        token in q
        for token in [
            "nao",
            "não",
            "nao foi",
            "não foi",
            "nao ajudou",
            "não ajudou",
            "negativo",
        ]
    )


# =========================================================
# Agenda — utilitários
# =========================================================
def parse_iso(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        # aceita "YYYY-MM-DDTHH:MM:SS" ou "YYYY-MM-DD HH:MM:SS"
        dt_str = dt_str.replace(" ", "T")
        return datetime.fromisoformat(dt_str)
    except Exception:
        return None


def resolve_relative(token: Optional[str]) -> Optional[datetime]:
    """
    Converte marcadores como:
      RELATIVE:TODAY@10:00
      RELATIVE:TOMORROW
    em datetime (para @HH:MM assume hoje/amanhã com hora/min).
    Retorna None se não reconhecido.
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
        hh = int(hh)
        mm = int(mm)
    else:
        day_part = body
        hh, mm = 9, 0  # default 09:00 quando não informado

    if day_part == "TODAY":
        base = now
    elif day_part == "TOMORROW":
        base = now + timedelta(days=1)
    else:
        return None

    return datetime(year=base.year, month=base.month, day=base.day, hour=hh, minute=mm)


def resolve_agenda_times(parsed: Dict[str, Any]) -> Dict[str, Optional[datetime]]:
    """
    Recebe o JSON do parse_agenda_command e devolve start/end como datetime.
    Se não houver end, assume 60min após start.
    Resolve também tokens RELATIVE:*.
    """
    start_raw = parsed.get("start")
    end_raw = parsed.get("end")

    start_dt = resolve_relative(start_raw) or parse_iso(start_raw)
    end_dt = resolve_relative(end_raw) or parse_iso(end_raw)

    if start_dt and not end_dt:
        end_dt = start_dt + timedelta(minutes=60)

    return {"start": start_dt, "end": end_dt}


def get_request_user_id() -> Optional[int]:
    """
    Recupera o id do usuário logado vindo do front:
    - Header: X-User-Id
    - Body JSON: { user_id: ... }
    """
    # header
    hdr = request.headers.get("X-User-Id")
    if hdr:
        try:
            return int(hdr)
        except Exception:
            pass
    # json
    if request.is_json:
        try:
            j = request.get_json(silent=True) or {}
            if "user_id" in j and j["user_id"] is not None:
                return int(j["user_id"])
        except Exception:
            pass
    return None


# =========================================================
# Rotas de arquivos estáticos básicos
# =========================================================
@app.get("/")
def serve_login():
    # página de login
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
    return (
        jsonify(
            {"status": "ok", "base_loaded": not BASE_IS_EMPTY, "base_chars": len(KNOWLEDGE_TEXT)}
        ),
        200,
    )


@app.get("/routes")
def routes():
    return jsonify(
        {"ok": True, "routes": ["/", "/healthz", "/chat", "/auth/login", "/webhook/whatsapp"]}
    )


# =========================================================
# Rota de CHAT (web)
# =========================================================
@app.post("/chat")
def chat_local():
    """
    Espera JSON: { "body": "<texto do usuário>", "user_id": <opcional> }
    Se identificar intenção de AGENDA, executa (criar/listar) usando o user_id.
    Caso contrário, usa o FAQ (PDF).
    """
    data = request.get_json(force=True)
    question = (data.get("body") or "").strip()
    user_id = get_request_user_id()

    if not question:
        return jsonify({"error": "body vazio"}), 400

    # 0) Tenta interpretar AGENDA
    agenda = None
    try:
        agenda = parse_agenda_command(question)
    except Exception:
        agenda = None

    if agenda and agenda.get("action") in {"create", "list", "cancel"}:
        if not user_id:
            # sem usuário logado, não dá pra associar eventos
            return jsonify({"answer": "Para usar a agenda, faça login primeiro."}), 401

        action = agenda["action"]

        # Resolver tempos
        times = resolve_agenda_times(agenda)
        start_dt = times["start"]
        end_dt = times["end"]
        descr = (agenda.get("descricao") or "").strip()

        if action == "create":
            if not start_dt or not end_dt or not descr:
                return jsonify(
                    {
                        "answer": "Não entendi totalmente data/horário/descrição. Tente: "
                        "'marcar cardiologista dia 15/11 às 16h por 1h'."
                    }
                )

            # verifica conflito apenas no calendário do próprio usuário (modelo 2)
            conflicts = find_conflicts(user_id, start_dt, end_dt)
            if conflicts:
                return jsonify(
                    {
                        "answer": "Você já tem compromisso nesse horário:\n"
                        + "\n".join(
                            f"• {c['descricao']} — {c['data_evento']} {c['hora_inicio']}-{c['hora_fim']}"
                            for c in conflicts
                        )
                    }
                )

            ok = add_event(user_id, descr, start_dt, end_dt)
            if not ok:
                return jsonify({"answer": "Não consegui salvar o evento agora. Tente novamente."}), 500

            return jsonify(
                {
                    "answer": f"Agendei: **{descr}** em {start_dt.strftime('%d/%m/%Y %H:%M')}–{end_dt.strftime('%H:%M')}."
                }
            )

        elif action == "list":
            # se o parser forneceu uma 'date' relativa/ISO, resolvemos
            date_token = agenda.get("date")
            query_day: Optional[date] = None
            if date_token:
                rel_dt = resolve_relative(date_token)
                if rel_dt:
                    query_day = rel_dt.date()
                else:
                    # tenta ISO simples (YYYY-MM-DD)
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
                lines.append(
                    f"• {r['descricao']} — {r['data_evento']} {r['hora_inicio']}-{r['hora_fim']}"
                )
            return jsonify({"answer": f"Seus compromissos em {query_day.strftime('%d/%m/%Y')}:\n" + "\n".join(lines)})

        elif action == "cancel":
            # (Opcional) Implementar um endpoint/DB para delete por id/horário/descrição.
            return jsonify({"answer": "Cancelamento ainda não está disponível nessa versão."})

    # 1) Fora da agenda -> segue fluxo de FAQ
    # 1.1) Base não carregada
    if BASE_IS_EMPTY:
        return jsonify({"answer": clamp(FALLBACK_NO_BASE + " " + DONT_KNOW_YET_MSG)})

    # 1.2) FAQ obrigatório (somente base)
    answer = ask_llm_with_deadline(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"[BASE DE CONHECIMENTO]\n{KNOWLEDGE_TEXT}\n\n"
                f"[PERGUNTA]\n{question}\n\n"
                "Responda SOMENTE se encontrar na base; caso contrário, escreva exatamente CONHECIMENTO_INSUFICIENTE.",
            },
        ]
    )
    if not answer or "conhecimento_insuficiente" in _norm(answer):
        return jsonify({"answer": clamp(DONT_KNOW_YET_MSG)})

    return jsonify({"answer": clamp(answer)})


# =========================================================
# Webhook WhatsApp (Twilio) — inalterado (resumo)
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
        from_num = request.form.get("From")  # 'whatsapp:+55...'
    elif request.is_json:
        data = request.get_json(silent=True) or {}
        question = (data.get("Body") or data.get("body") or "").strip()
        from_num = data.get("From") or data.get("from")

    if not question:
        return twiml_text("")

    print(">> FROM:", from_num)
    print(">> QUESTION:", question)

    # Usuário não cadastrado
    user_profile = get_user_by_whatsapp(from_num)
    print(">> PROFILE:", user_profile)
    if user_profile is None:
        return twiml_text(NOT_REGISTERED_MSG)

    # (Opcional) Poderíamos integrar a agenda aqui também, usando user_profile['id'].
    # Para simplificar, o webhook permanece com o fluxo de FAQ original.

    # Base não carregada
    if BASE_IS_EMPTY:
        return twiml_text(FALLBACK_NO_BASE + " " + DONT_KNOW_YET_MSG)

    # FAQ
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
# Login simples (sem hash — uso acadêmico)
# =========================================================
@app.post("/auth/login")
def login():
    """
    Autentica o usuário com email e senha (sem hash).
    Retorna { success, user:{id, nome, email, role_id} }.
    """
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
        try:
            conn.close()
        except Exception:
            pass

    if not user:
        return jsonify({"success": False, "message": "E-mail ou senha inválidos."}), 401
    if not user.get("ativo"):
        return jsonify({"success": False, "message": "Usuário inativo."}), 403

    return (
        jsonify(
            {
                "success": True,
                "user": {
                    "id": user["id"],
                    "nome": user["nome"],
                    "email": user["email"],
                    "role_id": user["role_id"],
                },
            }
        ),
        200,
    )


# =========================================================
# Start
# =========================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)