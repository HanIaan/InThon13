"""External DB checks are explicit opt-in and rollback all test changes."""
import os

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "network: opt-in external connection tests (RUN_NETWORK_TESTS=1)")


def pytest_collection_modifyitems(config, items):
    if os.getenv("RUN_NETWORK_TESTS") != "1":
        for item in items:
            if "network" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="network tests require RUN_NETWORK_TESTS=1"))
