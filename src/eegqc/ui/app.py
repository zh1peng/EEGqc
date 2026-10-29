import os
import sys
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase


def configure_app(app):
    app.setApplicationName("EEGqc")
    if sys.platform == "win32":
        # Windows' offscreen Qt plugin has no system font database.
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
        QFontDatabase.addApplicationFont(str(font))
        app.setFont(QFont("Microsoft YaHei UI", 10))
