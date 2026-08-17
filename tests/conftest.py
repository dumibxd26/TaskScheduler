from pathlib import Path

import pytest


@pytest.fixture
def project_root():
    return Path(__file__).parents[1]