"""
app.py — Interfaz web para ARCA ScrapON ~By Studio BP~
Flask + scraper en background + log en tiempo real via SSE.
"""

import os
import sys
import json
import io
import contextlib
import threading
import queue
from datetime import datetime, timedelta

from flask import Flask, render_template, request, jsonify, Response

sys.path.insert(0, os.path.dirname(__file__))

from scraper import scrape_cuit, LoginError

app = Flask(__name__)

log_queue = queue.Queue()
scraper_running = False
scraper_result = None


class ThreadSafeLogCapture(io.TextIOBase):
    def __init__(self):
        self._buf = ""

    def write(self, text):
        if not text:
            return 0
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if line:
                log_queue.put(line)
        return len(text)

    def flush(self):
        if self._buf.strip():
            log_queue.put(self._buf.strip())
            self._buf = ""


def _run_scraper(cuit, password, empresa_cuit, empresa_nombre, tipo, periods):
    global scraper_running, scraper_result
    scraper_running = True
    scraper_result = None

    try:
        cuit_data = {
            "cuit": cuit,
            "password": password,
            "razon_social": empresa_nombre or cuit,
            "empresas": [{"cuit": empresa_cuit, "razon_social": empresa_nombre}],
        }

        def dias_del_mes(anio, mes):
            if mes in (1, 3, 5, 7, 8, 10, 12):
                return 31
            if mes in (4, 6, 9, 11):
                return 30
            if mes == 2:
                if anio % 4 == 0 and (anio % 100 != 0 or anio % 400 == 0):
                    return 29
                return 28

        rangos = []
        for p in sorted(periods, key=lambda x: (x["year"], x["month"])):
            anio = p["year"]
            mes = p["month"]
            desde = f"01/{mes:02d}/{anio}"
            hasta_dia = dias_del_mes(anio, mes)
            hasta = f"{hasta_dia}/{mes:02d}/{anio}"
            rangos.append((desde, hasta))

        capture = ThreadSafeLogCapture()
        with contextlib.redirect_stdout(capture):
            archivos = scrape_cuit(
                cuit_data,
                tipo=tipo,
                rangos=rangos,
                output_dir="/home/pc_wolf_05/descargas_arca",
            )
        capture.flush()

        scraper_result = {"ok": True, "archivos": len(archivos), "rutas": archivos}
    except LoginError as e:
        scraper_result = {"ok": False, "error": f"Error de login: {e}"}
    except Exception as e:
        scraper_result = {"ok": False, "error": str(e)}
    finally:
        scraper_running = False
        log_queue.put("__DONE__")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/start", methods=["POST"])
def start_scraper():
    global scraper_running
    if scraper_running:
        return jsonify({"error": "Ya hay un scraper corriendo"}), 409

    data = request.json
    cuit = data.get("cuit", "").strip()
    password = data.get("password", "").strip()
    empresa_cuit = data.get("empresa_cuit", "").strip()
    empresa_nombre = data.get("empresa_nombre", "").strip()
    tipo = data.get("tipo", "recibidos")
    periods = data.get("periods", [])

    if not cuit or not password:
        return jsonify({"error": "CUIT y clave son obligatorios"}), 400
    if not periods:
        return jsonify({"error": "Seleccioná al menos un mes"}), 400

    while not log_queue.empty():
        try:
            log_queue.get_nowait()
        except queue.Empty:
            break

    t = threading.Thread(
        target=_run_scraper,
        args=(cuit, password, empresa_cuit, empresa_nombre, tipo, periods),
        daemon=True,
    )
    t.start()
    return jsonify({"ok": True})


@app.route("/api/status")
def status():
    return jsonify({
        "running": scraper_running,
        "result": scraper_result,
    })


@app.route("/api/log")
def log_stream():
    def generate():
        while True:
            try:
                msg = log_queue.get(timeout=30)
                if msg == "__DONE__":
                    yield f"data: {json.dumps({'done': True, 'result': scraper_result})}\n\n"
                    break
                yield f"data: {json.dumps({'text': msg})}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'ping': True})}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    print("=" * 50)
    print("  ARCA ScrapON ~By Studio BP~")
    print("  Abrí http://localhost:5000 en tu navegador")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
