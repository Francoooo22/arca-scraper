"""
scraper.py — Playwright scraper para ARCA (Mis Comprobantes emitidos y recibidos)

Flujo:
  1. Login en /contribuyente_/login.xhtml (CUIT) → contraseña
  2. Portal → click "Mis Comprobantes" → popup en fes.afip.gob.ar/mcmp
  3. Pantalla de selección de persona (representación) si aplica
  4. Navegar a comprobantesEmitidos.do o comprobantesRecibidos.do
  5. Setear #fechaEmision con "dd/mm/yyyy - dd/mm/yyyy" → click #buscarComprobantes
  6. Click botón CSV → descarga ZIP → extraer → parsear

Mejoras:
  - Reintentos automáticos (#3)
  - Soporte emitidos y recibidos (#1)
  - Bug fix: popup capturado con context.expect_event (#6)
  - Nota #4: fecha_vto_cae no está disponible en el CSV de ARCA; para obtenerla
    habría que hacer clic en cada fila individualmente (costoso) o usar WSFE web service.
"""

import io
import time
import zipfile
import functools
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from config import HEADLESS, TIMEOUT_MS, DOWNLOAD_DIR, PERIODO_DESDE, PERIODO_HASTA

URL_LOGIN_CUIT = "https://auth.afip.gob.ar/contribuyente_/login.xhtml"
URL_EMITIDOS   = "https://fes.afip.gob.ar/mcmp/jsp/comprobantesEmitidos.do"
URL_RECIBIDOS  = "https://fes.afip.gob.ar/mcmp/jsp/comprobantesRecibidos.do"

TIPOS_COMPROBANTE = {
    "1":  "Factura A",         "2":  "Nota de Débito A",      "3":  "Nota de Crédito A",
    "4":  "Recibo A",          "6":  "Factura B",              "7":  "Nota de Débito B",
    "8":  "Nota de Crédito B", "9":  "Recibo B",               "11": "Factura C",
    "12": "Nota de Débito C",  "13": "Nota de Crédito C",      "14": "Recibo C",
    "15": "Fact. Exportación", "19": "Factura E",
    "51": "FCE A",             "52": "ND FCE A",               "53": "NC FCE A",
    "54": "FCE B",             "55": "ND FCE B",               "56": "NC FCE B",
    "57": "FCE C",             "58": "ND FCE C",               "59": "NC FCE C",
}


class LoginError(Exception):
    """Credenciales incorrectas o login bloqueado — no tiene sentido reintentar."""


# ─── Retry decorator (#3) ─────────────────────────────────────────────────────

def con_reintentos(max_intentos: int = 3, demora_seg: int = 10,
                   no_reintentar: tuple = (LoginError,)):
    """
    Decora una función reintentándola hasta max_intentos veces ante excepciones.
    Las excepciones en no_reintentar se propagan de inmediato sin reintentar.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            ultimo_error = None
            for intento in range(1, max_intentos + 1):
                try:
                    return func(*args, **kwargs)
                except no_reintentar as e:
                    raise
                except Exception as e:
                    ultimo_error = e
                    if intento < max_intentos:
                        print(f"  [RETRY] Intento {intento}/{max_intentos} fallido: {e}")
                        print(f"  [RETRY] Reintentando en {demora_seg}s...")
                        time.sleep(demora_seg)
            raise ultimo_error
        return wrapper
    return decorator


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _cuit_con_guiones(cuit: str) -> str:
    """'20416440698' → '20-41644069-8'"""
    c = cuit.replace("-", "").strip()
    if len(c) == 11:
        return f"{c[:2]}-{c[2:10]}-{c[10]}"
    return cuit


def _derivar_periodo(fecha_str: str) -> str:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(fecha_str.strip(), fmt).strftime("%Y%m")
        except ValueError:
            continue
    return ""


def _parse_float(texto: str) -> float:
    limpio = texto.strip()
    if not limpio:
        return 0.0
    try:
        return float(limpio.replace(",", "."))
    except ValueError:
        return 0.0


# ─── Login ────────────────────────────────────────────────────────────────────

def login_arca(page, cuit: str, password: str) -> bool:
    """
    Login de dos pasos en ARCA.
    Paso 1: /login.xhtml → CUIT + Siguiente
    Paso 2: /loginClave.xhtml → contraseña + Ingresar
    """
    print(f"  [LOGIN] Navegando a ARCA para CUIT {cuit}...")
    page.goto(URL_LOGIN_CUIT, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)

    try:
        page.wait_for_selector("#F1\\:username", timeout=TIMEOUT_MS).fill(cuit)
        page.click("#F1\\:btnSiguiente")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)
    except PWTimeout:
        raise LoginError(f"No se encontró el campo CUIT en la página de login.")

    try:
        page.wait_for_selector("#F1\\:password", timeout=TIMEOUT_MS).fill(password)
        page.click("#F1\\:btnIngresar")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)
    except PWTimeout:
        raise LoginError("No se encontró el campo de contraseña.")

    if "login" in page.url.lower():
        raise LoginError(f"Credenciales incorrectas para CUIT {cuit}.")

    print(f"  [LOGIN] ✓ Login exitoso. URL: {page.url}")
    return True


# ─── Navegación ───────────────────────────────────────────────────────────────

def abrir_mis_comprobantes(context, page):
    """
    Click en 'Mis Comprobantes' del portal AFIP → captura el popup.
    Usa context.expect_event para capturar la nueva página de forma correcta (#6).
    """
    print("  [NAV] Abriendo 'Mis Comprobantes'...")
    page.wait_for_timeout(2000)

    try:
        link = page.locator("a:has-text('Mis Comprobantes')").first
        link.wait_for(timeout=TIMEOUT_MS)
        with context.expect_event("page", timeout=15000) as popup_info:
            link.click()
        popup = popup_info.value
    except Exception as e:
        raise RuntimeError(f"No se pudo abrir el popup de Mis Comprobantes: {e}")

    try:
        popup.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
        popup.wait_for_timeout(2000)
    except Exception:
        pass

    print(f"  [NAV] ✓ Popup abierto en: {popup.url}")
    return popup


def seleccionar_persona(popup, cuit: str):
    """
    Si aparece la pantalla 'Elegí una persona para ingresar', selecciona el propio CUIT.
    """
    if popup.locator("#idcontribuyente").count() == 0:
        return  # sin pantalla de selección

    print("  [PERSONA] Seleccionando persona...")
    cuit_fmt = _cuit_con_guiones(cuit)

    # Intentar click en el panel con el CUIT formateado (panel propio)
    panel = popup.locator(f"a.panel:has-text('{cuit_fmt}')").first
    if panel.count() > 0:
        panel.click()
    else:
        popup.evaluate(
            "document.getElementById('idcontribuyente').value='0';"
            "document.seleccionaEmpresaForm.submit();"
        )

    try:
        popup.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
        popup.wait_for_timeout(2000)
    except Exception:
        pass

    print(f"  [PERSONA] ✓ Persona seleccionada. URL: {popup.url}")


def buscar_comprobantes(popup, url_servicio: str, desde: str, hasta: str) -> bool:
    """
    Navega a la URL del servicio, setea el rango de fechas y hace click en Buscar.
    """
    print(f"  [NAV] Navegando a {url_servicio.split('/')[-1]}...")
    popup.goto(url_servicio, wait_until="domcontentloaded")
    popup.wait_for_timeout(3000)

    print(f"  [FILTRO] Filtrando por período: {desde} → {hasta}")
    try:
        popup.wait_for_selector("#fechaEmision", timeout=TIMEOUT_MS)
        popup.evaluate(
            f"document.getElementById('fechaEmision').value = '{desde} - {hasta}';"
        )
        popup.wait_for_timeout(300)
        popup.click("#buscarComprobantes")
        popup.wait_for_load_state("networkidle")
        popup.wait_for_timeout(5000)

        info = (popup.locator(".dataTables_info").first.text_content() or "").strip()
        print(f"  [FILTRO] ✓ {info}")
        return True
    except PWTimeout:
        raise RuntimeError("Timeout esperando la página de comprobantes.")
    except Exception as e:
        raise RuntimeError(f"Error aplicando filtros: {e}")


def descargar_csv(popup) -> bytes:
    """Click en botón CSV → descarga ZIP → retorna bytes del CSV."""
    print("  [CSV] Descargando CSV...")
    with popup.expect_download(timeout=30000) as dl_info:
        popup.locator("button:has-text('CSV')").first.click()
    download = dl_info.value

    with open(download.path(), "rb") as f:
        zip_bytes = f.read()

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        csv_bytes = z.read(z.namelist()[0])

    print(f"  [CSV] ✓ {len(csv_bytes)} bytes descargados.")
    return csv_bytes


# ─── Parsing CSV (#1) ─────────────────────────────────────────────────────────

def _parsear_csv(csv_bytes: bytes, cuit: str, razon_social: str,
                 tipo: str = "emitidos") -> list[dict]:
    """
    Parsea el CSV descargado de ARCA.

    EMITIDOS (28 columnas):
      0=FechaEmisión, 1=Tipo, 2=PtoVenta, 3=NroDesde, 4=NroHasta,
      5=CAE, 6=TipoDocRec, 7=NroDocRec(CUIT receptor), 8=DenomRec,
      9=TipoCambio, 10=Moneda, 11-21=IVA, 22=NetoTotal, 23=NetoNoGrav,
      24=Exentas, 25=OtrosTrib, 26=TotalIVA, 27=ImpTotal

    RECIBIDOS (30 columnas) — cols 6-10 diferentes:
      6=TipoDocEmisor, 7=NroDocEmisor(CUIT proveedor), 8=DenomEmisor,
      9=TipoDocReceptor, 10=NroDocReceptor(nuestro CUIT),
      11=TipoCambio, 12=Moneda, 13-23=IVA, 24=NetoTotal, 25=NetoNoGrav,
      26=Exentas, 27=OtrosTrib, 28=TotalIVA, 29=ImpTotal

    Para recibidos: almacenamos cuit_emisor = nuestro CUIT (para consultas
    consistentes), cuit_receptor = CUIT del proveedor, denominacion_receptor
    = nombre del proveedor.
    """
    try:
        texto = csv_bytes.decode("utf-8", errors="replace")
    except Exception:
        texto = csv_bytes.decode("latin-1", errors="replace")

    es_recibido = (tipo == "recibidos")
    tipo_op = "recibido" if es_recibido else "emitido"
    comprobantes = []

    for linea in texto.splitlines()[1:]:
        if not linea.strip():
            continue
        cols = linea.split(";")

        try:
            if es_recibido:
                if len(cols) < 30:
                    continue
                fecha_raw   = cols[0].strip().strip('"')
                tipo_cod    = cols[1].strip()
                pto_vta     = cols[2].strip().zfill(4)
                nro         = cols[3].strip().zfill(8)
                cae         = cols[5].strip()
                cuit_rec    = cols[7].strip()   # CUIT proveedor → va como receptor
                denom_rec   = cols[8].strip().strip('"')
                tipo_cambio = _parse_float(cols[11]) or 1.0
                moneda      = cols[12].strip() or "PES"
                neto        = _parse_float(cols[24])
                iva         = _parse_float(cols[28])
                total       = _parse_float(cols[29])
                cuit_emisor = cuit  # siempre nuestro CUIT como clave primaria
            else:
                if len(cols) < 28:
                    continue
                fecha_raw   = cols[0].strip().strip('"')
                tipo_cod    = cols[1].strip()
                pto_vta     = cols[2].strip().zfill(4)
                nro         = cols[3].strip().zfill(8)
                cae         = cols[5].strip()
                cuit_rec    = cols[7].strip()
                denom_rec   = cols[8].strip().strip('"')
                tipo_cambio = _parse_float(cols[9]) or 1.0
                moneda      = cols[10].strip() or "PES"
                neto        = _parse_float(cols[22])
                iva         = _parse_float(cols[26])
                total       = _parse_float(cols[27])
                cuit_emisor = cuit

            try:
                fecha = datetime.strptime(fecha_raw, "%Y-%m-%d").strftime("%d/%m/%Y")
            except ValueError:
                fecha = fecha_raw

            comprobantes.append({
                "cuit_emisor":           cuit_emisor,
                "razon_social":          razon_social,
                "fecha_comprobante":     fecha,
                "tipo_comprobante":      TIPOS_COMPROBANTE.get(tipo_cod, f"Tipo {tipo_cod}"),
                "punto_venta":           pto_vta,
                "numero":                nro,
                "cuit_receptor":         cuit_rec,
                "denominacion_receptor": denom_rec,
                "importe_neto":          neto,
                "importe_iva":           iva,
                "importe_total":         total,
                "moneda":                moneda,
                "tipo_cambio":           tipo_cambio,
                "cae":                   cae,
                "fecha_vto_cae":         "",   # no disponible en CSV de ARCA
                "estado":                "A",
                "periodo_fiscal":        _derivar_periodo(fecha),
                "tipo_operacion":        tipo_op,
            })
        except Exception:
            continue

    print(f"  [PARSE] ✓ {len(comprobantes)} comprobantes {tipo_op}s parseados.")
    return comprobantes


# ─── Función principal ────────────────────────────────────────────────────────

@con_reintentos(max_intentos=3, demora_seg=10, no_reintentar=(LoginError,))
def scrape_cuit(cuit_data: dict, desde: str = None, hasta: str = None,
                tipo: str = "emitidos") -> list[dict]:
    """
    Scrapea los comprobantes de un CUIT para el período indicado.

    cuit_data : dict con keys 'cuit', 'password', 'razon_social'
    tipo      : 'emitidos' | 'recibidos'
    Retorna   : lista de dicts listos para insertar en SQLite
    """
    cuit         = cuit_data["cuit"]
    password     = cuit_data["password"]
    razon_social = cuit_data.get("razon_social", cuit)
    desde        = desde or PERIODO_DESDE
    hasta        = hasta or PERIODO_HASTA
    url_servicio = URL_EMITIDOS if tipo == "emitidos" else URL_RECIBIDOS

    print(f"\n{'='*60}")
    print(f"  PROCESANDO [{tipo.upper()}]: {razon_social} ({cuit})")
    print(f"  Período: {desde} → {hasta}")
    print(f"{'='*60}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            executable_path="/snap/bin/chromium",
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            accept_downloads=True,
            locale="es-AR",
        )
        page = context.new_page()
        page.set_default_timeout(TIMEOUT_MS)

        try:
            login_arca(page, cuit, password)

            popup = abrir_mis_comprobantes(context, page)

            seleccionar_persona(popup, cuit)

            buscar_comprobantes(popup, url_servicio, desde, hasta)

            csv_bytes = descargar_csv(popup)

            return _parsear_csv(csv_bytes, cuit, razon_social, tipo)

        except LoginError:
            raise   # no capturar → el retry decorator la deja pasar
        except Exception as e:
            print(f"  [ERROR] {cuit} [{tipo}]: {e}")
            if HEADLESS:
                try:
                    page.screenshot(path=f"debug_{cuit}_{tipo}.png")
                except Exception:
                    pass
            raise   # re-lanzar para que el retry lo maneje
        finally:
            browser.close()
