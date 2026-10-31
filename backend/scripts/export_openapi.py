"""Write the OpenAPI spec to docs/openapi.json. Run from backend/: python -m scripts.export_openapi"""
import json
from pathlib import Path

from app.main import app

out = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
out.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out}")
