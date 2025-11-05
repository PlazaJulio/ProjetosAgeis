# db.py
from __future__ import annotations
from typing import Optional, Dict, List, Any, Tuple, Union
import datetime as dt
import pymysql
from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

# ---------------- Conexão ----------------

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

def _query(sql: str, params: Tuple | List = ()):
    conn = _get_conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _execute(sql: str, params: Tuple | List = ()):
    conn = _get_conn()
    if conn is None:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount
    finally:
        try:
            conn.close()
        except Exception:
            pass

# --------------- Utilidades ----------------

def _normalize_whatsapp_from(twilio_from: Optional[str]) -> Optional[str]:
    """Converte 'whatsapp:+55...' para '+55...'."""
    if not twilio_from:
        return None
    s = twilio_from.strip()
    if s.lower().startswith("whatsapp:"):
        s = s.split(":", 1)[1]
    return s

def _to_dt_str(value: Union[str, dt.date, dt.datetime]) -> str:
    """
    Converte para 'YYYY-MM-DD HH:MM:SS'.
    Aceita datetime, date ou string (inclusive 'YYYY-MM-DD' e 'YYYY-MM-DD HH:MM').
    """
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time(0, 0, 0)).strftime("%Y-%m-%d %H:%M:%S")
    v = str(value).strip().replace("T", " ")
    if len(v) == 10:  # 'YYYY-MM-DD'
        v += " 00:00:00"
    elif len(v) == 16:  # 'YYYY-MM-DD HH:MM'
        v += ":00"
    return v

# --------------- Usuários -------------------

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
            return cur.fetchone()
    finally:
        try:
            conn.close()
        except Exception:
            pass

# --------------- Agenda (tabela `agenda`) ---------------

# Estrutura esperada:
# CREATE TABLE agenda (
#   id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
#   user_id INT NOT NULL,
#   starts_at DATETIME NOT NULL,
#   ends_at   DATETIME NOT NULL,
#   descricao TEXT NOT NULL,
#   created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
#   updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
#   deleted_at TIMESTAMP NULL DEFAULT NULL,
#   INDEX idx_agenda_range (starts_at, ends_at),
#   INDEX idx_agenda_user (user_id),
#   CONSTRAINT fk_agenda_users FOREIGN KEY (user_id) REFERENCES users(id)
#     ON UPDATE CASCADE ON DELETE CASCADE
# );

def add_event(
    user_id: int,
    starts_at: Union[str, dt.datetime, dt.date],
    ends_at:   Union[str, dt.datetime, dt.date],
    descricao: str,
) -> int:
    """Insere um evento e retorna o ID gerado."""
    s = _to_dt_str(starts_at)
    e = _to_dt_str(ends_at)

    conn = _get_conn()
    if conn is None:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agenda (user_id, starts_at, ends_at, descricao, created_by_user_id)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user_id, s, e, descricao, user_id),
            )
            return int(cur.lastrowid or 0)
    finally:
        try:
            conn.close()
        except Exception:
            pass

def find_conflicts(
    user_id: int,
    starts_at: Union[str, dt.datetime, dt.date],
    ends_at:   Union[str, dt.datetime, dt.date],
    exclude_event_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Retorna eventos do MESMO usuário que conflitam com [starts_at, ends_at].
    Regra de sobreposição: (starts_at < ends_at_evento) AND (ends_at > starts_at_evento)
    """
    s = _to_dt_str(starts_at)
    e = _to_dt_str(ends_at)

    params: List[Any] = [user_id, s, e]
    sql = (
        "SELECT id, user_id, starts_at, ends_at, descricao "
        "FROM agenda "
        "WHERE deleted_at IS NULL "
        "  AND user_id = %s "
        "  AND ((%s < ends_at) AND (%s > starts_at))"
    )

    if exclude_event_id is not None:
        sql += " AND id <> %s"
        params.append(exclude_event_id)

    sql += " ORDER BY starts_at ASC"
    return _query(sql, params)

def list_events_for_user(
    user_id: int,
    start: Optional[Union[str, dt.datetime, dt.date]] = None,
    end:   Optional[Union[str, dt.datetime, dt.date]] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Lista eventos de um usuário (não deletados)."""
    params: List[Any] = [user_id]
    sql = (
        "SELECT id, user_id, starts_at, ends_at, descricao "
        "FROM agenda WHERE deleted_at IS NULL AND user_id = %s"
    )
    if start:
        sql += " AND ends_at >= %s"
        params.append(_to_dt_str(start))
    if end:
        sql += " AND starts_at <= %s"
        params.append(_to_dt_str(end))

    sql += " ORDER BY starts_at ASC LIMIT %s"
    params.append(int(limit))
    return _query(sql, params)

def list_events_for_user_on_date(user_id: int, date_like: Union[str, dt.date, dt.datetime]):
    """
    Lista eventos de um usuário em um dia específico (00:00–23:59).
    Aceita 'YYYY-MM-DD' (str), datetime.date ou datetime.datetime.
    """
    if isinstance(date_like, dt.datetime):
        d = date_like.date()
    elif isinstance(date_like, dt.date):
        d = date_like
    else:
        d = dt.datetime.strptime(str(date_like), "%Y-%m-%d").date()

    start = dt.datetime.combine(d, dt.time.min)   # 00:00:00
    end   = dt.datetime.combine(d, dt.time.max)   # 23:59:59.999999
    return list_events_for_user(user_id=user_id, start=start, end=end, limit=200)

def list_events_for_range(
    start: Union[str, dt.datetime, dt.date],
    end:   Union[str, dt.datetime, dt.date],
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Lista todos os eventos (não deletados) no intervalo informado."""
    s = _to_dt_str(start)
    e = _to_dt_str(end)
    sql = (
        "SELECT id, user_id, starts_at, ends_at, descricao "
        "FROM agenda "
        "WHERE deleted_at IS NULL "
        "  AND ((%s < ends_at) AND (%s > starts_at)) "
        "ORDER BY starts_at ASC LIMIT %s"
    )
    return _query(sql, (s, e, int(limit)))

def delete_event(event_id: int, user_id: Optional[int] = None) -> int:
    """Soft delete: marca deleted_at."""
    params: List[Any] = [event_id]
    sql = "UPDATE agenda SET deleted_at = NOW() WHERE id = %s AND deleted_at IS NULL"
    if user_id is not None:
        sql += " AND user_id = %s"
        params.append(user_id)
    return _execute(sql, params)