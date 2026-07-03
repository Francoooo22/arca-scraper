"""
run.py — Entry point del scraper ARCA

Uso:
    python run.py                                    # emitidos, todos los CUITs
    python run.py --tipo recibidos                   # solo recibidos
    python run.py --tipo ambos                       # emitidos + recibidos
    python run.py --desde 01/01/2025 --hasta 31/03/2025
    python run.py --cuit 20111111111
    python run.py --resumen                          # resumen de archivos descargados
"""

import argparse
import csv
import io
import os
import sys
import zipfile
from datetime import datetime, timedelta

from config import CUITS, PERIODO_DESDE, PERIODO_HASTA
from scraper import scrape_cuit, LoginError


def parsear_args():
    parser = argparse.ArgumentParser(description="ARCA Scraper — Comprobantes")
    parser.add_argument("--cuit",      type=str, help="Scrapear solo este CUIT")
    parser.add_argument("--empresa",   type=str, help="Scrapear solo esta empresa (razon social o CUIT)")
    parser.add_argument("--desde",     type=str, help="Fecha desde (dd/mm/yyyy)")
    parser.add_argument("--hasta",     type=str, help="Fecha hasta (dd/mm/yyyy)")
    parser.add_argument("--tipo",      type=str, default="emitidos",
                        choices=["emitidos", "recibidos", "ambos"],
                        help="Tipo de comprobantes a scrapear (default: emitidos)")
    parser.add_argument("--resumen",   action="store_true",
                        help="Mostrar resumen de archivos descargados y salir")
    return parser.parse_args()


def _tarea(cuit_data, tipo, rangos):
    """Ejecuta scrape_cuit para un CUIT/tipo con todos los rangos en una sesión."""
    try:
        archivos = scrape_cuit(cuit_data, tipo=tipo, rangos=rangos,
                               output_dir="descargas_arca")
        return cuit_data, tipo, archivos, None
    except LoginError as e:
        return cuit_data, tipo, [], str(e)
    except Exception as e:
        return cuit_data, tipo, [], str(e)


def _generar_meses(desde_str: str, hasta_str: str):
    """
    Genera tuplas (desde_mes, hasta_mes) en formato dd/mm/yyyy
    para cada mes calendario entre desde_str y hasta_str.
    """
    desde = datetime.strptime(desde_str, "%d/%m/%Y")
    hasta = datetime.strptime(hasta_str, "%d/%m/%Y")

    current = desde.replace(day=1)

    while current <= hasta.replace(day=1):
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


def _resumen_archivos(output_dir: str = "descargas_arca"):
    """Lee los archivos CSV/ZIP descargados y muestra un resumen por empresa y período."""
    if not os.path.isdir(output_dir):
        print(f"[RESUMEN] No existe la carpeta {output_dir}")
        return

    print(f"\n{'='*72}")
    print(f"  RESUMEN DE ARCHIVOS DESCARGADOS")
    print(f"  Carpeta: {output_dir}")
    print(f"{'='*72}")

    total_archivos = 0
    total_registros = 0

    for empresa_dir in sorted(os.listdir(output_dir)):
        empresa_path = os.path.join(output_dir, empresa_dir)
        if not os.path.isdir(empresa_path):
            continue

        print(f"\n  ── {empresa_dir} ──")

        for tipo in ["emitidos", "recibidos"]:
            tipo_path = os.path.join(empresa_path, tipo)
            if not os.path.isdir(tipo_path):
                continue

            archivos = sorted(f for f in os.listdir(tipo_path)
                              if f.endswith(".zip") or f.endswith(".csv"))
            if not archivos:
                continue

            print(f"    [{tipo}] {len(archivos)} archivo(s):")
            for fname in archivos:
                fpath = os.path.join(tipo_path, fname)
                fsize = os.path.getsize(fpath)
                registros = 0

                # Contar registros dentro del ZIP o CSV
                try:
                    if fname.endswith(".zip"):
                        with zipfile.ZipFile(fpath) as zf:
                            for zname in zf.namelist():
                                with zf.open(zname) as csvf:
                                    reader = csv.reader(io.TextIOWrapper(csvf, encoding="utf-8", errors="replace"),
                                                        delimiter=";")
                                    next(reader, None)  # skip header
                                    registros += sum(1 for _ in reader)
                    else:
                        with open(fpath, encoding="utf-8", errors="replace") as csvf:
                            reader = csv.reader(csvf, delimiter=";")
                            next(reader, None)
                            registros = sum(1 for _ in reader)
                except Exception:
                    registros = -1

                reg_str = f"{registros} reg." if registros >= 0 else "? reg."
                print(f"      {fname}  ({fsize:,} bytes, {reg_str})")
                total_archivos += 1
                if registros > 0:
                    total_registros += registros

    print(f"\n{'─'*72}")
    print(f"  Total: {total_archivos} archivo(s), {total_registros} registro(s)")
    print(f"{'─'*72}")


def _seleccionar_empresas(cuits_config: list) -> list:
    """
    Muestra la lista de empresas disponibles y pide al usuario que elija.
    Retorna la lista de CUITs config filtrada.
    """
    todas = []
    for cd in cuits_config:
        empresas = cd.get("empresas", [])
        if not empresas:
            todas.append((cd.get("razon_social", cd["cuit"]), cd))
        else:
            for emp in empresas:
                label = f"{emp['razon_social']} ({emp['cuit']})"
                todas.append((label, cd))

    if len(todas) <= 1:
        return cuits_config

    print(f"\n  Empresas disponibles:")
    for i, (label, _) in enumerate(todas, 1):
        print(f"    {i}. {label}")
    print(f"    0. Todas")

    while True:
        try:
            choice = input(f"\n  Seleccioná empresa (0-{len(todas)}): ").strip()
            choice = int(choice)
            if choice == 0:
                return cuits_config
            if 1 <= choice <= len(todas):
                _, cuit_data = todas[choice - 1]
                return [cuit_data]
        except (ValueError, EOFError):
            pass
        print("  Opción inválida.")


def main():
    args = parsear_args()

    desde = args.desde or PERIODO_DESDE
    hasta = args.hasta or PERIODO_HASTA

    # ── Modo resumen ──────────────────────────────────────────────────────
    if args.resumen:
        _resumen_archivos()
        return

    # ── Filtrar CUITs ─────────────────────────────────────────────────────
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

    # ── Seleccionar empresa (solo si hay varias y NO se pasó --cuit) ─────
    if not args.cuit:
        cuits_a_procesar = _seleccionar_empresas(cuits_a_procesar)

    tipos = ["emitidos", "recibidos"] if args.tipo == "ambos" else [args.tipo]
    meses = list(_generar_meses(desde, hasta))

    tareas = [(cd, t, meses) for cd in cuits_a_procesar for t in tipos]

    print(f"\n{'#'*60}")
    print(f"  ARCA SCRAPER — Comprobantes")
    print(f"  Período  : {desde} → {hasta}  ({len(meses)} meses)")
    print(f"  Tipo     : {args.tipo}")
    print(f"  CUITs    : {len(cuits_a_procesar)}")
    print(f"  Sesiones : {len(tareas)}  (1 login por sesión)")
    print(f"{'#'*60}")

    resultados = []

    for cuit_data, tipo, meses_rangos in tareas:
        print(f"\n{'─'*50}")
        print(f"  {cuit_data.get('razon_social', cuit_data['cuit'])} — {tipo}")
        print(f"{'─'*50}")
        _, tipo_r, archivos, error = _tarea(cuit_data, tipo, meses_rangos)
        resultados.append(_procesar_resultado(cuit_data, tipo_r, archivos, error))

    print(f"\n{'='*60}")
    print("  RESULTADO FINAL")
    print(f"{'='*60}")
    for r in resultados:
        icono = "✓" if r["estado"] == "OK" else "✗"
        print(
            f"  {icono} [{r['tipo']:<9}] {r['razon_social'][:22]:<22} "
            f"Archivos: {r['archivos']:>3}  {r['estado']}"
        )

    _resumen_archivos()


def _procesar_resultado(cuit_data: dict, tipo: str,
                         archivos: list, error: str | None) -> dict:
    """Retorna dict con el resultado para el resumen final."""
    cuit_login  = cuit_data["cuit"]
    razon_social = cuit_data.get("razon_social", cuit_login)

    if error:
        print(f"  [ERROR] {cuit_login} [{tipo}]: {error}")
        return {"cuit": cuit_login, "razon_social": razon_social, "tipo": tipo,
                "archivos": 0, "estado": f"ERROR: {error}"}

    print(f"  [OK] {len(archivos)} archivo(s) descargado(s).")
    for a in archivos:
        print(f"    → {a}")
    return {"cuit": cuit_login, "razon_social": razon_social, "tipo": tipo,
            "archivos": len(archivos), "estado": "OK"}


if __name__ == "__main__":
    main()
