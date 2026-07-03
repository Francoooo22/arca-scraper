"""
scraper.py — Playwright scraper para ARCA (Mis Comprobantes emitidos y recibidos)

Flujo:
  1. Login en /contribuyente_/login.xhtml (CUIT) → contraseña
  2. Portal → click "Mis Comprobantes" → popup en fes.afip.gob.ar/mcmp
  3. Pantalla de selección de persona (representación) si aplica
  4. Navegar a comprobantesEmitidos.do o comprobantesRecibidos.do
  5. Setear #fechaEmision con "dd/mm/yyyy - dd/mm/yyyy" → click #buscarComprobantes
  6. Extraer datos de la DataTable vía JS → guardar como CSV/ZIP

Mejoras:
  - Reintentos automáticos (#3)
  - Soporte emitidos y recibidos (#1)
  - Bug fix: popup capturado con context.expect_event (#6)
  - Nota #4: fecha_vto_cae no está disponible en el CSV de ARCA; para obtenerla
    habría que hacer clic en cada fila individualmente (costoso) o usar WSFE web service.
"""

import io
import os
import re
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


def _click_cuit_en_pantalla(popup, cuit: str, contexto: str = ""):
    """
    Busca y hace click en un CUIT en la pantalla de selección de persona.
    Usa múltiples selectores como fallback.
    """
    cuit_fmt = _cuit_con_guiones(cuit)
    prefijo = f"  [{contexto}] " if contexto else "  "

    # Selectores en orden de preferencia
    selectores = [
        f"a.panel:has-text('{cuit_fmt}')",
        f"a:has-text('{cuit_fmt}')",
        f"a:has-text('{cuit}')",
        f"td:has-text('{cuit_fmt}')",
        f"td:has-text('{cuit}')",
        f"span:has-text('{cuit_fmt}')",
        f"span:has-text('{cuit}')",
    ]

    for sel in selectores:
        try:
            elem = popup.locator(sel).first
            if elem.count() > 0:
                elem.click()
                print(f"{prefijo}Click en: {sel}")
                return True
        except Exception:
            continue

    # Último recurso: submit del formulario
    print(f"{prefijo}Fallback: submit del formulario...")
    try:
        popup.evaluate(
            "document.getElementById('idcontribuyente').value='0';"
            "document.seleccionaEmpresaForm.submit();"
        )
        return True
    except Exception:
        return False


def seleccionar_persona(popup, cuit: str, razon_social: str = ""):
    """
    Si aparece la pantalla 'Elegí una persona para ingresar' / 'REPRESENTAR A:',
    selecciona la empresa indicada.
    """
    if not _detectar_pantalla_personas(popup):
        return

    print(f"  [PERSONA] Seleccionando: {razon_social or cuit}...")
    _click_cuit_en_pantalla(popup, cuit, "PERSONA")

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

    _click_cuit_en_pantalla(popup, cuit, "SWITCH")

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
    """Extrae las filas completas de la DataTable vía su API JavaScript.

    Estrategia:
      1. Intenta page.len(-1) para cargar todo de una
      2. Si falla o devuelve pocos resultados, itera página por página
    """
    all_rows = []

    try:
        # Método 1: intentar cargar todo de una
        popup.evaluate("""
            () => {
                try {
                    const table = $('#tablaDataTables').DataTable();
                    if (table) table.page.len(-1).draw();
                } catch(e) {}
            }
        """)
        popup.wait_for_timeout(3000)

        data = popup.evaluate("""
            () => {
                const table = $('#tablaDataTables').DataTable();
                if (!table) return null;
                try {
                    const rows = table.rows({ search: 'applied' }).data().toArray();
                    if (rows && rows.length > 0) {
                        const body = rows.map(r => {
                            if (Array.isArray(r)) return r.map(v => v == null ? '' : String(v));
                            if (typeof r === 'object') return Object.values(r).map(v => v == null ? '' : String(v));
                            return [String(r)];
                        });
                        return { body: body, metodo: 'rows' };
                    }
                } catch(e) {}
                return { body: [], metodo: 'empty' };
            }
        """)

        if data and isinstance(data, dict) and data.get("body"):
            body = data["body"]
            if isinstance(body, list) and len(body) > 5:
                print(f"  [DATATABLE] {len(body)} filas extraídas (método: page_all)")
                return body

        # Método 2: iterar página por página
        print(f"  [DATATABLE] Fallback: iterando páginas...")
        page_info = popup.evaluate("""
            () => {
                const table = $('#tablaDataTables').DataTable();
                if (!table) return null;
                // Restaurar paginación normal
                table.page.len(10).draw();
                const info = table.page.info();
                return { pages: info.pages, total: info.recordsDisplay };
            }
        """)
        if not page_info:
            return all_rows

        total_pages = page_info.get("pages", 0)
        total_records = page_info.get("total", 0)
        print(f"  [DATATABLE] {total_records} registros en {total_pages} páginas")

        for pg in range(total_pages):
            popup.evaluate(f"""
                () => {{
                    const table = $('#tablaDataTables').DataTable();
                    table.page({pg}).draw('page');
                }}
            """)
            popup.wait_for_timeout(1500)

            page_rows = popup.evaluate("""
                () => {
                    const table = $('#tablaDataTables').DataTable();
                    if (!table) return [];
                    const rows = table.rows({ page: 'current', search: 'applied' }).data().toArray();
                    return rows.map(r => {
                        if (Array.isArray(r)) return r.map(v => v == null ? '' : String(v));
                        if (typeof r === 'object') return Object.values(r).map(v => v == null ? '' : String(v));
                        return [String(r)];
                    });
                }
            """)
            if isinstance(page_rows, list):
                all_rows.extend(page_rows)
                if (pg + 1) % 6 == 0 or pg == total_pages - 1:
                    print(f"  [DATATABLE] Página {pg+1}/{total_pages} — {len(all_rows)} filas acumuladas")

        print(f"  [DATATABLE] ✓ {len(all_rows)} filas extraídas (paginado)")
        return all_rows

    except Exception as e:
        print(f"  [DATATABLE] Error: {e}")
        return all_rows if all_rows else []


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
                # Emitidos: mismo formato 30 cols generado por descargar_csv
                # col[7] = NroDocEmisor (nuestro CUIT, ignorar)
                # col[8] = DenomEmisor (nuestro nombre, ignorar)
                # col[9] = DenomReceptor (nombre del cliente)
                # col[10] = TipoDocReceptor
                # col[13] = NroDocReceptor (CUIT del cliente)
                # col[11] = TipoCambio, col[12] = Moneda
                # col[24] = Neto, col[28] = IVA, col[29] = Total
                if len(cols) < 30:
                    continue
                fecha_raw   = cols[0].strip().strip('"')
                tipo_cod    = cols[1].strip()
                pto_vta     = cols[2].strip().zfill(4)
                nro         = cols[3].strip().zfill(8)
                cae         = cols[5].strip()
                cuit_rec    = cols[13].strip()   # NroDocReceptor (CUIT del cliente)
                denom_rec   = cols[9].strip().strip('"')  # DenomReceptor (nombre cliente)
                tipo_cambio = _parse_float(cols[11]) or 1.0
                moneda      = cols[12].strip() or "PES"
                neto        = _parse_float(cols[24])
                iva         = _parse_float(cols[28])
                total       = _parse_float(cols[29])
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

def _descargar_archivo_arca(popup, empresa: str, tipo: str,
                             periodo: str, output_dir: str) -> str:
    """
    Descarga el archivo real de ARCA usando el botón de descarga.
    Intercepta la URL de descarga y usa fetch() desde JS (solución snap Chromium).
    Primero intenta Excel, si falla intenta CSV.
    Retorna la ruta del archivo guardado, o "" si no se pudo descargar.
    """
    safe_name = re.sub(r'[^\w\-]', '_', empresa).strip('_')
    folder = os.path.join(output_dir, safe_name, tipo)
    os.makedirs(folder, exist_ok=True)

    tc = "E" if tipo == "emitidos" else "R"

    # Verificar si hay datos
    info = popup.evaluate("""
        () => {
            const table = $('#tablaDataTables').DataTable();
            if (!table) return null;
            const info = table.page.info();
            return { total: info.recordsDisplay };
        }
    """)
    if not info or info.get("total", 0) == 0:
        print(f"  [DOWNLOAD] ⚠ Sin datos para descargar.")
        return ""

    total = info["total"]
    print(f"  [DOWNLOAD] {total} comprobantes para descargar.")

    # Obtener cookies de la sesión para el fetch
    cookies = popup.context.cookies()
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    # Para cada formato: Excel primero, CSV después
    for formato, boton_texto, tf in [("xls", "Excel", "xls"), ("csv", "CSV", "csv")]:
        print(f"  [DOWNLOAD] Intentando {boton_texto}...")

        try:
            btn = popup.locator(f"button:has-text('{boton_texto}')").first
            if btn.count() == 0:
                print(f"  [DOWNLOAD] Botón '{boton_texto}' no encontrado.")
                continue

            # Interceptar la URL de descarga
            captured_url = {"url": None}

            def on_request(request):
                if "descargarComprobantes.do" in request.url:
                    captured_url["url"] = request.url

            popup.on("request", on_request)

            # Click en el botón de descarga
            btn.click()
            popup.wait_for_timeout(3000)

            popup.remove_listener("request", on_request)

            if not captured_url["url"]:
                print(f"  [DOWNLOAD] ✗ No se capturó URL de descarga para {boton_texto}.")
                continue

            dl_url = captured_url["url"]
            print(f"  [DOWNLOAD] URL capturada: {dl_url[:80]}...")

            # Usar fetch() desde el navegador para descargar
            result = popup.evaluate(f"""
                async () => {{
                    try {{
                        const resp = await fetch("{dl_url}", {{
                            credentials: 'include',
                            headers: {{
                                'Cookie': '{cookie_str}'
                            }}
                        }});
                        if (!resp.ok) return {{ error: 'HTTP ' + resp.status }};
                        const contentType = resp.headers.get('content-type') || '';
                        const blob = await resp.blob();
                        const reader = new FileReader();
                        return new Promise((resolve) => {{
                            reader.onloadend = () => {{
                                resolve({{
                                    data: reader.result,
                                    size: blob.size,
                                    type: contentType,
                                    url: resp.url
                                }});
                            }};
                            reader.readAsDataURL(blob);
                        }});
                    }} catch(e) {{
                        return {{ error: e.message }};
                    }}
                }}
            """)

            if not result or result.get("error"):
                print(f"  [DOWNLOAD] ✗ Error fetch: {result}")
                continue

            data_url = result.get("data", "")
            if not data_url or "," not in data_url:
                print(f"  [DOWNLOAD] ✗ Respuesta vacía.")
                continue

            # Decodificar base64
            import base64
            _, b64data = data_url.split(",", 1)
            raw_bytes = base64.b64decode(b64data)

            if len(raw_bytes) < 100:
                print(f"  [DOWNLOAD] ✗ Archivo muy pequeño ({len(raw_bytes)} bytes), posible error.")
                continue

            # Determinar extensión del contenido
            # ARCA devuelve ZIP conteniendo CSV, guardarlo como .zip
            ext = "zip"
            filename = f"{safe_name}_{tipo}_{periodo}.{ext}"
            filepath = os.path.join(folder, filename)

            with open(filepath, "wb") as f:
                f.write(raw_bytes)

            filesize = os.path.getsize(filepath)
            print(f"  [DOWNLOAD] ✓ {boton_texto} guardado: {filepath} ({filesize:,} bytes)")
            return filepath

        except Exception as e:
            print(f"  [DOWNLOAD] ✗ Error con {boton_texto}: {e}")
            continue

    print(f"  [DOWNLOAD] ⚠ No se pudo descargar ningún archivo.")
    return ""


def _scrape_empresa_rangos(popup, cuit_rep: str, razon_social: str,
                           tipo: str, rangos: list[tuple[str, str]],
                           output_dir: str | None) -> list[dict]:
    """
    Para una empresa ya seleccionada en el popup, recorre todos los rangos
    de fechas en la misma sesión del navegador.
    Descarga archivos reales de ARCA (Excel o CSV).
    """
    url_servicio = URL_EMITIDOS if tipo == "emitidos" else URL_RECIBIDOS
    archivos = []

    for i, (desde, hasta) in enumerate(rangos):
        print(f"\n  ── Rango {i+1}/{len(rangos)}: {desde} → {hasta} ──")
        buscar_comprobantes(popup, url_servicio, desde, hasta)

        periodo = desde[6:10] + desde[3:5]

        if output_dir:
            filepath = _descargar_archivo_arca(popup, razon_social, tipo, periodo, output_dir)
            if filepath:
                archivos.append(filepath)

    return archivos


@con_reintentos(max_intentos=3, demora_seg=10, no_reintentar=(LoginError,))
def scrape_cuit(cuit_data: dict, desde: str = None, hasta: str = None,
                tipo: str = "emitidos", output_dir: str = None,
                rangos: list[tuple[str, str]] | None = None) -> list[str]:
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
    rangos    : lista de tuplas (desde, hasta) en formato dd/mm/yyyy
                si se pasa, itera cada rango en la misma sesión del navegador
    output_dir: carpeta donde guardar los archivos descargados
    Retorna   : lista de rutas de archivos descargados
    """
    cuit_login  = cuit_data["cuit"]
    password     = cuit_data["password"]
    desde        = desde or PERIODO_DESDE
    hasta        = hasta or PERIODO_HASTA

    if not rangos:
        rangos = [(desde, hasta)]

    empresas = cuit_data.get("empresas")
    if not empresas:
        cuit_rep = cuit_data.get("cuit_representacion", cuit_login)
        razon    = cuit_data.get("razon_social", cuit_login)
        empresas = [{"cuit": cuit_rep, "razon_social": razon}]

    print(f"\n{'='*60}")
    print(f"  PROCESANDO [{tipo.upper()}] — {len(empresas)} empresa(s)")
    print(f"  Login: {cuit_login}  |  Rangos: {len(rangos)} período(s)")
    for d, h in rangos:
        print(f"    • {d} → {h}")
    print(f"{'='*60}")

    todos_archivos = []

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

                archivos = _scrape_empresa_rangos(
                    popup, cuit_rep, razon_social, tipo, rangos, output_dir
                )
                todos_archivos.extend(archivos)

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

    return todos_archivos
