"""Serverless entrypoint.

Vercel imports `app` from this module and serves it as an ASGI application.
The FastAPI app itself is built in `app.main`; nothing host-specific belongs
here, so a container host can keep running `app.main:app` directly.
"""

from app.main import app

__all__ = ["app"]
