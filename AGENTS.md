# AGENTS.md — ARCA ScrapON ~By Studio BP~

Este archivo contiene todo el contexto del proyecto para retomarlo sin perder memoria.

---

## Estado actual (Julio 2026)

El scraper **funciona y está operativo**. Se descargan comprobantes reales de ARCA
vía la web interface. Archivos reales descargados del portal (CSV/ZIP).

- **Web**: `http://localhost:5000` (Flask, thread con scraper)
- **GitHub**: `github.com/Francoooo22/arca-scraper.git` (branch `main`)
- **Archivos descargados**: `/home/pc_wolf_05/descargas_arca/` (accesible desde Windows vía `\\wsl.localhost\Ubuntu\home\pc_wolf_05\descargas_arca\`)

---

## Credenciales y CUITs

| Campo | Valor |
|---|---|
| CUIT login | `23348079719` (Cristian De Benedectis) |
| Contraseña | `Wolfcris2025` (hardcodeada en config.py) |
| Wolf Travel S.A. | `30716583445` (empresa propia) |
| LANTIER S.A. | `30719185653` (representación) |
| ARAMENDI Y ASOCIADOS S.A. | `30709590657` (representación) |

### Configuración en `config.py`

```python
CUITS = [
    {
        "cuit": "23348079719",
        "password": "Wolfcris2025",
        "razon_social": "Cristian De Benedectis",
        "empresas": [
            {"cuit": "30716583445", "razon_social": "Wolf Travel S.A."},
            {"cuit": "30719185653", "razon_social": "LANTIER S.A."},
            {"cuit": "30709590657", "razon_social": "ARAMENDI Y ASOCIADOS SOCIEDAD ANONIMA"},
        ],
    },
]
```

---

## Arquitectura

```
arca-scraper/
├── files (3)/
│   ├── app.py                 # Flask backend (scraper thread + SSE log)
│   ├── scraper.py             # Playwright: login, navegación, descarga real
│   ├── run.py                 # Entry point CLI (alternativo a la web)
│   ├── config.py              # Configuración (CUITS, PERIODO, HEADLESS)
│   ├── db.py                  # SQLite schema + scrape_log
│   ├── export.py              # Exportación Excel/CSV (no usado actualmente)
│   ├── chromium-wrapper.sh    # Wrapper para Chromium snap Ubuntu
│   ├── templates/
│   │   └── index.html         # UI web moderna
│   ├── start.sh               # Launcher para escritorio
│   └── descargas_arca/        # (legacy, ahora se usa ~/descargas_arca/)
├── venv/                      # Python 3.14 + playwright 1.60.0
└── AGENTS.md                  # Este archivo
```

---

## Flujo de descarga (IMPORTANTE)

### Método actual: Descarga real vía botón de ARCA

```
1. Login en ARCA → Portal → "Mis Comprobantes" → Popup
2. Seleccionar empresa (si hay representación)
3. Navegar a comprobantesEmitidos.do o comprobantesRecibidos.do
4. Setear rango de fechas con jQuery.daterangepicker
5. Click en "Buscar" → la DataTable muestra resultados
6. ESCUCHAR requests en el popup para capturar la URL de descarga
7. Click en botón "CSV" de ARCA
8. Capturar la URL de descargarComprobantes.do del request
9. Usar fetch() desde JS con las cookies de la sesión para descargar
10. Decodificar base64 y guardar como .zip
```

### Por qué NO se usa el botón Excel

El botón Excel de ARCA no dispara un request `descargarComprobantes.do`,
sino que genera el archivo internamente. Solo el botón CSV genera la URL
de descarga que podemos interceptar.

### Código clave de descarga (`scraper.py`)

```python
# 1. Escuchar requests
captured_url = {"url": None}
def on_request(request):
    if "descargarComprobantes.do" in request.url:
        captured_url["url"] = request.url
popup.on("request", on_request)

# 2. Click en botón CSV
btn = popup.locator("button:has-text('CSV')").first
btn.click()

# 3. Descargar vía fetch() con cookies
cookies = popup.context.cookies()
cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
result = popup.evaluate(f"""
    async () => {{
        const resp = await fetch("{url}", {{
            credentials: 'include',
            headers: {{ 'Cookie': '{cookie_str}' }}
        }});
        const blob = await resp.blob();
        const reader = new FileReader();
        return new Promise((resolve) => {{
            reader.onloadend = () => resolve({{
                data: reader.result, size: blob.size
            }});
            reader.readAsDataURL(blob);
        }});
    }}
""")

# 4. Guardar como .zip
import base64
_, b64data = result["data"].split(",", 1)
raw_bytes = base64.b64decode(b64data)
# Guardar como .zip porque ARCA devuelve ZIPs
```

---

## Cómo correr el scraper

### Desde la web (Flask)
```bash
fuser -k 5000/tcp 2>/dev/null
cd ~/arca-scraper/files\ \(3\)
setsid ~/arca-scraper/venv/bin/python app.py > /tmp/arca_flask.log 2>&1 &
```

### Desde la CLI (recomendado para automatización)

```bash
cd ~/arca-scraper/files\ \(3\)

# Todas las empresas, emitidos
~/arca-scraper/venv/bin/python run.py --tipo emitidos

# Solo recibidos
~/arca-scraper/venv/bin/python run.py --tipo recibidos

# Emitidos + recibidos
~/arca-scraper/venv/bin/python run.py --tipo ambos

# Empresa específica por CUIT (NO usa menú interactivo)
~/arca-scraper/venv/bin/python run.py --cuit 30709590657 --tipo ambos

# Período específico
~/arca-scraper/venv/bin/python run.py --cuit 30709590657 --tipo ambos \
    --desde 01/01/2025 --hasta 30/06/2026
```

**IMPORTANTE**: Siempre usar `--cuit` para evitar el menú interactivo que
pendea cuando hay más de una empresa configurada. Sin `--cuit`, el script
pide input por stdin y se cuelga en ejecución no interactiva.

### Resumen de archivos descargados
```bash
~/arca-scraper/venv/bin/python run.py --resumen
```

---

## Problemas resueltos (historial)

### 1. `save_as()` no funciona con snap Chromium
- **Problema**: Playwright snap en Ubuntu 26.04 no permite leer archivos descargados
- **Solución**: Intercepta la URL `descargarComprobantes.do` y usa `fetch()` desde JS
- **Archivo**: `scraper.py` → `_descargar_archivo_arca()`

### 2. LogCapture mataba el servidor Flask
- **Problema**: `sys.stdout = LogCapture()` global rompía Flask en threads
- **Solución**: `ThreadSafeLogCapture` con `io.TextIOBase` + `contextlib.redirect_stdout`

### 3. Paginación incompleta (Sep/Oct solo 5 filas)
- **Problema**: `buttons.exportData` solo tomaba la página visible
- **Solución**: Ahora se descarga el archivo real de ARCA, no se extrae la DataTable

### 4. Carpetas con puntos no accesibles desde Windows
- **Problema**: `LANTIER S. A.` con punto no se abre vía `\\wsl.localhost`
- **Solución**: `re.sub(r'[^\w\-]', '_', nombre)` — genera `LANTIER_SA`

### 5. Server moría después del error de login
- **Problema**: El thread del scraper crasheaba el proceso Flask
- **Solución**: `contextlib.redirect_stdout` en vez de reemplazar `sys.stdout` global

### 6. Menú interactivo colgaba en CLI
- **Problema**: `run.py` tenía `input()` para elegir empresa; en ejecución no
  interactiva se quedaba pidiendo input indefinidamente
- **Solución**: Usar `--cuit` para filtrar sin preguntar. Se agregó:
  ```python
  if not args.cuit:
      cuits_a_procesar = _seleccionar_empresas(cuits_a_procesar)
  ```

### 7. Extracción de DataTable fallaba con miles de registros
- **Problema**: `page.len(-1).draw()` no cargaba todas las filas con 1.000+ registros
- **Solución**: Se cambió a descarga real del archivo de ARCA (no más extracción vía JS)

### 8. Botón Excel no captura URL de descarga
- **Problema**: El botón Excel de ARCA genera el archivo internamente sin
  disparar el request `descargarComprobantes.do`
- **Solución**: Se usa botón CSV que sí genera la URL interceptable

---

## Notas técnicas importantes

### Chromium wrapper
```bash
#!/bin/bash
exec snap run chromium "$@"
```
- Playwright NO soporta chromium en Ubuntu 26.04 (`playwright install chromium` falla)
- Se usa el snap de Chromium como reemplazo
- Se invoca vía `executable_path` en `p.chromium.launch()`

### Formato CSV de ARCA
- Separador: `;` (punto y coma)
- 30 columnas (recibidos) o 28 columnas (emitidos)
- Decimales con coma (`138978,73`)
- Fechas: `YYYY-MM-DD` o `dd/mm/yyyy`
- Guardado como **ZIP conteniendo el CSV** (ARCA siempre devuelve ZIPs)

### Login ARCA (2 pasos)
1. `/login.xhtml` → CUIT + "Siguiente"
2. `/loginClave.xhtml` → contraseña + "Ingresar"
3. Verificar que URL no contenga "login" (si la contiene, falló)

### URLs del portal
```
Login:        https://auth.afip.gob.ar/contribuyente_/login.xhtml
Portal:       https://portalcf.cloud.afip.gob.ar/portal/app/
Popup:        https://fes.afip.gob.ar/mcmp/jsp/index.do
Emitidos:     https://fes.afip.gob.ar/mcmp/jsp/comprobantesEmitidos.do
Recibidos:    https://fes.afip.gob.ar/mcmp/jsp/comprobantesRecibidos.do
Descarga:     https://fes.afip.gob.ar/mcmp/jsp/descargarComprobantes.do?id={id}&tc={R|E}&tf={csv|xls}
```
- `tc=R` para recibidos, `tc=E` para emitidos
- `tf=csv` o `tf=xls`

### Botones de descarga en ARCA
- **Excel**: No dispara request `descargarComprobantes.do` → no se puede interceptar
- **CSV**: Sí dispara el request → se puede interceptar y descargar con fetch()
- **PDF**: No es útil para procesamiento

---

## Pendiente / Known issues

1. **Botón Excel no funciona** — Solo CSV genera URL interceptable. No hay forma
   de descargar el Excel real de ARCA desde Playwright.
2. **Sin auth en la web** — Cualquiera que acceda a `:5000` puede correr el scraper
3. **Server muere si nadie lo usa** — Flask se detiene, hay que reiniciar con `setsid`
4. **Output de Playwright se bufferiza** — Siempre usar `-u` flag en Python:
   `python -u script.py` para ver output en tiempo real

---

## Git

```bash
cd ~/arca-scraper
git add -A
git commit -m "mensaje"
git push origin main
```

Archivos ignorados por `.gitignore`: `*.html`, `*.csv`, `*.zip`, `*.png`, `venv/`, `__pycache__/`, `*.db`
(Se usa `git add -f` para templates HTML)
