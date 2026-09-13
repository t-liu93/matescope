"""Export the checked-in frontend contract from the FastAPI application."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from matescope.main import app

repository_root = Path(__file__).resolve().parents[1]
(repository_root / "openapi.json").write_text(
    json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8"
)
