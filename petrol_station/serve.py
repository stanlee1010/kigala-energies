"""Production server (Waitress). Works on Windows and Linux.

    python serve.py

HOST/PORT come from the environment. Default 127.0.0.1:8000: only this computer can open it,
because it is plain HTTP and logins could be captured on the station Wi-Fi (security audit F-08).
Phones use the HTTPS internet site. HOST=0.0.0.0 opens it to the network again.
"""
import os

from waitress import serve

from backend.app import create_app

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    print(f"Kigala Energies station system running on http://{host}:{port}")
    # 16 worker threads: many phones can be served at once (a slow upload does not hold up the rest).
    serve(create_app(), host=host, port=port, threads=int(os.environ.get("THREADS", "16")))
