"""
run.py — Entry point del scraper ARCA

Uso:
    python run.py                                    # emitidos, todos los CUITs
    python run.py --tipo recibidos                   # solo recibidos
    python run.py --tipo ambos                       # emitidos + recibidos
    python run.py --desde 01/01/2025 --hasta 31/03/2025
    python run.py --cuit 20111111111
    python run.py --workers 3                        # 3 CUITs en paralelo (#5)
    python run.py --export excel
    python run.py --resumen
"""

import argparse
import sys
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import CUITS, PERIODO_DESDE, PERIODO_HASTA
from db import init_db, insertar_muchos, log_inicio, log_fin
from scraper import scrape_cuit, LoginError
from export import exportar_excel, exportar_csv, resumen_por_periodo


def parsear_args():
    parser = argparse.ArgumentParser(description="ARCA Scraper — Comprobantes")
    parser.add_argument("--cuit",      type=str, help="Scrapear solo este CUIT")
    parser.add_argument("--desde",     type=str, help="Fecha desde (dd/mm/yyyy)")
    parser.add_argument("--hasta",     type=str, help="Fecha hasta (dd/mm/yyyy)")
    parser.add_argument("--tipo",      type=str, default="emitidos",
                        choices=["emitidos", "recibidos", "ambos"],
                        help="Tipo de comprobantes a scrapear (default: emitidos)")
    parser.add_argument("--workers",   type=int, default=1,
                        help="CUITs a procesar en paralelo (default: 1) (#5)")
    parser.add_argument("--export",    type=str, choices=["excel", "csv"],
                        help="Exportar datos existentes sin scrapear")
    parser.add_argument("--resumen",   action="store_true",
                        help="Mostrar resumen de comprobantes por período")
    parser.add_argument("--no-export", action="store_true",
                        help="Solo scrapear, no exportar al final")
    return parser.parse_args()


def mostrar_resumen(cuit_filtro: str = None):
    print("\n" + "="*70)
    print("  RESUMEN POR PERÍODO")
    print("="*70)
    rows = resumen_por_periodo(cuit=cuit_filtro)
    if not rows:
        print("  Sin datos en la base.")
        return
    print(f"  {'CUIT':<16} {'Razón Social':<22} {'Tipo':<10} {'Per.':<8} {'Cant':>5} {'Total':>15}")
    print("  " + "-"*75)
    for r in rows:
        print(
            f"  {r['cuit_emisor']:<16} "
            f"{(r['razon_social'] or '')[:21]:<22} "
            f"{r['tipo_operacion']:<10} "
            f"{r['periodo_fiscal']:<8} "
            f"{r['cantidad']:>5} "
            f"${r['total_general']:>14,.2f}"
        )
    print("="*70)


def _tarea(cuit_data, desde, hasta, tipo):
    """Ejecuta scrape_cuit para un CUIT/tipo y retorna (cuit_data, tipo, resultado)."""
    try:
        comprobantes = scrape_cuit(cuit_data, desde=desde, hasta=hasta, tipo=tipo, output_dir="descargas_arca")
        return cuit_data, tipo, comprobantes, None
    except LoginError as e:
        return cuit_data, tipo, [], str(e)
    except Exception as e:
        return cuit_data, tipo, [], str(e)


def _generar_meses(desde_str: str, hasta_str: str):
    """
    Genera tuplas (desde_mes, hasta_mes) en formato dd/mm/yyyy
    para cada mes calendario entre desde_str y hasta_str.
    Ej: '15/01/2025' - '10/03/2025' → ('15/01/2025','31/01/2025'),
                                       ('01/02/2025','28/02/2025'),
                                       ('01/03/2025','10/03/2025')
    """
    desde = datetime.strptime(desde_str, "%d/%m/%Y")
    hasta = datetime.strptime(hasta_str, "%d/%m/%Y")

    current = desde.replace(day=1)

    while current <= hasta.replace(day=1):
        # Primer día del próximo mes
        if current.month == 12:
            next_month = current.replace(year=current.year + 1, month=1)
        else:
            next_month = current.replace(month=current.month + 1)
        ultimo = next_month - timedelta(days=1)

        mes_desde = current.strftime("%d/%m/%Y")
        mes_hasta = ultimo.strftime("%d/%m/%Y")

        if current.year == desde.year and current.month == desde.month:
            mes_desde = desde_str
        if current.year == hasta.year and current.month == hasta.month:
            mes_hasta = hasta_str

        yield (mes_desde, mes_hasta)
        current = next_month


def main():
    args = parsear_args()
    init_db()

    # ── Solo resumen ──────────────────────────────────────────────────────────
    if args.resumen:
        mostrar_resumen(args.cuit)
        return

    # ── Solo exportar ─────────────────────────────────────────────────────────
    if args.export:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        if args.export == "excel":
            exportar_excel(output_path=f"comprobantes_{ts}.xlsx", cuit=args.cuit)
        else:
            exportar_csv(output_path=f"comprobantes_{ts}.csv", cuit=args.cuit)
        return

    # ── Preparar lista de CUITs ───────────────────────────────────────────────
    desde = args.desde or PERIODO_DESDE
    hasta = args.hasta or PERIODO_HASTA

    cuits_a_procesar = CUITS
    if args.cuit:
        cuit_arg = args.cuit.replace("-", "")
        def _match(c):
            if c["cuit"].replace("-", "") == cuit_arg:
                return True
            if c.get("cuit_representacion", "").replace("-", "") == cuit_arg:
                return True
            for e in c.get("empresas", []):
                if e["cuit"].replace("-", "") == cuit_arg:
                    return True
            return False
        cuits_a_procesar = [c for c in CUITS if _match(c)]
        if not cuits_a_procesar:
            print(f"[ERROR] CUIT {args.cuit} no encontrado en config.py")
            sys.exit(1)

    tipos = ["emitidos", "recibidos"] if args.tipo == "ambos" else [args.tipo]
    meses = list(_generar_meses(desde, hasta))

    # Lista plana de tareas: (cuit_data, tipo, mes_desde, mes_hasta)
    tareas = [(cd, t, md, mh) for cd in cuits_a_procesar for t in tipos for md, mh in meses]

    print(f"\n{'#'*60}")
    print(f"  ARCA SCRAPER — Comprobantes (mes a mes)")
    print(f"  Período  : {desde} → {hasta}  ({len(meses)} meses)")
    print(f"  Tipo     : {args.tipo}")
    print(f"  CUITs    : {len(cuits_a_procesar)}")
    print(f"  Tareas   : {len(tareas)}  |  Workers: {args.workers}")
    print(f"{'#'*60}")

    resultados = []

    # ── Ejecución: paralela o secuencial (#5) ─────────────────────────────────
    workers = min(args.workers, len(tareas))

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            fut_map = {
                executor.submit(_tarea, cd, md, mh, t): (cd, t, md, mh)
                for cd, t, md, mh in tareas
            }
            for future in as_completed(fut_map):
                cd, t, md, mh = fut_map[future]
                cuit_data, tipo, comprobantes, error = future.result()
                resultados.append(_procesar_resultado(cuit_data, tipo, comprobantes, error, md, mh))
    else:
        for cuit_data, tipo, md, mh in tareas:
            print(f"\n{'─'*50}")
            print(f"  Mes: {md} → {mh}")
            print(f"{'─'*50}")
            _, tipo_r, comprobantes, error = _tarea(cuit_data, md, mh, tipo)
            resultados.append(_procesar_resultado(cuit_data, tipo_r, comprobantes, error, md, mh))

    # ── Resumen final ─────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  RESULTADO FINAL")
    print(f"{'='*60}")
    for r in resultados:
        icono = "✓" if r["estado"] == "OK" else "✗"
        print(
            f"  {icono} [{r['tipo']:<9}] {r['razon_social'][:22]:<22} "
            f"Total: {r['total']:>4}  Nuevos: {r['nuevos']:>4}  {r['estado']}"
        )

    # ── Exportar si no se indicó --no-export ─────────────────────────────────
    if not args.no_export:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        try:
            exportar_excel(output_path=f"comprobantes_{ts}.xlsx", cuit=args.cuit)
        except Exception:
            exportar_csv(output_path=f"comprobantes_{ts}.csv", cuit=args.cuit)

    mostrar_resumen(args.cuit)


def _procesar_resultado(cuit_data: dict, tipo: str,
                         comprobantes: list, error: str | None,
                         mes_desde: str = "", mes_hasta: str = "") -> dict:
    """Inserta en DB y retorna dict de resultado para el resumen."""
    cuit_login  = cuit_data["cuit"]
    razon_social = cuit_data.get("razon_social", cuit_login)
    tipo_op      = tipo.rstrip("s")

    log_id = log_inicio(cuit_login, razon_social, mes_desde, mes_hasta, tipo_op)

    if error:
        log_fin(log_id, "ERROR", 0, 0, error)
        print(f"  [ERROR] {cuit_login} [{tipo}] {mes_desde}→{mes_hasta}: {error}")
        return {"cuit": cuit_login, "razon_social": razon_social, "tipo": tipo,
                "total": 0, "nuevos": 0, "estado": f"ERROR: {error}"}

    total, nuevos = insertar_muchos(comprobantes)
    log_fin(log_id, "OK", total, nuevos)
    print(f"  [DB] ✓ [{tipo}] {mes_desde}→{mes_hasta}: {total} encontrados, {nuevos} nuevos insertados.")
    return {"cuit": cuit_login, "razon_social": razon_social, "tipo": tipo,
            "total": total, "nuevos": nuevos, "estado": "OK"}


if __name__ == "__main__":
    main()
