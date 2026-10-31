"""Pytest fixtures and helpers for contract testing."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_provider
from app.main import app


@pytest.fixture
def client():
    """FastAPI TestClient with dependency overrides cleaned up on teardown."""
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
