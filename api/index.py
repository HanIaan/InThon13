"""Vercel Python function: serves the FastAPI backend at /api/v1/* (see vercel.json rewrites).

Set DATA_MODE=live, LIVE_READ=query, DATABASE_URL and CACHE_SECONDS in the Vercel project.
The app keeps its own routes (/api/v1/...), so the original request path is used as is.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.main import app  # noqa: E402,F401
