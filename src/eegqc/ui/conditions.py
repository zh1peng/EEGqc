from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from eegqc.core.conditions import Clause, Condition


class ConditionsDialog(QDialog):
    def __init__(self, dataset, queries, parent=None):
        super().__init__(parent)
        self.dataset, self.queries = dataset, dict(queries)
        self.setWindowTitle("ERP Condition queries and overlays")
        self.resize(850, 480)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Each named condition produces an ERP; Join rules with AND / OR.Numeric comparisons require numeric fields."
            )
        )
        body = QHBoxLayout()
        self.names = QListWidget()
        self.names.addItems(self.queries)
        self.names.currentTextChanged.connect(self.load_query)
        body.addWidget(self.names, 1)
        editor = QWidget()
        form = QVBoxLayout(editor)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Condition name, e.g. target_correct")
        self.mode = QComboBox()
        self.mode.addItem("Match all AND", "all")
        self.mode.addItem("Match any OR", "any")
        form.addWidget(self.name)
        form.addWidget(self.mode)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Field", "Operator", "Value (in comma-separated)"])
        self.table.horizontalHeader().setStretchLastSection(True)
        form.addWidget(self.table)
        row = QHBoxLayout()
        for text, callback in (
            ("Add rule", self.add_row),
            ("Delete selected rule", self.remove_row),
            ("Save condition", self.save_query),
            ("Delete condition", self.remove_query),
        ):
            item = QPushButton(text)
            item.clicked.connect(callback)
            row.addWidget(item)
        form.addLayout(row)
        body.addWidget(editor, 3)
        layout.addLayout(body)
        self.count = QLabel("")
        layout.addWidget(self.count)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.apply_queries)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.add_row()

    def add_row(self, checked=False, clause=None):
        row = self.table.rowCount()
        self.table.insertRow(row)
        field = QComboBox()
        field.addItems(self.dataset.conditions)
        op = QComboBox()
        op.addItems(["=", "!=", "in", "contains", ">", ">=", "<", "<="])
        value = QLineEdit()
        if clause is not None:
            field.setCurrentText(clause.field)
            op.setCurrentText(clause.operator)
            value.setText(clause.value)
        for col, widget in enumerate((field, op, value)):
            self.table.setCellWidget(row, col, widget)

    def remove_row(self):
        if self.table.currentRow() >= 0:
            self.table.removeRow(self.table.currentRow())

    def load_query(self, name):
        if name not in self.queries:
            return
        query = self.queries[name]
        self.name.setText(name)
        self.mode.setCurrentIndex(self.mode.findData(query.mode))
        self.table.setRowCount(0)
        for clause in query.clauses:
            self.add_row(clause=clause)

    def save_query(self):
        name = self.name.text().strip()
        if not name:
            name, ok = QInputDialog.getText(self, "Condition name", "Name")
            if not ok or not name.strip():
                return False
        clauses = tuple(
            Clause(
                self.table.cellWidget(r, 0).currentText(),
                self.table.cellWidget(r, 1).currentText(),
                self.table.cellWidget(r, 2).text(),
            )
            for r in range(self.table.rowCount())
        )
        query = Condition(clauses, self.mode.currentData())
        try:
            count = int(query.mask(self.dataset).sum())
        except (ValueError, KeyError) as error:
            QMessageBox.warning(self, "Invalid condition", str(error))
            return False
        self.queries[name.strip()] = query
        self.names.blockSignals(True)
        self.names.clear()
        self.names.addItems(self.queries)
        self.names.blockSignals(False)
        self.count.setText(f"{name}: {count} trials(before manual rejection)")
        return True

    def apply_queries(self):
        has_values = any(self.table.cellWidget(row, 2).text() for row in range(self.table.rowCount()))
        if self.name.text().strip() or has_values:
            if not self.name.text().strip():
                self.name.setText(f"Condition {len(self.queries) + 1}")
            if not self.save_query():
                return
        self.accept()

    def remove_query(self):
        self.queries.pop(self.names.currentItem().text(), None) if self.names.currentItem() else None
        self.names.clear()
        self.names.addItems(self.queries)
        if self.queries:
            self.names.setCurrentRow(0)
        else:
            self.name.clear()
            self.table.setRowCount(0)
