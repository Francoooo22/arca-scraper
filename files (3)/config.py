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
        "cuit": "20416440698",
        "razon_social": "Franco",
        "password": "Franco2026",
    },
    # Agregá más clientes acá...
]

# ──────────────────────────────────────────────
# Configuración general
# ──────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "arca_scraper.db")

# Período a scrapear (formato ARCA: dd/mm/yyyy - dd/mm/yyyy)
PERIODO_DESDE = os.getenv("PERIODO_DESDE", "01/01/2025")
PERIODO_HASTA = os.getenv("PERIODO_HASTA", "31/05/2025")

# Playwright: True = sin ventana (servidor), False = con ventana (debug)
HEADLESS = os.getenv("HEADLESS", "False").lower() == "true"

# Segundos de espera máxima para elementos de ARCA (puede ser lento)
TIMEOUT_MS = 30_000

# Carpeta donde se guardan los XMLs/TXTs descargados de ARCA
DOWNLOAD_DIR = os.path.abspath("downloads")
