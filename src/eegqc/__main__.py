import argparse
import os
import sys


def main():
    from multiprocessing import freeze_support

    freeze_support()
    parser = argparse.ArgumentParser(description="EEGqc manual EEG quality control")
    parser.add_argument("dataset", nargs="?", help="EEGLAB .set file")
    parser.add_argument("--demo", action="store_true", help="Open explicitly synthetic demonstration data")
    args = parser.parse_args()
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
    os.environ.setdefault("MPLBACKEND", "Agg")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from threadpoolctl import threadpool_limits
    import scipy.linalg  # noqa: F401 -- include lazily loaded SciPy BLAS in the global thread limit

    from eegqc.ui.main_window import MainWindow
    from eegqc.ui.app import configure_app

    app = QApplication(sys.argv)
    configure_app(app)
    window = MainWindow()
    window.show()
    if args.dataset:
        QTimer.singleShot(0, lambda: window.open_path(args.dataset))
    elif args.demo:
        QTimer.singleShot(0, window.open_demo)
    with threadpool_limits(limits=2, user_api="blas"):
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
