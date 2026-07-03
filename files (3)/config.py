"""
config.py — Configuración de CUITs y credenciales
Podés agregar/quitar CUITs de la lista CUITS.
En producción, usá variables de entorno o un JSON externo en lugar de hardcodear.
"""

import os
from dotenv import load_dotenv

load_dotenv()  # Carga .env si existe

# ──────────────────────────────────────────────
# Lista de clientes a scrapear
# Podés cargar esto desde un JSON o base de datos
# ──────────────────────────────────────────────
CUITS = [
    {
        "cuit": "23348079719",
        "password": "Wolfcris2025",
        "razon_social": "Cristian De Benedectis",
        "empresas": [
            {"cuit": "30716583445", "razon_social": "Wolf Travel S.A."},
            {"cuit": "30719185653", "razon_social": "LANTIER S.A."},
            {"cuit": "30709590657", "razon_social": "ARAMENDI Y ASOCIADOS SOCIEDAD ANONIMA"},
            {"cuit": "30717781984", "razon_social": "FAMILY GROUP S.A."},
        ],
    },
    # ── Formato simple (1 empresa sin lista) ──────────────────────────────
    # {
    #     "cuit": "20111111111",
    #     "password": "clave",
    #     "razon_social": "Mi Empresa SRL",
    #     "cuit_representacion": "30111111112",  # opcional
    # },
    #
    # ── Formato multi-empresa ────────────────────────────────────────────
    # {
    #     "cuit": "20222222222",
    #     "password": "clave",
    #     "razon_social": "Dueño",
    #     "empresas": [
    #         {"cuit": "30222222223", "razon_social": "Empresa A S.A."},
    #         {"cuit": "30222222224", "razon_social": "Empresa B S.R.L."},
    #     ],
    # },
]

# ──────────────────────────────────────────────
# Configuración general
# ──────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "arca_scraper.db")

# Período a scrapear (formato ARCA: dd/mm/yyyy - dd/mm/yyyy)
PERIODO_DESDE = os.getenv("PERIODO_DESDE", "01/01/2025")
PERIODO_HASTA = os.getenv("PERIODO_HASTA", "31/12/2025")

# Playwright: True = sin ventana (servidor), False = con ventana (debug)
HEADLESS = os.getenv("HEADLESS", "True").lower() == "true"

# Segundos de espera máxima para elementos de ARCA (puede ser lento)
TIMEOUT_MS = 30_000

# Carpeta donde se guardan los XMLs/TXTs descargados de ARCA
DOWNLOAD_DIR = os.path.abspath("downloads")
