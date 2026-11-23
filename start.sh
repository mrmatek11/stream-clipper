#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
command -v ffmpeg >/dev/null || { echo "Brak ffmpeg. Wpisz: sudo apt install ffmpeg python3-venv"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -U -r requirements.txt
.venv/bin/python clipper.py "$@"
