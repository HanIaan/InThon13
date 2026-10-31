"""Fixtures for Seoul ingestion tests.

All fixture files under ./fixtures are real responses/files from data.seoul.go.kr (sample key or
public file download, fetched 2026-10-31), trimmed to a few rows. No API key is stored anywhere.
Tests marked ``network`` hit the real servers and only run with RUN_NETWORK_TESTS=1.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_configure(config):
    config.addinivalue_line("markers", "network: calls real external APIs (set RUN_NETWORK_TESTS=1)")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_NETWORK_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="network test; set RUN_NETWORK_TESTS=1")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def boundary_dongs():
    import json

    from app.ingestion.seoul.admin_dong import parse_boundaries

    return parse_boundaries(json.loads((FIXTURES / "boundaries_sample.geojson").read_text(encoding="utf-8")))
