# ARCA Scraper — Comprobantes 2025

Scraper en Python + Playwright para descargar comprobantes **emitidos y recibidos**
desde el servicio "Mis Comprobantes" de ARCA (ex-AFIP). Descarga los archivos
reales tal cual los genera ARCA (CSV o Excel).

---

## Instalación

```bash
cd arca-scraper
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

En Ubuntu 26.04 con Chromium por snap, el scraper usa `chromium-wrapper.sh`.

---

## Configuración

Editá `config.py` con tus clientes:

```python
CUITS = [
    {
        "cuit": "23348079719",
        "password": "clave_fiscal",
        "razon_social": "Cristian De Benedectis",
        "empresas": [
            {"cuit": "30716583445", "razon_social": "Wolf Travel S.A."},
        ],
    },
]

PERIODO_DESDE = "01/01/2025"
PERIODO_HASTA = "31/12/2025"
HEADLESS = True   # False para ver el navegador (debug)
```

---

## Uso

```bash
# Emitidos (default)
python run.py

# Recibidos
python run.py --tipo recibidos

# Ambos
python run.py --tipo ambos

# Período específico
python run.py --desde 01/03/2025 --hasta 31/03/2025

# Solo un CUIT
python run.py --cuit 30716583445
```

---

## Archivos descargados

Los archivos se guardan como CSVs reales de ARCA en:

```
descargas_arca/
  Wolf Travel S.A.
    emitidos/
      Wolf Travel S.A._emitidos_202501.csv
      Wolf Travel S.A._emitidos_202502.csv
      ...
    recibidos/
      Wolf Travel S.A._recibidos_202501.csv
      Wolf Travel S.A._recibidos_202502.csv
      ...
```

El formato CSV es el exacto de ARCA (separador `;`, 30 columnas), listo para
importar en sistemas de gestión.

---

## Estructura del proyecto

```
arca-scraper/
├── config.py              # CUITs, credenciales, período, HEADLESS
├── scraper.py             # Playwright: login, navegación, descarga real
├── run.py                 # Entry point CLI
├── db.py                  # SQLite: schema y log de ejecuciones
├── export.py              # Exportación a Excel / CSV (consolidado)
├── requirements.txt
├── chromium-wrapper.sh    # Wrapper para Chromium snap (Ubuntu)
└── descargas_arca/        # Archivos descargados de ARCA
```

---

## Cómo funciona la descarga

1. Intenta descargar **Excel** primero (`button:has-text('Excel')`)
2. Si no está disponible, descarga **CSV** (`button:has-text('CSV')`)
3. Intercepta la URL de descarga (`descargarComprobantes.do`)
4. La ejecuta via `fetch()` con las cookies de la sesión del navegador
5. Guarda el archivo tal cual lo da ARCA

**Por qué no usa `download.save_as()` de Playwright:** El snap Chromium en
Ubuntu 26.04 no permite que Playwright lea los archivos descargados. La
solución es usar `fetch()` desde JavaScript dentro del navegador.

---

## Notas

- ARCA no tiene captcha en el login de clave fiscal estándar
- Un solo login recorre todos los meses (no reloguea por mes)
- Si el scraper deja de funcionar, actualizá los selectores CSS en `scraper.py`
- Usá `HEADLESS = False` para debuggear con el navegador visible
- Los archivos de emitidos pueden estar vacíos si la empresa no emite por ARCA
