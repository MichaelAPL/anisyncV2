import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    """Load tests/fixtures/<name> (JSON)."""
    return json.loads((FIXTURES / name).read_text())
