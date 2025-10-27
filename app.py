from flask import Flask, request, jsonify, Response
from config import PORT, DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
from pdf_ingest import load_knowledge_text
from llm import chat
from db import get_user_by_whatsapp
import html
import pymysql
import traceback
import unicodedata
import json
import time

# ---------- Base (PDF -> texto) ----------
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

app = Flask(__name__)

# ---------- Helpers ----------
def clamp(s: str, max_chars: int = 1500) -> str:
    s = (s or "").strip()
    return s[:max_chars]

def xml_escape(s: str) -> str:
    return html.escape(s or "", quote=True)

def twiml_text(text: str) -> Response:
    """Gera TwiML com header correto e texto escapado/limitado."""
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

def ask_llm_with_deadline(messages, deadline_sec: int = 10):
    """Chama o LLM e devolve None se passar do prazo ou der erro."""
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

# Fora de contexto: sem pergunta de confirmação e sem follow-up
OUT_OF_SCOPE_MSG = (
    "Sou um assistente interno e só consigo ajudar com informações da empresa (onboarding, setores, processos e contatos)."
)

# Em contexto mas sem resposta: pergunta confirmação e ativa follow-up
DONT_KNOW_YET_MSG = (
    "Essa pergunta está no contexto da empresa, mas ainda não tenho essa resposta no manual. "
    "Vamos aprimorar os conhecimentos com essa dúvida. Sua dúvida foi atendida? (responda 'não' para receber o contato do responsável)"
)

# ---------- Pequena memória de confirmação por número ----------
STATE = {}  # { from_num: {"awaiting": True, "role_like": str|None, "ts": float} }

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

# ---------- DB helpers (contatos) ----------
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

def _query(sql: str, params=()):
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass

def fetch_contacts_by_role_like(role_like: str, limit: int = 10):
    like = f"%{role_like}%"
    sql = (
        "SELECT u.nome, u.email, u.telefone, c.cargo "
        "FROM users u JOIN cargos c ON u.role_id = c.role_id "
        "WHERE u.ativo = 1 AND (c.cargo LIKE %s OR c.grupo_familia LIKE %s) "
        "ORDER BY c.cargo, u.nome "
        "LIMIT %s"
    )
    return _query(sql, (like, like, limit))

def fetch_all_contacts(limit: int = 20):
    sql = (
        "SELECT u.nome, u.email, u.telefone, c.cargo "
        "FROM users u JOIN cargos c ON u.role_id = c.role_id "
        "WHERE u.ativo = 1 "
        "ORDER BY c.cargo, u.nome "
        "LIMIT %s"
    )
    return _query(sql, (limit,))

def format_contacts(contacts):
    if not contacts:
        return "Não encontrei contatos para esse perfil no momento."
    lines = []
    for c in contacts:
        lines.append(f"• {c.get('nome','-')} — {c.get('cargo','-')} | {c.get('email','-')} | {c.get('telefone','-')}")
    return "\n".join(lines)

# ---------- Intents de contato (determinísticas) ----------
ROLE_SYNONYMS = [
    ("marketing", "Marketing"),
    ("recursos humanos", "Recursos Humanos"),
    ("rh", "Recursos Humanos"),
    ("financeiro", "Financeiro"),
    ("comercial", "Comercial"),
    ("vendas", "Comercial"),
    ("administrador de sistemas", "Administrador de Sistemas"),
    ("admin de sistemas", "Administrador de Sistemas"),
    ("administrador do sistema", "Administrador de Sistemas"),
    ("ti", "Tecnologia"),
    ("tecnologia", "Tecnologia"),
]
CONTACT_TRIGGERS = tuple(_norm(x) for x in ["contato", "contatos", "responsável", "responsavel"])

# Gatilhos mais “humanos” de contato (variações)
CONTACT_SEM_TRIGGERS = tuple(_norm(x) for x in [
    "contato", "contatos", "responsavel", "responsável",
    "falar com", "com quem falar", "quem cuida", "quem posta", "quem é o responsável",
    "quem e o responsavel"
])

def looks_like_contact_request(text: str) -> bool:
    q = _norm(text)
    return any(t in q for t in CONTACT_SEM_TRIGGERS)

def detect_contact_intent(question: str):
    q = _norm(question)

    if not any(t in q for t in CONTACT_TRIGGERS):
        return None

    if any(x in q for x in ("todos", "todas", "lista", "listar")):
        intent = {"type": "all"}
        print(">> CONTACT_INTENT(DET):", intent)
        return intent

    for key_norm, like in ROLE_SYNONYMS:
        if key_norm in q:
            intent = {"type": "role", "like": like}
            print(">> CONTACT_INTENT(DET):", intent, "| MATCHED_KEY:", key_norm)
            return intent

    intent = {"type": "unknown"}
    print(">> CONTACT_INTENT(DET):", intent)
    return intent

# ---------- Classificador semântico de contato ----------
VALID_CONTACT_ROLES = {
    "Administrador de Sistemas": "Administrador de Sistemas",
    "Tecnologia": "Tecnologia",
    "Recursos Humanos": "Recursos Humanos",
    "Marketing": "Marketing",
    "Comercial": "Comercial",
    "Financeiro": "Financeiro",
}

CLASSIFIER_CONTACT_SYS = (
    "Você é um roteador curto que decide se a pergunta é um pedido de CONTATO interno.\n"
    "Se for, escolha exatamente UM dos papéis válidos:\n"
    f"{', '.join(VALID_CONTACT_ROLES.keys())}.\n"
    'Responda APENAS em JSON: {"is_contact_request": true/false, "role": "<papel ou null>"}'
)
CLASSIFIER_CONTACT_FEWSHOTS = [
    ("quem cuida das senhas e acessos?", '{"is_contact_request": true, "role": "Administrador de Sistemas"}'),
    ("preciso falar com quem posta no instagram", '{"is_contact_request": true, "role": "Marketing"}'),
    ("qual o contato do rh?", '{"is_contact_request": true, "role": "Recursos Humanos"}'),
    ("quem paga os fornecedores?", '{"is_contact_request": true, "role": "Financeiro"}'),
    ("quem faz prospecção de clientes?", '{"is_contact_request": true, "role": "Comercial"}'),
    ("quem mantém os servidores e a infraestrutura de rede?", '{"is_contact_request": true, "role": "Tecnologia"}'),
    ("qual a política de férias?", '{"is_contact_request": false, "role": null}'),
]

def semantic_contact_guess(question: str):
    q = (question or "").strip()
    if not q:
        return None
    msgs = [{"role": "system", "content": CLASSIFIER_CONTACT_SYS}]
    for u, a in CLASSIFIER_CONTACT_FEWSHOTS:
        msgs.append({"role": "user", "content": u})
        msgs.append({"role": "assistant", "content": a})
    msgs.append({"role": "user", "content": q})

    try:
        raw = chat(msgs) or ""
    except Exception:
        print("!! LLM ERROR (classifier-contact):\n", traceback.format_exc())
        return None

    try:
        data = json.loads(raw.strip())
    except Exception:
        print(">> CLASSIFIER_CONTACT_RAW:", raw)
        return None

    if not isinstance(data, dict):
        return None

    if bool(data.get("is_contact_request")):
        role = data.get("role")
        if isinstance(role, str) and role in VALID_CONTACT_ROLES:
            intent = {"type": "role", "like": VALID_CONTACT_ROLES[role]}
            print(">> CONTACT_INTENT(SEM):", intent)
            return intent
        return {"type": "unknown"}
    return None

# --- Classificador de ESCOPO (empresa x fora de contexto) ---
CLASSIFIER_SCOPE_SYS = (
    "Classifique se a pergunta está no ESCOPO da empresa (onboarding, setores, processos, benefícios, sistemas internos, contatos) "
    "ou FORA DE CONTEXTO. Responda APENAS em JSON: "
    '{"in_scope": true/false, "role_hint": "<opcional: Marketing, RH, Tecnologia, etc. ou vazio>"}'
)
CLASSIFIER_SCOPE_FEWSHOTS = [
    ("como solicitar férias?", '{"in_scope": true, "role_hint": "Recursos Humanos"}'),
    ("quem cuida das permissões do sistema?", '{"in_scope": true, "role_hint": "Administrador de Sistemas"}'),
    ("quem posta no instagram?", '{"in_scope": true, "role_hint": "Marketing"}'),
    ("a empresa da bonus por algo?", '{"in_scope": true, "role_hint": "Marketing"}'),
    ("como faço bolo de laranja?", '{"in_scope": false, "role_hint": ""}'),
    ("previsão do tempo amanhã", '{"in_scope": false, "role_hint": ""}'),
]

def classify_scope(question: str):
    q = (question or "").strip()
    if not q:
        return {"in_scope": False, "role_hint": None}

    msgs = [{"role": "system", "content": CLASSIFIER_SCOPE_SYS}]
    for u, a in CLASSIFIER_SCOPE_FEWSHOTS:
        msgs.append({"role": "user", "content": u})
        msgs.append({"role": "assistant", "content": a})
    msgs.append({"role": "user", "content": q})

    try:
        raw = chat(msgs) or ""
    except Exception:
        print("!! LLM ERROR (classifier-scope):\n", traceback.format_exc())
        return {"in_scope": False, "role_hint": None}

    try:
        data = json.loads(raw.strip())
    except Exception:
        print(">> CLASSIFIER_SCOPE_RAW:", raw)
        return {"in_scope": False, "role_hint": None}

    role_hint = data.get("role_hint") if isinstance(data.get("role_hint"), str) else None
    role_hint = role_hint if role_hint else None
    return {"in_scope": bool(data.get("in_scope")), "role_hint": role_hint}

# ---------- Rotas utilitárias ----------
@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "base_loaded": not BASE_IS_EMPTY, "base_chars": len(KNOWLEDGE_TEXT)}), 200

@app.get("/")
def root():
    return jsonify({"ok": True, "routes": ["/", "/healthz", "/chat", "/webhook/whatsapp"]})

# ---------- Teste local (JSON) ----------
@app.post("/chat")
def chat_local():
    data = request.get_json(force=True)
    question = (data.get("body") or "").strip()
    if not question:
        return jsonify({"error": "body vazio"}), 400

    # 1) Se for pedido de contato explícito (ou com gatilhos humanos), responde contatos
    if looks_like_contact_request(question):
        intent = detect_contact_intent(question)
        if intent and intent["type"] in ("all", "role"):
            return jsonify({"answer": clamp(handle_contact_intent(intent))})
        sem = semantic_contact_guess(question)
        if sem:
            return jsonify({"answer": clamp(handle_contact_intent(sem))})

    # 2) Fora de contexto?
    scope = classify_scope(question)
    print(">> SCOPE:", scope)
    if not scope["in_scope"]:
        return jsonify({"answer": clamp(OUT_OF_SCOPE_MSG)})

    # 3) Base não carregada
    if BASE_IS_EMPTY:
        set_pending("local", scope.get("role_hint"))
        return jsonify({"answer": clamp(FALLBACK_NO_BASE + " " + DONT_KNOW_YET_MSG)})

    # 4) FAQ obrigatório (modelo deve retornar conteúdo da base ou CONHECIMENTO_INSUFICIENTE)
    answer = ask_llm_with_deadline([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content":
            f"[BASE DE CONHECIMENTO]\n{KNOWLEDGE_TEXT}\n\n"
            f"[PERGUNTA]\n{question}\n\n"
            "Responda SOMENTE se encontrar na base; caso contrário, escreva exatamente CONHECIMENTO_INSUFICIENTE."
        }
    ])
    if not answer or "conhecimento_insuficiente" in _norm(answer):
        set_pending("local", scope.get("role_hint"))
        return jsonify({"answer": clamp(DONT_KNOW_YET_MSG)})

    print(">> FINAL ANSWER (local):", answer[:200], "..." if len(answer) > 200 else "")
    return jsonify({"answer": clamp(answer)})

def handle_contact_intent(intent):
    if intent["type"] == "all":
        contacts = fetch_all_contacts(limit=20)
        return "Contatos (até 20):\n" + format_contacts(contacts)
    if intent["type"] == "role":
        contacts = fetch_contacts_by_role_like(intent["like"], limit=10)
        return f"Contatos — {intent['like']}:\n" + format_contacts(contacts)
    if intent["type"] == "unknown":
        return ("Você pediu contatos, mas não identifiquei o setor/cargo. "
                "Exemplos: 'contato do administrador de sistemas', 'contatos do RH', 'listar contatos de marketing'.")
    return None

# ---------- Webhook WhatsApp (Twilio) ----------
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

    # fluxo de confirmação: se usuário respondeu "não"
    pending = get_pending(from_num)
    if pending and is_negative_answer(question):
        role_like = pending.get("role_like") or "Recursos Humanos"
        contacts = fetch_contacts_by_role_like(role_like, limit=10)
        msg = f"Aqui estão os contatos — {role_like}:\n" + format_contacts(contacts)
        pop_pending(from_num)
        print(">> FINAL ANSWER (contatos follow-up):", msg[:200], "..." if len(msg) > 200 else "")
        return twiml_text(msg)

    # usuário não cadastrado
    user_profile = get_user_by_whatsapp(from_num)
    print(">> PROFILE:", user_profile)
    if user_profile is None:
        return twiml_text(NOT_REGISTERED_MSG)

    # CONTATOS: só se o texto parecer de contato (gatilhos humanos)
    if looks_like_contact_request(question):
        intent = detect_contact_intent(question)
        if intent and intent["type"] in ("all", "role"):
            msg = handle_contact_intent(intent)
            print(">> FINAL ANSWER (contatos det):", msg[:200], "..." if len(msg) > 200 else "")
            return twiml_text(msg)
        sem = semantic_contact_guess(question)
        if sem:
            msg = handle_contact_intent(sem)
            print(">> FINAL ANSWER (contatos sem):", msg[:200], "..." if len(msg) > 200 else "")
            return twiml_text(msg)

    # SCOPE: prioriza FAQ / conteúdo
    scope = classify_scope(question)
    print(">> SCOPE:", scope)
    if not scope["in_scope"]:
        # fora de contexto -> sem follow-up
        return twiml_text(OUT_OF_SCOPE_MSG)

    # Base não carregada
    if BASE_IS_EMPTY:
        set_pending(from_num, scope.get("role_hint"))
        return twiml_text(FALLBACK_NO_BASE + " " + DONT_KNOW_YET_MSG)

    # FAQ: obriga usar somente a base; se não encontrar, ativa follow-up
    answer = ask_llm_with_deadline([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content":
            f"[IDENTIDADE]\n{user_profile.get('nome','Usuário')}\n\n"
            f"[BASE DE CONHECIMENTO]\n{KNOWLEDGE_TEXT}\n\n"
            f"[PERGUNTA]\n{question}\n\n"
            "Responda SOMENTE se encontrar na base; caso contrário, escreva exatamente CONHECIMENTO_INSUFICIENTE."
        }
    ])
    if not answer or "conhecimento_insuficiente" in _norm(answer):
        set_pending(from_num, scope.get("role_hint"))
        print(">> FINAL ANSWER (fallback):", DONT_KNOW_YET_MSG)
        return twiml_text(DONT_KNOW_YET_MSG)

    print(">> FINAL ANSWER:", answer[:200], "..." if len(answer) > 200 else "")
    return twiml_text(answer)

# ---------- Start ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)