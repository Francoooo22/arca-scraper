"""
db.py — Schema SQLite y operaciones de base de datos
Tablas:
  - comprobantes_emitidos : comprobantes emitidos Y recibidos (campo tipo_operacion)
  - scrape_log            : historial de ejecuciones por CUIT y período
"""

import sqlite3
from datetime import datetime
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # soporta escrituras concurrentes (#5)
    return conn


def init_db():
    """Crea las tablas si no existen y aplica migraciones incrementales."""
    conn = get_conn()
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS comprobantes_emitidos (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            cuit_emisor         TEXT NOT NULL,
            razon_social        TEXT,
            fecha_comprobante   TEXT,
            tipo_comprobante    TEXT,
            punto_venta         TEXT,
            numero              TEXT,
            cuit_receptor       TEXT,
            denominacion_receptor TEXT,
            importe_neto        REAL,
            importe_iva         REAL,
            importe_total       REAL,
            moneda              TEXT DEFAULT 'PES',
            tipo_cambio         REAL DEFAULT 1.0,
            cae                 TEXT,
            fecha_vto_cae       TEXT,
            estado              TEXT,
            periodo_fiscal      TEXT,
            tipo_operacion      TEXT DEFAULT 'emitido',
            incluido_ddjj       INTEGER DEFAULT 0,
            observaciones       TEXT,
            scrapeado_en        TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(cuit_emisor, punto_venta, numero, tipo_comprobante, tipo_operacion)
        );

        CREATE TABLE IF NOT EXISTS scrape_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            cuit            TEXT NOT NULL,
            razon_social    TEXT,
            periodo_desde   TEXT,
            periodo_hasta   TEXT,
            tipo_operacion  TEXT DEFAULT 'emitido',
            estado          TEXT,
            comprobantes_encontrados INTEGER DEFAULT 0,
            comprobantes_nuevos      INTEGER DEFAULT 0,
            mensaje         TEXT,
            iniciado_en     TEXT DEFAULT (datetime('now', 'localtime')),
            finalizado_en   TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_comp_cuit   ON comprobantes_emitidos(cuit_emisor);
        CREATE INDEX IF NOT EXISTS idx_comp_periodo ON comprobantes_emitidos(periodo_fiscal);
        CREATE INDEX IF NOT EXISTS idx_comp_cae    ON comprobantes_emitidos(cae);
    """)

    # Migraciones: agregar columnas nuevas ANTES de crear índices sobre ellas
    _agregar_columna(cur, "comprobantes_emitidos", "tipo_operacion", "TEXT DEFAULT 'emitido'")
    _agregar_columna(cur, "scrape_log",            "tipo_operacion", "TEXT DEFAULT 'emitido'")

    # Índice sobre tipo_operacion (requiere que la columna ya exista)
    try:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_comp_tipo ON comprobantes_emitidos(tipo_operacion)"
        )
    except Exception:
        pass

    conn.commit()
    conn.close()
    print(f"[DB] Base de datos inicializada en: {DB_PATH}")


def _agregar_columna(cur, tabla: str, columna: str, definicion: str):
    """Agrega una columna si aún no existe (ALTER TABLE seguro)."""
    try:
        cur.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")
    except Exception:
        pass  # ya existe → ignorar


def insertar_comprobante(datos: dict) -> bool:
    """
    Inserta un comprobante. Si ya existe (UNIQUE), lo ignora.
    Retorna True si fue insertado, False si ya existía.
    """
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT OR IGNORE INTO comprobantes_emitidos (
                cuit_emisor, razon_social, fecha_comprobante,
                tipo_comprobante, punto_venta, numero,
                cuit_receptor, denominacion_receptor,
                importe_neto, importe_iva, importe_total,
                moneda, tipo_cambio, cae, fecha_vto_cae,
                estado, periodo_fiscal, tipo_operacion
            ) VALUES (
                :cuit_emisor, :razon_social, :fecha_comprobante,
                :tipo_comprobante, :punto_venta, :numero,
                :cuit_receptor, :denominacion_receptor,
                :importe_neto, :importe_iva, :importe_total,
                :moneda, :tipo_cambio, :cae, :fecha_vto_cae,
                :estado, :periodo_fiscal, :tipo_operacion
            )
        """, datos)
        insertado = cur.rowcount > 0
        conn.commit()
        return insertado
    finally:
        conn.close()


def insertar_muchos(lista: list[dict]) -> tuple[int, int]:
    """Inserta una lista de comprobantes. Retorna (total, nuevos)."""
    nuevos = 0
    for comp in lista:
        if insertar_comprobante(comp):
            nuevos += 1
    return len(lista), nuevos


def log_inicio(cuit: str, razon_social: str, desde: str, hasta: str,
               tipo_operacion: str = "emitido") -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO scrape_log
            (cuit, razon_social, periodo_desde, periodo_hasta, tipo_operacion, estado)
        VALUES (?, ?, ?, ?, ?, 'CORRIENDO')
    """, (cuit, razon_social, desde, hasta, tipo_operacion))
    log_id = cur.lastrowid
    conn.commit()
    conn.close()
    return log_id


def log_fin(log_id: int, estado: str, encontrados: int, nuevos: int, mensaje: str = ""):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE scrape_log
        SET estado=?, comprobantes_encontrados=?, comprobantes_nuevos=?,
            mensaje=?, finalizado_en=datetime('now','localtime')
        WHERE id=?
    """, (estado, encontrados, nuevos, mensaje, log_id))
    conn.commit()
    conn.close()


def consultar_comprobantes(cuit: str = None, periodo: str = None,
                           tipo_operacion: str = None) -> list[dict]:
    """Consulta comprobantes con filtros opcionales."""
    conn = get_conn()
    cur = conn.cursor()
    query = "SELECT * FROM comprobantes_emitidos WHERE 1=1"
    params = []
    if cuit:
        query += " AND cuit_emisor = ?"
        params.append(cuit)
    if periodo:
        query += " AND periodo_fiscal = ?"
        params.append(periodo)
    if tipo_operacion:
        query += " AND tipo_operacion = ?"
        params.append(tipo_operacion)
    query += " ORDER BY fecha_comprobante DESC, numero DESC"
    cur.execute(query, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows
