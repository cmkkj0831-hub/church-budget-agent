import shutil
from datetime import date

import pytest

from core.paths import DATA_DIR
from core.report import load_settings

TODAY = date(2026, 10, 23)


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="실제 Claude API를 호출하는 스모크 테스트 실행(비용 발생)")


def pytest_configure(config):
    config.addinivalue_line("markers", "live: 실제 Claude API 호출(--live일 때만)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="--live 플래그가 있을 때만 실제 API 호출")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def data_dir(tmp_path):
    """data/ 복사본. 변경 테스트는 이 경로만 쓴다."""
    d = tmp_path / "data"
    shutil.copytree(DATA_DIR, d)
    return d


@pytest.fixture
def settings(data_dir):
    return load_settings(data_dir)
