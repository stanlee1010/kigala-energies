"""Vercel entrypoint for the existing Flask application."""
from backend.app import create_app

app = create_app()
