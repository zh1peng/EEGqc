STYLE = """
QMainWindow, QDialog { background: #f3f6fa; }
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 12px; color: #243247; }
QMenuBar, QMenu, QToolBar { background: #ffffff; border: 0; spacing: 8px; }
QMenuBar { padding: 3px 14px; color: #607086; }
QMenuBar::item:selected, QMenu::item:selected { background: #e8f4f4; }
QMenu::item { padding: 7px 22px; }
QToolBar { padding: 12px 18px; border-bottom: 1px solid #dce4eb; }
QPushButton, QToolButton { background: #ffffff; border: 1px solid #d3dde6;
  padding: 7px 12px; border-radius: 5px; }
QPushButton:hover, QToolButton:hover { background: #edf7f7; border-color: #7bb8bd; }
QPushButton:checked { background: #e0f2f2; border-color: #0c929c; color: #087b85; }
QPushButton:disabled { color: #9ba7b4; background: #f4f6f8; border-color: #e1e7ed; }
QPushButton[primary='true'] { background: #078b98; border-color: #078b98; color: white; font-weight: 600; }
QPushButton[primary='true']:hover { background: #067784; }
QPushButton[primary='true']:disabled { background: #edf2f5; border-color: #dce4eb; color: #93a1af; }
QPushButton#rejectButton { background: #fff1f2; border-color: #e5b5bd; color: #ad3547; }
QPushButton#workspaceNav { text-align: left; padding: 12px 10px; border: 0; background: transparent; }
QPushButton#workspaceNav:checked { background: #078b98; color: white; font-weight: 600; }
QPushButton#workspaceNav:hover { background: #dceff0; color: #086e77; }
QLabel#brand { color: #173446; font-size: 23px; font-weight: 700; }
QLabel#heading { font-size: 20px; font-weight: 600; color: #20384d; }
QLabel#muted { color: #718195; font-size: 11px; }
QLabel#sectionLabel { color: #52647a; font-size: 12px; font-weight: 600; padding-top: 12px; padding-bottom: 6px; }
QLabel#context { background: #ffffff; padding: 8px 0 14px 0; font-size: 13px; color: #52647a; }
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
  background: #ffffff; border: 1px solid #d3dde6; padding: 6px; border-radius: 4px;
  selection-background-color: #d5eeee; selection-color: #173446; }
QLineEdit:focus, QPlainTextEdit:focus { border-color: #078b98; }
QComboBox QAbstractItemView { background: #ffffff; selection-background-color: #d5eeee; }
QDockWidget { font-weight: 600; }
QDockWidget::title { padding: 10px 14px; background: #f3f6fa; color: #395369; border-bottom: 1px solid #dce4eb; }
QTableWidget, QListWidget { background: #ffffff; border: 1px solid #e1e7ed; alternate-background-color: #f6f9fb; outline: 0; }
QTableWidget::item:selected, QListWidget::item:selected { background: #d9eff0; color: #075e69; }
QListWidget#datasetList { background: transparent; border: 0; }
QListWidget#datasetList::item { padding: 10px 5px; border-radius: 4px; }
QListWidget#rankList::item { padding: 9px 8px; border-bottom: 1px solid #edf1f5; }
QHeaderView::section { background: #edf3f7; border: 0; padding: 9px; color: #4e6377; }
QScrollArea, QStackedWidget { border: 0; background: #ffffff; }
QWidget#inspectorBody { background: #ffffff; }
QWidget#navigationBody { background: #f3f6fa; border-right: 1px solid #dce4eb; }
QTabWidget::pane { border: 0; background: #ffffff; }
QTabBar::tab { padding: 11px 18px; background: #f3f6fa; color: #708095; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { background: #ffffff; color: #078b98; border-bottom: 2px solid #078b98; }
QSplitter::handle { background: #dce4eb; width: 2px; }
QStatusBar { background: #f3f6fa; color: #718195; border-top: 1px solid #dce4eb; }
QCheckBox, QRadioButton { spacing: 7px; padding: 4px; }
QGroupBox { border: 1px solid #dce4eb; border-radius: 5px; margin-top: 14px; padding-top: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QScrollBar:vertical { background: #f3f6fa; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #c4d1dc; min-height: 24px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""
