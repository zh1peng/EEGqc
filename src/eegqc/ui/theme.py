STYLE = """
QMainWindow, QDialog { background: #262c34; }
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 12px; color: #e5eaf0; }
QMenuBar, QMenu, QToolBar { background: #252b32; border: 0; spacing: 8px; }
QMenuBar { padding: 5px 12px; }
QMenuBar::item:selected, QMenu::item:selected { background: #35434f; }
QToolBar { padding: 8px 14px; border-bottom: 1px solid #39424c; }
QPushButton, QToolButton { background: #343e49; border: 1px solid #4c5967;
  padding: 7px 13px; border-radius: 4px; }
QPushButton:hover, QToolButton:hover { background: #43505e; }
QPushButton:checked { background: #204653; border-color: #37b8ca; color: #8ee6ef; }
QPushButton:disabled { color: #79848f; border-color: #39424c; }
QPushButton[primary='true'] { background: #168b9d; border-color: #27adbe; color: white; }
QPushButton[primary='true']:disabled { background: #303b44; border-color: #46515d; color: #82909c; }
QLabel#brand { color: #51c5d4; font-size: 21px; font-weight: 700; }
QLabel#heading { font-size: 19px; font-weight: 600; }
QLabel#muted { color: #a4b0bc; }
QLabel#context { background: #2c343e; padding: 12px 18px; font-size: 13px; }
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
  background: #2c353f; border: 1px solid #4a5664; padding: 6px; border-radius: 3px; }
QComboBox QAbstractItemView { background: #2c353f; selection-background-color: #215864; }
QDockWidget { font-weight: 600; }
QDockWidget::title { padding: 10px; background: #2b333c; }
QTableWidget, QListWidget { background: #2b333d; border: 0; alternate-background-color: #303944; }
QTableWidget::item:selected, QListWidget::item:selected { background: #24505e; }
QHeaderView::section { background: #35404c; border: 0; padding: 7px; }
QScrollArea, QStackedWidget { border: 0; }
QSplitter::handle { background: #3b4652; width: 2px; }
QStatusBar { background: #232a32; color: #b7c3cd; }
QCheckBox, QRadioButton { spacing: 7px; padding: 4px; }
QGroupBox { border: 1px solid #46515f; margin-top: 14px; padding-top: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
"""
