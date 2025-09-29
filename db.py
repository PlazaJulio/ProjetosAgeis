# db.py
from typing import Optional, Dict
import pymysql
from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

def _get_conn():
    """Abre conexão com MySQL via PyMySQL usando as variáveis do .env."""
    if not all([DB_HOST, DB_USER, DB_NAME]):
        return None
    return pymysql.connect(
        host=DB_HOST,
        port=int(DB_PORT or 3306),
        user=DB_USER,
        password=DB_PASSWORD or "",
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )

def _normalize_whatsapp_from(twilio_from: Optional[str]) -> Optional[str]:
    """Converte 'whatsapp:+55...' para '+55...'."""
    if not twilio_from:
        return None
    s = twilio_from.strip()
    if s.lower().startswith("whatsapp:"):
        s = s.split(":", 1)[1]
    return s

def get_user_by_whatsapp(twilio_from: Optional[str]) -> Optional[Dict]:
    """Busca o usuário + cargo pelo telefone E.164; retorna dict ou None."""
    phone = _normalize_whatsapp_from(twilio_from)
    if not phone:
        return None

    conn = _get_conn()
    if conn is None:
        return None

    sql = (
        "SELECT u.id, u.nome, u.email, u.telefone, u.ativo, u.role_id, "
        "c.cargo, c.grupo_familia, c.nivel_carreira "
        "FROM users u JOIN cargos c ON u.role_id = c.role_id "
        "WHERE u.telefone = %s AND u.ativo = 1 LIMIT 1"
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (phone,))
            row = cur.fetchone()
        return row
    finally:
        try:
            conn.close()
        except Exception:
            pass