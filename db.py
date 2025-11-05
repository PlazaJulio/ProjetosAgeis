# db.py
from typing import Optional, Dict, List
import pymysql
from datetime import datetime, timedelta, date
from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME


# ------------------------
# Conexão e utilitários
# ------------------------
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


# ------------------------
# Usuários
# ------------------------
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


# =========================
# AGENDA (CRUD simples)
# =========================
def agenda_find_conflicts(user_id: int, starts_at: datetime, ends_at: datetime) -> List[Dict]:
    """
    Conflito se houver qualquer sobreposição: (start < existente_end) AND (end > existente_start)
    """
    conn = _get_conn()
    if conn is None:
        return []

    sql = """
      SELECT id, user_id, descricao, starts_at, ends_at, created_at, updated_at
      FROM agenda
      WHERE user_id = %s
        AND starts_at < %s
        AND ends_at   > %s
      ORDER BY starts_at ASC
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id, ends_at, starts_at))
            return cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def agenda_create(user_id: int, descricao: str, starts_at: datetime, ends_at: datetime) -> int:
    """Cria um evento e retorna o ID criado."""
    conn = _get_conn()
    if conn is None:
        return 0

    sql = """
      INSERT INTO agenda (user_id, descricao, starts_at, ends_at)
      VALUES (%s, %s, %s, %s)
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id, descricao, starts_at, ends_at))
            return cur.lastrowid or 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def agenda_list_between(user_id: int, start: datetime, end: datetime) -> List[Dict]:
    """Lista eventos do usuário que comecem no intervalo [start, end)."""
    conn = _get_conn()
    if conn is None:
        return []

    sql = """
      SELECT id, user_id, descricao, starts_at, ends_at, created_at, updated_at
      FROM agenda
      WHERE user_id = %s
        AND starts_at >= %s
        AND starts_at < %s
      ORDER BY starts_at ASC
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id, start, end))
            return cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def agenda_delete_by_id(user_id: int, event_id: int) -> int:
    """Exclui um evento do usuário pelo ID. Retorna número de linhas afetadas (0/1)."""
    conn = _get_conn()
    if conn is None:
        return 0

    sql = "DELETE FROM agenda WHERE id = %s AND user_id = %s"
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (event_id, user_id))
            return cur.rowcount or 0
    finally:
        try:
            conn.close()
        except Exception:
            pass