import os
import warnings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
warnings.filterwarnings("ignore", category=DeprecationWarning)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

# one QApplication for the whole session (widgets need QApplication, not just QGuiApplication)
APP = QApplication.instance() or QApplication([])


@pytest.fixture(scope="session", autouse=True)
def _analysis_cache(tmp_path_factory):
    """Keep analysis results out of the user's ~/.cache during tests."""
    from mdmovie.analysis import runner
    runner.CACHE_DIR = str(tmp_path_factory.mktemp("analysis-cache"))
