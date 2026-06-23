# ARCA Scraper — Comprobantes Emitidos

Scraper en Python + Playwright para descargar comprobantes emitidos desde
el servicio "Mis Comprobantes" de ARCA (ex-AFIP). 100% gratuito, sin APIs de terceros.

---

## Instalación

```bash
# 1. Clonar / copiar la carpeta
cd arca_scraper

# 2. Crear entorno virtual (recomendado)
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Instalar el navegador Chromium para Playwright
playwright install chromium
```

---

## Configuración

Editá `config.py` y cargá tus clientes:

```python
CUITS = [
    {
        "cuit": "20111111111",
        "razon_social": "Empresa SRL",
        "password": "clave_fiscal",
    },
    # ...
]

PERIODO_DESDE = "01/01/2025"
PERIODO_HASTA = "31/05/2025"
HEADLESS = False   # False = ver el navegador (recomendado para debug)
```

Opcionalmente podés usar un archivo `.env`:
```
PERIODO_DESDE=01/01/2025
PERIODO_HASTA=31/05/2025
HEADLESS=true
DB_PATH=arca_scraper.db
```

---

## Uso

```bash
# Scrapear todos los CUITs del período configurado
python run.py

# Scrapear un período específico
python run.py --desde 01/03/2025 --hasta 31/03/2025

# Scrapear solo un CUIT
python run.py --cuit 20111111111

# Solo exportar lo que ya está en la DB (sin scrapear)
python run.py --export excel
python run.py --export csv

# Ver resumen por período
python run.py --resumen

# Scrapear sin generar Excel al final
python run.py --no-export
```

---

## Estructura de archivos

```
arca_scraper/
├── config.py         # Lista de CUITs y configuración general
├── db.py             # SQLite: schema, insert, consulta
├── scraper.py        # Playwright: login ARCA + parseo de tabla
├── export.py         # Exportación a Excel / CSV
├── run.py            # Entry point con argumentos CLI
├── requirements.txt
├── arca_scraper.db   # (generado automáticamente)
└── downloads/        # (para futura descarga de PDFs)
```

---

## ⚠ Notas importantes

### Selectores de ARCA pueden cambiar
ARCA actualiza su frontend periódicamente. Si el scraper deja de funcionar,
inspeccioná el elemento en el navegador (F12) y actualizá los selectores
en `scraper.py`.

### Debug con HEADLESS=False
Mientras probás, dejá `HEADLESS = False` en `config.py` para ver qué hace
el navegador y detectar problemas de login, captchas, o cambios en la UI.

### Screenshots de debug
Cuando `HEADLESS = True`, el scraper guarda un screenshot `debug_{cuit}.png`
por cada CUIT procesado para poder diagnosticar problemas.

### Captcha
ARCA no tiene captcha en el login de clave fiscal estándar. Si aparece,
puede ser por múltiples intentos fallidos — esperá unos minutos.

### Nacional Software / integración
El CSV exportado puede importarse directamente en la mayoría de ERPs.
La DB SQLite puede consultarse desde Python/pandas para generar reportes
o cruzar con datos de Nacional Software via SQL.

---

## Control de exclusión (uso avanzado)

Los comprobantes tienen el campo `incluido_ddjj` (0/1). Podés marcarlos
manualmente o via script después de presentar cada DDJJ:

```python
import sqlite3
conn = sqlite3.connect("arca_scraper.db")
conn.execute("""
    UPDATE comprobantes_emitidos
    SET incluido_ddjj = 1
    WHERE periodo_fiscal = '202503' AND cuit_emisor = '20111111111'
""")
conn.commit()
```

---

## Próximos módulos (roadmap)

- [ ] `scraper_retenciones.py` — Mis Retenciones / Percepciones
- [ ] `scraper_recibidos.py`   — Mis Comprobantes Recibidos
- [ ] `scraper_sifere.py`      — SIFERE IIBB retenciones
- [ ] `api.py`                 — Flask REST API para integración con otras apps
