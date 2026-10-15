import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.utils.paths as paths  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    # never touch the real data/ folder from tests
    monkeypatch.setattr(paths, "BASE_DIR", tmp_path)
    paths.ensure_dirs()
    return tmp_path


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtCore import QCoreApplication

    return QCoreApplication.instance() or QCoreApplication([])
