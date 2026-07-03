#!/bin/bash
# ARCA ScrapON ~By Studio BP~ — Launcher
cd "$(dirname "$0")"
source ../venv/bin/activate
echo ""
echo "  ╔══════════════════════════════════════════╗"
echo "  ║   ARCA ScrapON ~By Studio BP~            ║"
echo "  ║   Iniciando servidor...                  ║"
echo "  ╚══════════════════════════════════════════╝"
echo ""
echo "  Abrí en tu navegador: http://localhost:5000"
echo ""
python app.py
