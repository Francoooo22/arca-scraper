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
import os
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


def _detectar_pantalla_personas(popup):
    """Detecta si estamos en la pantalla de selección de persona."""
    return (popup.locator("#idcontribuyente").count() > 0
            or popup.locator("h3:has-text('REPRESENTAR A')").count() > 0
            or popup.locator('[title="Cambiar persona representada"]').count() > 0)


def seleccionar_persona(popup, cuit: str, razon_social: str = ""):
    """
    Si aparece la pantalla 'Elegí una persona para ingresar' / 'REPRESENTAR A:',
    selecciona la empresa indicada.
    """
    if not _detectar_pantalla_personas(popup):
        return

    print(f"  [PERSONA] Seleccionando: {razon_social or cuit}...")
    cuit_fmt = _cuit_con_guiones(cuit)

    link = popup.locator(f"a:has-text('{cuit_fmt}')").first
    if link.count() > 0:
        link.click()
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

    print(f"  [PERSONA] ✓ Seleccionada. URL: {popup.url}")


def cambiar_persona_representada(popup, cuit: str, razon_social: str = ""):
    """
    Click en 'Cambiar persona representada' y selecciona otra empresa
    sin necesidad de reloguear.
    """
    print(f"  [SWITCH] Cambiando a: {razon_social or cuit}...")
    btn = popup.locator('[title="Cambiar persona representada"]').first
    btn.wait_for(timeout=TIMEOUT_MS)
    btn.click()
    popup.wait_for_timeout(1500)

    cuit_fmt = _cuit_con_guiones(cuit)
    link = popup.locator(f"a:has-text('{cuit_fmt}')").first
    link.wait_for(timeout=TIMEOUT_MS)
    link.click()

    try:
        popup.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
        popup.wait_for_timeout(2000)
    except Exception:
        pass

    print(f"  [SWITCH] ✓ Cambiado a: {razon_social or cuit}")


def _set_fecha_range(popup, desde: str, hasta: str):
    """Setea el rango de fechas usando jQuery.daterangepicker API."""
    try:
        # Intentar vía jQuery + daterangepicker (actualiza estado interno)
        ok = popup.evaluate(f"""() => {{
            const $el = $('#fechaEmision');
            if ($el.length && $el.data('daterangepicker')) {{
                const drp = $el.data('daterangepicker');
                // moment reconoce DD/MM/YYYY con parse format explícito
                drp.setStartDate(moment('{desde}', 'DD/MM/YYYY'));
                drp.setEndDate(moment('{hasta}', 'DD/MM/YYYY'));
                return true;
            }}
            // Fallback: jQuery val + trigger
            if ($el.length) {{
                $el.val('{desde} - {hasta}').trigger('change');
                return true;
            }}
            return false;
        }}""")
        if ok:
            popup.wait_for_timeout(500)
            return True

        # Último recurso: native value
        fe = popup.locator("#fechaEmision")
        if fe.count() > 0:
            fe.fill(f"{desde} - {hasta}")
            return True

        return False
    except Exception as e:
        print(f"  [FILTRO] Warning: {e}")
        return False


def buscar_comprobantes(popup, url_servicio: str, desde: str, hasta: str) -> bool:
    """
    Navega a la URL del servicio, setea el rango de fechas y hace click en Buscar.
    """
    print(f"  [NAV] Navegando a {url_servicio.split('/')[-1]}...")
    popup.goto(url_servicio, wait_until="domcontentloaded")
    popup.wait_for_timeout(3000)

    print(f"  [FILTRO] Filtrando por período: {desde} → {hasta}")
    try:
        # Esperar a que cargue el formulario
        popup.wait_for_selector("#buscarComprobantes", timeout=TIMEOUT_MS)
        popup.wait_for_timeout(1000)

        # Intentar daterangepicker primero; fallback a fechaEmision
        if not _set_fecha_range(popup, desde, hasta):
            try:
                popup.evaluate(
                    f"document.getElementById('fechaEmision').value = '{desde} - {hasta}';"
                )
                popup.wait_for_timeout(300)
            except Exception:
                pass

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


def _extraer_datatable(popup) -> list[list[str]]:
    """Extrae las filas completas de la DataTable vía su API JavaScript."""
    try:
        data = popup.evaluate("""
            () => {
                const table = $('#tablaDataTables').DataTable();
                if (!table) return null;
                try {
                    const ex = table.buttons.exportData({ modifier: { page: 'all' } });
                    return { header: ex.header, body: ex.body };
                } catch(e) {
                    return { header: [], body: [] };
                }
            }
        """)
        if not data or not isinstance(data, dict) or not data.get("body"):
            return []
        body = data["body"]
        if not isinstance(body, list):
            return []
        return body
    except Exception as e:
        print(f"  [DATATABLE] Error: {e}")
        return []


def descargar_csv(popup) -> bytes:
    """Extrae datos de la DataTable y los convierte a CSV en memoria."""
    print("  [CSV] Extrayendo datos de DataTable...")
    try:
        filas = _extraer_datatable(popup) or []
    except Exception as e:
        print(f"  [CSV] ⚠ Error extrayendo DataTable: {e}")
        return b""
    if not isinstance(filas, list) or not filas:
        print("  [CSV] ⚠ Sin datos en la tabla.")
        return b""

    # Filtrar filas inválidas (DataTable vacía puede devolver [0])
    filas = [f for f in filas if isinstance(f, list)]

    if not filas:
        print("  [CSV] ⚠ Sin datos en la tabla.")
        return b""

    # DataTable column layout (52 cols):
    # 0=Fecha, 1=Tipo, 2=Número(PtoVta-NroDesde),
    # 3=PtoVta, 4=NroDesde, 5=NroHasta,
    # 6=CodAut, 7=TipoCodAut, 8=CAE,
    # 9-12=Emisor info, 13-15=Receptor info,
    # 16=TipoCambio, 17=Moneda,
    # 18-39=IVA detalle (pares raw/fmt),
    # 40=NetoGravTotal_raw, 42=NetoNoGrav_raw,
    # 44=Exentas_raw, 46=OtrosTrib_raw,
    # 48=TotalIVA_raw, 50=ImpTotal_raw
    #
    # Los valores raw ya son números limpios (e.g. "1824885.52"),
    # solo hay que reemplazar vacío/guion por "0"

    lineas = [";".join([""] * 30)]  # dummy header (el parser saltea línea 0)
    for f in filas:
        def nv(i):
            v = f[i] if len(f) > i else ""
            return "0" if not v or v == "-" else v

        cols_30 = [""] * 30
        cols_30[0]  = f[0] if len(f) > 0 else ""                        # Fecha
        cols_30[1]  = f[1].split(" - ")[0] if len(f) > 1 and " - " in f[1] else (f[1] if len(f) > 1 else "")  # Tipo
        cols_30[2]  = f[3] if len(f) > 3 else ""                        # PtoVta
        cols_30[3]  = f[4] if len(f) > 4 else ""                        # NroDesde
        cols_30[4]  = f[5] if len(f) > 5 else ""                        # NroHasta
        cols_30[5]  = f[8] if len(f) > 8 else ""                        # CAE
        cols_30[6]  = f[10] if len(f) > 10 else ""                      # TipoDocEmisor
        cols_30[7]  = f[11] if len(f) > 11 else ""                      # NroDocEmisor (CUIT)
        cols_30[8]  = f[12].strip('"') if len(f) > 12 else ""           # DenomEmisor
        cols_30[9]  = f[14] if len(f) > 14 else ""                      # TipoDocReceptor
        cols_30[10] = f[15] if len(f) > 15 else ""                      # NroDocReceptor
        cols_30[11] = f[16] if len(f) > 16 else "1"                     # TipoCambio
        cols_30[12] = f[17] if len(f) > 17 else "PES"                   # Moneda
        cols_30[24] = nv(40)                                             # NetoTotal
        cols_30[25] = nv(42)                                             # NetoNoGrav
        cols_30[26] = nv(44)                                             # Exentas
        cols_30[27] = nv(46)                                             # OtrosTrib
        cols_30[28] = nv(48)                                             # TotalIVA
        cols_30[29] = nv(50)                                             # ImpTotal

        lineas.append(";".join(cols_30))

    csv_text = "\n".join(lineas)
    print(f"  [CSV] ✓ {len(filas)} filas extraídas ({len(csv_text)} bytes).")

    # Retornar CSV crudo (sin ZIP) igual que la original descargar_csv
    return csv_text.encode("utf-8")


def guardar_zip_arca(csv_bytes: bytes, empresa: str, tipo: str,
                     periodo: str, output_dir: str = "descargas_arca") -> str:
    """
    Guarda el CSV empaquetado en ZIP con la estructura de carpetas y naming
    que requiere el usuario:
      {output_dir}/{empresa}/{tipo}/{empresa}_{periodo}_AAAAMMDD.zip

    Retorna la ruta del ZIP guardado.
    """
    from datetime import date
    ts = date.today().strftime("%Y%m%d")
    safe_name = empresa.replace("/", "_").replace("\\", "_").strip()
    folder = os.path.join(output_dir, safe_name, tipo)
    os.makedirs(folder, exist_ok=True)

    filename = f"{safe_name}_{periodo}_{ts}.zip"
    path = os.path.join(folder, filename)

    import zipfile, io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("comprobantes.csv", csv_bytes.decode("utf-8", errors="replace"))

    with open(path, "wb") as f:
        f.write(buf.getvalue())

    print(f"  [ARCHIVO] ✓ ZIP guardado: {path} ({buf.tell()} bytes)")
    return path


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
    es_recibido = (tipo == "recibidos")
    tipo_op = "recibido" if es_recibido else "emitido"

    if not csv_bytes:
        print(f"  [PARSE] ⚠ 0 comprobantes {tipo_op}s (CSV vacío).")
        return []

    try:
        texto = csv_bytes.decode("utf-8", errors="replace")
    except Exception:
        texto = csv_bytes.decode("latin-1", errors="replace")
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
                try:
                    fecha = datetime.strptime(fecha_raw, "%d/%m/%Y").strftime("%d/%m/%Y")
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
                tipo: str = "emitidos", output_dir: str = None) -> list[dict]:
    """
    Scrapea los comprobantes de uno o más CUITs representados para el período.

    cuit_data : dict con keys:
        'cuit'         — CUIT del dueño (login)
        'password'     — clave fiscal del dueño
        'razon_social' — nombre del dueño (usa login CUIT si falta)
        'cuit_representacion' — CUIT único de empresa (opcional)
        'empresas'     — lista de dicts [{"cuit": ..., "razon_social": ...}]
                         si está presente, itera todas en una misma sesión
    tipo      : 'emitidos' | 'recibidos'
    output_dir: si se pasa, guarda los archivos ZIP organizados en carpetas
    Retorna   : lista de dicts listos para insertar en SQLite
    """
    cuit_login  = cuit_data["cuit"]
    password     = cuit_data["password"]
    desde        = desde or PERIODO_DESDE
    hasta        = hasta or PERIODO_HASTA
    url_servicio = URL_EMITIDOS if tipo == "emitidos" else URL_RECIBIDOS

    # Resolver lista de empresas a scrapear
    empresas = cuit_data.get("empresas")
    if not empresas:
        cuit_rep = cuit_data.get("cuit_representacion", cuit_login)
        razon    = cuit_data.get("razon_social", cuit_login)
        empresas = [{"cuit": cuit_rep, "razon_social": razon}]

    print(f"\n{'='*60}")
    print(f"  PROCESANDO [{tipo.upper()}] — {len(empresas)} empresa(s)")
    print(f"  Login: {cuit_login}  |  Período: {desde} → {hasta}")
    print(f"{'='*60}")

    todos_comprobantes = []
    errores = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            executable_path=os.path.join(os.path.dirname(__file__), "chromium-wrapper.sh"),
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
            login_arca(page, cuit_login, password)
            popup = abrir_mis_comprobantes(context, page)

            for i, emp in enumerate(empresas):
                cuit_rep    = emp["cuit"]
                razon_social = emp.get("razon_social", cuit_rep)

                print(f"\n  ── Empresa {i+1}/{len(empresas)}: {razon_social} ({cuit_rep}) ──")

                if i == 0:
                    seleccionar_persona(popup, cuit_rep, razon_social)
                else:
                    cambiar_persona_representada(popup, cuit_rep, razon_social)

                buscar_comprobantes(popup, url_servicio, desde, hasta)
                csv_bytes = descargar_csv(popup)
                comps = _parsear_csv(csv_bytes, cuit_rep, razon_social, tipo)
                todos_comprobantes.extend(comps)

                if output_dir and csv_bytes:
                    # desde = dd/mm/yyyy → periodo = yyyymm
                    periodo = desde[6:10] + desde[3:5]
                    guardar_zip_arca(csv_bytes, razon_social, tipo, periodo, output_dir)

        except LoginError:
            raise
        except Exception as e:
            print(f"  [ERROR] {e}")
            try:
                page.screenshot(path=f"debug_{cuit_login}_{tipo}.png")
            except Exception:
                pass
            raise
        finally:
            browser.close()

    return todos_comprobantes
