from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import __version__
from .ui.main_window import MainWindow
from .ui.theme import apply_theme


def main() -> int:
    # Paramiko's debug output is deliberately not enabled in the desktop app.
    logging.getLogger("paramiko").addHandler(logging.NullHandler())
    logging.getLogger("paramiko").propagate = False
    application = QApplication(sys.argv)
    application.setApplicationName("ScpPro")
    application.setOrganizationName("ScpPro")
    application.setApplicationVersion(__version__)
    application.setWindowIcon(QIcon(str(Path(__file__).parent / "resources/icon.svg")))
    apply_theme(application)
    application.styleHints().colorSchemeChanged.connect(lambda _scheme: apply_theme(application))
    window = MainWindow()
    window.show()
    # Optional finite run for packaging smoke tests; normal launches run until quit.
    smoke_duration = os.environ.get("SCPPRO_SMOKE_SECONDS")
    if smoke_duration:
        from PySide6.QtCore import QTimer

        QTimer.singleShot(int(float(smoke_duration) * 1000), window.close)
    return application.exec()
