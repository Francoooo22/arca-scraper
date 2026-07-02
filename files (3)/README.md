# ARCA Scraper — Comprobantes 2025

Scraper en Python + Playwright para descargar comprobantes **emitidos y recibidos**
desde el servicio "Mis Comprobantes" de ARCA (ex-AFIP). 100% gratuito, sin APIs de terceros.

Descarga mes a mes y guarda los archivos ZIP originales en carpetas organizadas
por empresa y tipo, listos para importar en sistemas de gestión.

---

## Instalación

```bash
cd arca-scraper
python -m venv venv
source venv/bin/activate           # Linux/Mac
pip install -r requirements.txt
playwright install chromium
```

En Ubuntu 26.04 con Chromium por snap, el scraper usa `chromium-wrapper.sh`
para ejecutar el binario del snap automáticamente.

---

## Configuración

Editá `config.py` con tus clientes:

```python
CUITS = [
    {
        "cuit": "20111111111",
        "password": "clave_fiscal",
        "razon_social": "Mi Empresa SRL",
        "cuit_representacion": "30111111112",  # opcional
        "empresas": [                           # o multi-empresa
            {"cuit": "30222222223", "razon_social": "Empresa A S.A."},
            {"cuit": "30222222224", "razon_social": "Empresa B S.R.L."},
        ],
    },
]

PERIODO_DESDE = "01/01/2025"
PERIODO_HASTA = "31/12/2025"
HEADLESS = True   # False para ver el navegador (debug)
```

Opcionalmente via `.env`:
```
PERIODO_DESDE=01/01/2025
PERIODO_HASTA=31/12/2025
HEADLESS=true
DB_PATH=arca_scraper.db
```

---

## Uso

```bash
# Scrapear solo emitidos (default)
python run.py

# Scrapear solo recibidos
python run.py --tipo recibidos

# Scrapear ambos
python run.py --tipo ambos

# Período específico
python run.py --desde 01/03/2025 --hasta 31/03/2025

# Solo un CUIT
python run.py --cuit 30716583445

# Paralelizar CUITs/meses
python run.py --workers 3

# Exportar DB a Excel o CSV (sin scrapear)
python run.py --export excel
python run.py --export csv

# Resumen por período
python run.py --resumen
```

---

## Archivos descargados

El scraper guarda los ZIP originales en:

```
descargas_arca/
  Wolf Travel S.A./
    recibidos/
      Wolf Travel S.A._202501_20260702.zip
      Wolf Travel S.A._202502_20260702.zip
      ...
    emitidos/   (idem)
  Otra Empresa/
    recibidos/
    emitidos/
```

Cada ZIP contiene `comprobantes.csv` en el formato exacto de ARCA (30 columnas),
listo para importar en sistemas de gestión sin modificaciones.

---

## Estructura del proyecto

```
arca_scraper/
├── config.py         # CUITs, credenciales, período, HEADLESS
├── db.py             # SQLite: schema, inserción, consultas, log
├── scraper.py        # Playwright: login, navegación, DataTable, ZIP
├── export.py         # Exportación consolidada a Excel / CSV
├── run.py            # Entry point CLI con workers y meses
├── requirements.txt
├── chromium-wrapper.sh  # Wrapper para Chromium snap (Ubuntu)
├── .env              # Config via variables de entorno (opcional)
├── arca_scraper.db   # SQLite con todos los comprobantes
└── descargas_arca/   # ZIPS originales de ARCA (generado)
```

---

## Notas

- ARCA no tiene captcha en el login de clave fiscal estándar
- Si el scraper deja de funcionar, actualizá los selectores CSS en `scraper.py`
- Usá `HEADLESS = False` para debuggear con el navegador visible
- Los comprobantes se insertan con `INSERT OR IGNORE` para evitar duplicados
- La DB usa `tipo_operacion = 'emitido' | 'recibido'` en una misma tabla
