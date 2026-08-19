"""Serverless entrypoint.

Vercel detects this project as `framework: fastapi` and routes every path to
the app itself. Do NOT add a `rewrites` rule pointing here: the build warns
that "internal rewrites in backend framework projects now route requests using
the rewritten destination path", so the app receives the literal `/api/index`
for every request and answers 404 to everything, `/docs` included.

Vercel imports `app` from this module and serves it as an ASGI application.
The FastAPI app itself is built in `app.main`; nothing host-specific belongs
here, so a container host can keep running `app.main:app` directly.
"""

from app.main import app

__all__ = ["app"]
