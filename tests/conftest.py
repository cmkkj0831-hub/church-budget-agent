import shutil
from datetime import date

import pytest

from core.issues import IssueBook
from core.ledger import Ledger
from core.paths import DATA_DIR

TODAY = date(2026, 10, 23)


@pytest.fixture
def data_dir(tmp_path):
    """data/ 복사본. 변경 테스트는 이 경로만 쓴다."""
    d = tmp_path / "data"
    shutil.copytree(DATA_DIR, d)
    return d


@pytest.fixture
def ledger(data_dir):
    return Ledger.load(data_dir)


@pytest.fixture
def book(data_dir):
    return IssueBook.load(data_dir)
