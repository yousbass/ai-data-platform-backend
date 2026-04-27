

"""
Safe Formula DSL for local derived-column execution.

The Smart AI can suggest formulas, but this module validates and executes only a
controlled set of safe operations on a local pandas DataFrame.

Supported examples:
- maximum_price - minimum_price
- round((original_price - sale_price) / original_price, 2)
- date_diff(closed_date, opened_date, "days")
- if(stock_quantity <= reorder_level, "Low Stock", "OK")
- bucket(minimum_price, [0, 50, 100, 500], ["Low", "Medium", "High"])
- lower(merchant_name)
- concat(first_name, last_name, " ")
"""

from __future__ import annotations

import ast
import operator
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd


class FormulaDSLError(ValueError):
    """Raised when a formula is unsafe, invalid, or unsupported."""


MAX_AST_NODES = 250
MAX_STRING_LENGTH = 500


BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


COMPARISON_OPERATORS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


UNARY_OPERATORS = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Not: operator.not_,
}


DISALLOWED_NODES = (
    ast.Attribute,
    ast.Subscript,
    ast.Lambda,
    ast.Dict,
    ast.Set,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.Await,
    ast.Yield,
    ast.YieldFrom,
    ast.Import,
    ast.ImportFrom,
    ast.Global,
    ast.Nonlocal,
    ast.Assign,
    ast.AugAssign,
    ast.Delete,
    ast.For,
    ast.While,
    ast.If,
    ast.With,
    ast.Try,
    ast.Raise,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
)


ALLOWED_FUNCTIONS = {
    "year",
    "quarter",
    "month",
    "week",
    "day",
    "hour",
    "minute",
    "dayofweek",
    "is_weekend",
    "today",
    "date_diff",
    "lower",
    "upper",
    "length",
    "contains",
    "startswith",
    "endswith",
    "concat",
    "if",
    "coalesce",
    "isnull",
    "abs",
    "round",
    "min",
    "max",
    "log",
    "sqrt",
    "bucket",
}


def _count_ast_nodes(tree: ast.AST) -> int:
    return sum(1 for _ in ast.walk(tree))


def _validate_ast(tree: ast.AST) -> None:
    if _count_ast_nodes(tree) > MAX_AST_NODES:
        raise FormulaDSLError("Formula is too complex.")

    for node in ast.walk(tree):
        if isinstance(node, DISALLOWED_NODES):
            raise FormulaDSLError(f"Unsupported syntax: {type(node).__name__}")

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise FormulaDSLError("Only direct function calls are allowed.")
            if node.func.id not in ALLOWED_FUNCTIONS:
                raise FormulaDSLError(f"Unsupported function: {node.func.id}")
            if node.keywords:
                raise FormulaDSLError("Keyword arguments are not supported in formulas.")

        if isinstance(node, ast.Name):
            if node.id.startswith("__"):
                raise FormulaDSLError("Unsafe name is not allowed.")

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if len(node.value) > MAX_STRING_LENGTH:
                raise FormulaDSLError("String literal is too long.")


def _to_numeric(value: Any) -> Any:
    if isinstance(value, pd.Series):
        return pd.to_numeric(value, errors="coerce")
    return value


def _to_datetime(value: Any) -> Any:
    """Convert values to timezone-naive pandas datetimes.

    The DSL uses dates for grouping, filtering, and date differences. Some
    source files contain timezone-aware timestamps. We normalize them to UTC
    and then remove timezone information so month/year extraction does not
    produce pandas timezone warnings.
    """
    if isinstance(value, pd.Series):
        parsed = pd.to_datetime(value, errors="coerce", format="mixed", utc=True)
        return parsed.dt.tz_convert(None)

    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    try:
        return parsed.tz_convert(None)
    except Exception:
        return parsed


def _series_str(value: Any) -> Any:
    if isinstance(value, pd.Series):
        return value.astype("string")
    return str(value)


def _broadcast_scalar(df: pd.DataFrame, value: Any) -> pd.Series:
    return pd.Series([value] * len(df), index=df.index)


def _safe_divide(left: Any, right: Any) -> Any:
    left_num = _to_numeric(left)
    right_num = _to_numeric(right)
    if isinstance(right_num, pd.Series):
        right_num = right_num.replace(0, np.nan)
    elif right_num == 0:
        return np.nan
    return left_num / right_num


def _safe_floor_divide(left: Any, right: Any) -> Any:
    left_num = _to_numeric(left)
    right_num = _to_numeric(right)
    if isinstance(right_num, pd.Series):
        right_num = right_num.replace(0, np.nan)
    elif right_num == 0:
        return np.nan
    return left_num // right_num


def _apply_binary(op_node: ast.operator, left: Any, right: Any) -> Any:
    if isinstance(op_node, ast.Div):
        return _safe_divide(left, right)
    if isinstance(op_node, ast.FloorDiv):
        return _safe_floor_divide(left, right)

    op_func = BINARY_OPERATORS.get(type(op_node))
    if not op_func:
        raise FormulaDSLError(f"Unsupported binary operator: {type(op_node).__name__}")

    if isinstance(op_node, (ast.Add, ast.Sub, ast.Mult, ast.Mod, ast.Pow)):
        left = _to_numeric(left) if isinstance(op_node, (ast.Sub, ast.Mult, ast.Mod, ast.Pow)) else left
        right = _to_numeric(right) if isinstance(op_node, (ast.Sub, ast.Mult, ast.Mod, ast.Pow)) else right

    return op_func(left, right)


def _apply_comparison(op_node: ast.cmpop, left: Any, right: Any) -> Any:
    op_func = COMPARISON_OPERATORS.get(type(op_node))
    if not op_func:
        raise FormulaDSLError(f"Unsupported comparison operator: {type(op_node).__name__}")
    return op_func(left, right)


def _logical_and(values: list[Any]) -> Any:
    result = values[0]
    for value in values[1:]:
        result = result & value
    return result


def _logical_or(values: list[Any]) -> Any:
    result = values[0]
    for value in values[1:]:
        result = result | value
    return result


def _date_part(value: Any, part: str) -> Any:
    dt = _to_datetime(value)
    if isinstance(dt, pd.Series):
        if getattr(dt.dt, "tz", None) is not None:
            dt = dt.dt.tz_convert(None)
        if part == "year":
            return dt.dt.year
        if part == "quarter":
            return dt.dt.year.astype("Int64").astype(str) + "-Q" + dt.dt.quarter.astype("Int64").astype(str)
        if part == "month":
            return dt.dt.strftime("%Y-%m")
        if part == "week":
            return dt.dt.isocalendar().week.astype("Int64")
        if part == "day":
            return dt.dt.day
        if part == "hour":
            return dt.dt.hour
        if part == "minute":
            return dt.dt.minute
        if part == "dayofweek":
            return dt.dt.day_name()
    else:
        if pd.isna(dt):
            return np.nan
        if part == "year":
            return dt.year
        if part == "quarter":
            return f"{dt.year}-Q{((dt.month - 1) // 3) + 1}"
        if part == "month":
            return f"{dt.year}-{dt.month:02d}"
        if part == "week":
            return dt.isocalendar().week
        if part == "day":
            return dt.day
        if part == "hour":
            return dt.hour
        if part == "minute":
            return dt.minute
        if part == "dayofweek":
            return dt.day_name()

    raise FormulaDSLError(f"Unsupported date part: {part}")


def _date_diff(left: Any, right: Any, unit: str) -> Any:
    left_dt = _to_datetime(left)
    right_dt = _to_datetime(right)
    diff = left_dt - right_dt

    if unit == "days":
        return diff.dt.days if isinstance(diff, pd.Series) else diff.days
    if unit == "hours":
        return diff.dt.total_seconds() / 3600 if isinstance(diff, pd.Series) else diff.total_seconds() / 3600
    if unit == "minutes":
        return diff.dt.total_seconds() / 60 if isinstance(diff, pd.Series) else diff.total_seconds() / 60
    if unit == "months":
        return (diff.dt.days / 30.4375) if isinstance(diff, pd.Series) else diff.days / 30.4375
    if unit == "years":
        return (diff.dt.days / 365.25) if isinstance(diff, pd.Series) else diff.days / 365.25

    raise FormulaDSLError("date_diff unit must be one of: days, hours, minutes, months, years.")


def _call_function(df: pd.DataFrame, name: str, args: list[Any]) -> Any:
    if name == "today":
        if args:
            raise FormulaDSLError("today() does not accept arguments.")
        return _broadcast_scalar(df, date.today().isoformat())

    if name in {"year", "quarter", "month", "week", "day", "hour", "minute", "dayofweek"}:
        if len(args) != 1:
            raise FormulaDSLError(f"{name}() expects one argument.")
        return _date_part(args[0], name)

    if name == "is_weekend":
        if len(args) != 1:
            raise FormulaDSLError("is_weekend() expects one argument.")
        dt = _to_datetime(args[0])
        return dt.dt.dayofweek.isin([5, 6]) if isinstance(dt, pd.Series) else dt.dayofweek in [5, 6]

    if name == "date_diff":
        if len(args) != 3:
            raise FormulaDSLError("date_diff() expects three arguments: col_a, col_b, unit.")
        return _date_diff(args[0], args[1], str(args[2]))

    if name == "lower":
        if len(args) != 1:
            raise FormulaDSLError("lower() expects one argument.")
        value = _series_str(args[0])
        return value.str.lower() if isinstance(value, pd.Series) else value.lower()

    if name == "upper":
        if len(args) != 1:
            raise FormulaDSLError("upper() expects one argument.")
        value = _series_str(args[0])
        return value.str.upper() if isinstance(value, pd.Series) else value.upper()

    if name == "length":
        if len(args) != 1:
            raise FormulaDSLError("length() expects one argument.")
        value = _series_str(args[0])
        return value.str.len() if isinstance(value, pd.Series) else len(value)

    if name == "contains":
        if len(args) != 2:
            raise FormulaDSLError("contains() expects two arguments.")
        value = _series_str(args[0])
        needle = str(args[1])
        return value.str.contains(needle, case=False, na=False, regex=False) if isinstance(value, pd.Series) else needle.lower() in value.lower()

    if name == "startswith":
        if len(args) != 2:
            raise FormulaDSLError("startswith() expects two arguments.")
        value = _series_str(args[0])
        prefix = str(args[1])
        return value.str.startswith(prefix, na=False) if isinstance(value, pd.Series) else value.startswith(prefix)

    if name == "endswith":
        if len(args) != 2:
            raise FormulaDSLError("endswith() expects two arguments.")
        value = _series_str(args[0])
        suffix = str(args[1])
        return value.str.endswith(suffix, na=False) if isinstance(value, pd.Series) else value.endswith(suffix)

    if name == "concat":
        if len(args) < 2:
            raise FormulaDSLError("concat() expects at least two arguments.")
        sep = ""
        values = args
        if len(args) >= 3 and not isinstance(args[-1], pd.Series):
            sep = str(args[-1])
            values = args[:-1]
        result = _series_str(values[0])
        for value in values[1:]:
            result = result.fillna("") + sep + _series_str(value).fillna("")
        return result

    if name == "if":
        if len(args) != 3:
            raise FormulaDSLError("if() expects condition, true_value, false_value.")
        condition, true_value, false_value = args
        return pd.Series(np.where(condition, true_value, false_value), index=df.index)

    if name == "coalesce":
        if len(args) < 2:
            raise FormulaDSLError("coalesce() expects at least two arguments.")
        result = args[0]
        if not isinstance(result, pd.Series):
            result = _broadcast_scalar(df, result)
        for value in args[1:]:
            if not isinstance(value, pd.Series):
                value = _broadcast_scalar(df, value)
            result = result.combine_first(value)
        return result

    if name == "isnull":
        if len(args) != 1:
            raise FormulaDSLError("isnull() expects one argument.")
        return args[0].isna() if isinstance(args[0], pd.Series) else pd.isna(args[0])

    if name == "abs":
        if len(args) != 1:
            raise FormulaDSLError("abs() expects one argument.")
        return _to_numeric(args[0]).abs() if isinstance(args[0], pd.Series) else abs(args[0])

    if name == "round":
        if len(args) not in {1, 2}:
            raise FormulaDSLError("round() expects one or two arguments.")
        digits = int(args[1]) if len(args) == 2 else 0
        value = _to_numeric(args[0])
        return value.round(digits) if isinstance(value, pd.Series) else round(value, digits)

    if name == "min":
        if len(args) != 2:
            raise FormulaDSLError("min() expects two arguments.")
        return pd.concat([_to_numeric(args[0]), _to_numeric(args[1])], axis=1).min(axis=1)

    if name == "max":
        if len(args) != 2:
            raise FormulaDSLError("max() expects two arguments.")
        return pd.concat([_to_numeric(args[0]), _to_numeric(args[1])], axis=1).max(axis=1)

    if name == "log":
        if len(args) != 1:
            raise FormulaDSLError("log() expects one argument.")
        return np.log(_to_numeric(args[0]))

    if name == "sqrt":
        if len(args) != 1:
            raise FormulaDSLError("sqrt() expects one argument.")
        return np.sqrt(_to_numeric(args[0]))

    if name == "bucket":
        if len(args) != 3:
            raise FormulaDSLError("bucket() expects value, edges, labels.")
        value, edges, labels = args
        if not isinstance(edges, list) or not isinstance(labels, list):
            raise FormulaDSLError("bucket() edges and labels must be lists.")
        if len(labels) != len(edges) - 1:
            raise FormulaDSLError("bucket() labels length must equal len(edges) - 1.")
        return pd.cut(_to_numeric(value), bins=edges, labels=labels, include_lowest=True).astype("string")

    raise FormulaDSLError(f"Unsupported function: {name}")


def _eval_node(df: pd.DataFrame, node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(df, node.body)

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        if node.id in {"True", "False", "None"}:
            return {"True": True, "False": False, "None": None}[node.id]
        if node.id not in df.columns:
            raise FormulaDSLError(f"Unknown column or name: {node.id}")
        return df[node.id]

    if isinstance(node, ast.List):
        return [_eval_node(df, item) for item in node.elts]

    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(df, item) for item in node.elts)

    if isinstance(node, ast.BinOp):
        return _apply_binary(node.op, _eval_node(df, node.left), _eval_node(df, node.right))

    if isinstance(node, ast.UnaryOp):
        value = _eval_node(df, node.operand)
        if isinstance(node.op, ast.Not):
            return ~value if isinstance(value, pd.Series) else not value
        op_func = UNARY_OPERATORS.get(type(node.op))
        if not op_func:
            raise FormulaDSLError(f"Unsupported unary operator: {type(node.op).__name__}")
        return op_func(value)

    if isinstance(node, ast.BoolOp):
        values = [_eval_node(df, value) for value in node.values]
        if isinstance(node.op, ast.And):
            return _logical_and(values)
        if isinstance(node.op, ast.Or):
            return _logical_or(values)
        raise FormulaDSLError(f"Unsupported boolean operator: {type(node.op).__name__}")

    if isinstance(node, ast.Compare):
        left = _eval_node(df, node.left)
        comparisons = []
        current_left = left
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(df, comparator)
            comparisons.append(_apply_comparison(op, current_left, right))
            current_left = right
        return _logical_and(comparisons) if len(comparisons) > 1 else comparisons[0]

    if isinstance(node, ast.Call):
        function_name = node.func.id
        args = [_eval_node(df, arg) for arg in node.args]
        return _call_function(df, function_name, args)

    raise FormulaDSLError(f"Unsupported expression: {type(node).__name__}")


def validate_formula(df: pd.DataFrame, formula: str) -> None:
    """Validate syntax and safe expression structure."""
    if not formula or not isinstance(formula, str):
        raise FormulaDSLError("Formula must be a non-empty string.")

    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise FormulaDSLError(f"Invalid formula syntax: {exc}") from exc

    _validate_ast(tree)

    # Evaluate on a small sample to catch missing columns and basic type issues early.
    sample = df.head(25).copy()
    _eval_node(sample, tree)


def execute_formula(df: pd.DataFrame, formula: str) -> Any:
    """Validate and execute a safe formula on the provided DataFrame."""
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise FormulaDSLError(f"Invalid formula syntax: {exc}") from exc

    _validate_ast(tree)
    result = _eval_node(df, tree)

    if isinstance(result, pd.Series):
        return result

    return _broadcast_scalar(df, result)