"""Explicit, serializable ERP predicates. No expression evaluation."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Clause:
    field: str
    operator: str
    value: str

    def mask(self, columns):
        values = columns[self.field]
        if self.operator == "=":
            return values == self.value
        if self.operator == "!=":
            return values != self.value
        if self.operator == "in":
            return np.isin(values, [v.strip() for v in self.value.split(",")])
        if self.operator == "contains":
            return np.char.find(values.astype(str), self.value) >= 0
        operations = {">": np.greater, ">=": np.greater_equal, "<": np.less, "<=": np.less_equal}
        if self.operator not in operations:
            raise ValueError(f"Unknown condition operator: {self.operator}")
        return operations[self.operator](values.astype(float), float(self.value))


@dataclass(frozen=True)
class Condition:
    clauses: tuple[Clause, ...] = ()
    mode: str = "all"

    def mask(self, dataset):
        if not self.clauses:
            return np.ones(dataset.n_epochs, dtype=bool)
        masks = [clause.mask(dataset.conditions) for clause in self.clauses]
        if self.mode == "all":
            return np.logical_and.reduce(masks)
        if self.mode == "any":
            return np.logical_or.reduce(masks)
        raise ValueError("Condition mode must be all or any")

    def describe(self):
        separator = " AND " if self.mode == "all" else " OR "
        return separator.join(f"{c.field} {c.operator} {c.value}" for c in self.clauses) or "全部"


def normalize_condition(condition):
    if isinstance(condition, Condition):
        return condition
    return Condition() if condition is None else Condition((Clause(condition[0], "=", str(condition[1])),))
