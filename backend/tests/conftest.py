import pytest
from fastapi.testclient import TestClient

from app.main import app

SMALL_BBOX = "126.97,37.55,127.00,37.58"
NIGHT = dict(time_slot="night", sound_sensitivity=5, light_sensitivity=3, crowd_sensitivity=1)


@pytest.fixture(scope="session")
def client():
    return TestClient(app)
