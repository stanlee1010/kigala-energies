"""Production server (Waitress). Works on Windows and Linux.

    python serve.py

HOST/PORT come from the environment (default 0.0.0.0:8000 so phones on the
station Wi-Fi can open http://<this-computer-ip>:8000).
"""
import os

from waitress import serve

from backend.app import create_app

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    print(f"Kigala Energies station system running on http://{host}:{port}")
    # 16 worker threads: many phones can be served at once (a slow upload does not hold up the rest).
    serve(create_app(), host=host, port=port, threads=int(os.environ.get("THREADS", "16")))
