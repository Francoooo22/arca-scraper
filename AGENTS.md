# AGENTS.md — ARCA ScrapON ~By Studio BP~

Este archivo contiene todo el contexto del proyecto para retomarlo sin perder memoria.

---

## Estado actual (Julio 2026)

El scraper **funciona y está operativo**. Se descargan comprobantes reales de ARCA
para Wolf Travel S.A. y LANTIER S.A. vía la web interface.

- **Web**: `http://localhost:5000` (Flask, thread con scraper)
- **GitHub**: `github.com/Francoooo22/arca-scraper.git` (branch `main`)
- **Último commit**: `beb20ae`
- **Archivos descargados**: `/home/pc_wolf_05/descargas_arca/` (accesible desde Windows vía `\\wsl.localhost\Ubuntu\home\pc_wolf_05\descargas_arca\`)

---

## Credenciales y CUITs

| Campo | Valor |
|---|---|
| CUIT login | `23348079719` (Cristian De Benedectis) |
| Wolf Travel S.A. | `30716583445` |
| LANTIER S.A. | `30719185653` |
| Contraseña | Se ingresa por la web (no está hardcodeada) |

---

## Arquitectura

```
arca-scraper/
├── files (3)/
│   ├── app.py                 # Flask backend (scraper thread + SSE log)
│   ├── scraper.py             # Playwright: login, navegación, extracción DataTable
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

## Flujo de la web

1. Usuario ingresa CUIT + clave fiscal en `http://localhost:5000`
2. Selecciona empresa (mi empresa / representante), tipo (recibidos/emitidos/ambos), meses
3. Flask lanza `_run_scraper()` en un thread daemon
4. Playwright abre Chromium → login ARCA → "Mis Comprobantes" → popup
5. Para cada mes: filtra fechas → extrae DataTable → guarda CSV/ZIP
6. Log en tiempo real vía SSE (Server-Sent Events)
7. Archivos guardados en `/home/pc_wolf_05/descargas_arca/{empresa}/{tipo}/`

---

## Problemas resueltos (historial)

### 1. `save_as()` no funciona con snap Chromium
- **Problema**: Playwright snap en Ubuntu 26.04 no permite leer archivos descargados
- **Solución**: Intercepta la URL `descargarComprobantes.do` y usa `fetch()` desde JS

### 2. LogCapture mataba el servidor Flask
- **Problema**: `sys.stdout = LogCapture()` global rompía Flask en threads
- **Solución**: `ThreadSafeLogCapture` con `io.TextIOBase` + `contextlib.redirect_stdout`

### 3. Paginación incompleta (Sep/Oct solo 5 filas)
- **Problema**: `buttons.exportData` solo tomaba la página visible
- **Solución**: Fuerza `table.page.len(-1).draw()` antes de extraer

### 4. Carpetas con puntos no accesibles desde Windows
- **Problema**: `LANTIER S. A.` con punto no se abre vía `\\wsl.localhost`
- **Solución**: `re.sub(r'[^\w\-]', '_', nombre)` — genera `LANTIER_SA`

### 5. Server moría después del error de login
- **Problema**: El thread del scraper crasheaba el proceso Flask
- **Solución**: `contextlib.redirect_stdout` en vez de reemplazar `sys.stdout` global

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
- 30 columnas
- Decimales con coma (`138978,73`)
- Fechas: `YYYY-MM-DD` o `dd/mm/yyyy`
- Guardado como ZIP conteniendo el CSV

### Extracción de DataTable
```javascript
// Fuerza mostrar todas las filas
table.page.len(-1).draw();
// Luego extrae
table.rows({ search: 'applied' }).data().toArray();
```

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

---

## Pendiente / Known issues

1. **Emitidos no descarga nada** — Wolf Travel no tiene comprobantes emitidos en ARCA (solo recibidos)
2. **Archivos LANTIER en zip** — El scraper guarda como ZIP, Wolf Travel como CSV sueltos (inconsistente)
3. **Sin auth en la web** — Cualquiera que acceda a `:5000` puede correr el scraper
4. **Server muere si nadie lo usa** — Flask se detiene, hay que reiniciar con `setsid`

---

## Cómo levantar el server

```bash
# Matar algo en puerto 5000
fuser -k 5000/tcp 2>/dev/null

# Iniciar
cd ~/arca-scraper/files\ \(3\)
setsid ~/arca-scraper/venv/bin/python app.py > /tmp/arca_flask.log 2>&1 &

# Verificar
curl -s http://localhost:5000/api/status
```

## Cómo correr el scraper sin la web

```bash
cd ~/arca-scraper/files\ \(3\)
~/arca-scraper/venv/bin/python run.py --tipo recibidos
```

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
