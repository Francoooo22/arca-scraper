# ARCA ScrapON ~By Studio BP~

Scraper para descargar comprobantes **emitidos y recibidos** desde ARCA (ex-AFIP).
Incluye interfaz web moderna con log en tiempo real.

---

## Inicio rápido

```bash
cd ~/arca-scraper/files\ \(3\)
./start.sh
```

Abrí **http://localhost:5000** en el navegador.

---

## Uso web

1. Ingresá tu **CUIT** y **clave fiscal**
2. Elegí empresa (mi empresa / representante)
3. Tipo de comprobante: recibidos / emitidos / ambos
4. Seleccioná los meses
5. Clic en "Descargar comprobantes"
6. Seguí el log en tiempo real

---

## Uso CLI

```bash
python run.py --tipo recibidos
python run.py --tipo emitidos
python run.py --tipo ambos
python run.py --desde 01/03/2025 --hasta 31/03/2025
```

---

## Archivos descargados

Se guardan en `/home/pc_wolf_05/descargas_arca/` (accesible desde Windows):

```
descargas_arca/
├── Wolf_Travel_SA/
│   ├── emitidos/
│   │   └── Wolf Travel S.A._emitidos_202501.csv
│   └── recibidos/
│       └── Wolf Travel S.A._recibidos_202501.csv
└── LANTIER_SA/
    └── recibidos/
        └── LANTIER S. A._recibidos_202501.zip
```

Desde Windows: `\\wsl.localhost\Ubuntu\home\pc_wolf_05\descargas_arca\`

---

## Configuración

Editar `config.py`:

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
HEADLESS = True
```

---

## Estructura

```
arca-scraper/
├── files (3)/
│   ├── app.py                 # Flask backend + scraper thread
│   ├── scraper.py             # Playwright: login, navegación, extracción
│   ├── run.py                 # CLI entry point
│   ├── config.py              # Configuración
│   ├── db.py                  # SQLite schema
│   ├── chromium-wrapper.sh    # Wrapper Chromium snap
│   ├── templates/index.html   # UI web
│   └── start.sh               # Launcher
├── venv/                      # Python 3.14 + Playwright
└── AGENTS.md                  # Contexto del proyecto
```

---

## Requisitos

- Python 3.14
- Playwright 1.60.0
- Chromium (snap en Ubuntu 26.04)
- Flask

---

## Notas

- Un solo login recorre todos los meses seleccionados
- El CSV tiene separador `;` y 30 columnas (formato nativo ARCA)
- Los nombres de carpetas se sanitizan (sin puntos ni espacios)
- Ver `AGENTS.md` para contexto completo del proyecto
