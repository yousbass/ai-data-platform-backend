import re
from typing import Dict, Tuple
import pandas as pd

ROLE_KEYWORDS = {
    "date": ["date", "time", "datetime", "created", "updated", "joined", "invoice date", "order date"],
    "payment_method": ["payment", "cash", "card", "cash_type", "pay", "method"],
    "product": ["product", "item", "coffee", "sku", "service", "menu", "product name", "item name"],
    "customer": ["customer", "client", "buyer"],
    "employee": ["employee", "staff", "worker"],
    "location": ["region", "city", "country", "branch", "location", "area"],
    "status": ["status", "state", "stage", "progress"],
    "quantity": ["qty", "quantity", "units", "count", "stock", "volume"],
    "money": ["money", "amount", "revenue", "sales", "price", "cost", "fee", "salary", "value", "total"],
    "id": ["id", "number", "no", "code", "reference", "invoice", "order_id"]
}


def _clean_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _keyword_matches(clean_name: str, keyword: str) -> bool:
    """
    Match keywords as normalized words/phrases, not as arbitrary substrings.

    This prevents mistakes such as matching 'fee' inside 'coffee_name'.
    """
    clean_keyword = _clean_name(keyword)
    if not clean_keyword:
        return False

    pattern = rf"(^|\s){re.escape(clean_keyword)}($|\s)"
    return re.search(pattern, clean_name) is not None


def detect_type(series: pd.Series) -> str:
    if pd.api.types.is_numeric_dtype(series):
        return "number"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"

    sample = series.dropna().astype(str).head(50)
    if len(sample) == 0:
        return "unknown"

    numeric = pd.to_numeric(sample, errors="coerce").notna().mean()
    if numeric >= 0.85:
        return "number"

    dates = pd.to_datetime(sample, errors="coerce", format="mixed").notna().mean()
    if dates >= 0.75:
        return "datetime"

    return "text"


def guess_role(name: str, series: pd.Series, detected_type: str) -> Tuple[str, float]:
    clean = _clean_name(name)
    for role, words in ROLE_KEYWORDS.items():
        if any(_keyword_matches(clean, w) for w in words):
            return role, 0.90

    unique_ratio = series.nunique(dropna=True) / max(len(series), 1)
    if detected_type == "datetime":
        return "date", 0.85
    if detected_type == "number":
        if unique_ratio < 0.05:
            return "numeric_category", 0.55
        return "measure", 0.70
    if detected_type == "text":
        if unique_ratio <= 0.25:
            return "dimension", 0.75
        return "text", 0.60
    return "unknown", 0.40


def profile_dataframe(df: pd.DataFrame) -> Dict:
    columns = []
    for col in df.columns:
        s = df[col]
        dtype = detect_type(s)
        role, conf = guess_role(col, s, dtype)
        missing_count = int(s.isna().sum())
        info = {
            "name": col,
            "detected_type": dtype,
            "role_guess": role,
            "role_confidence": conf,
            "missing_count": missing_count,
            "missing_percent": round(missing_count / max(len(df), 1) * 100, 2),
            "unique_count": int(s.nunique(dropna=True)),
            "unique_ratio": round(s.nunique(dropna=True) / max(len(df), 1), 4)
        }
        if dtype == "number":
            numeric = pd.to_numeric(s, errors="coerce")
            info.update({
                "min": float(numeric.min()) if numeric.notna().any() else None,
                "max": float(numeric.max()) if numeric.notna().any() else None,
                "mean": float(numeric.mean()) if numeric.notna().any() else None
            })
        if dtype == "datetime":
            dt = pd.to_datetime(s, errors="coerce", format="mixed")
            info.update({
                "min_date": str(dt.min()) if dt.notna().any() else None,
                "max_date": str(dt.max()) if dt.notna().any() else None
            })
        columns.append(info)

    return {
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "duplicate_rows": int(df.duplicated().sum()),
        "columns": columns
    }


def detect_domain(profile: Dict) -> Dict:
    roles = {c["role_guess"] for c in profile["columns"]}
    names = " ".join(c["name"].lower() for c in profile["columns"])
    scores = {
        "sales_or_transactions": 0,
        "hr": 0,
        "inventory": 0,
        "finance": 0,
        "operations": 0
    }
    if {"date", "money"}.issubset(roles): scores["sales_or_transactions"] += 2
    if "product" in roles: scores["sales_or_transactions"] += 2
    if "payment_method" in roles: scores["sales_or_transactions"] += 1
    if any(w in names for w in ["employee", "salary", "department", "join"]): scores["hr"] += 3
    if any(w in names for w in ["stock", "sku", "supplier", "inventory", "reorder"]): scores["inventory"] += 3
    if any(w in names for w in ["invoice", "expense", "account", "budget"]): scores["finance"] += 2
    best = max(scores, key=scores.get)
    return {"possible_business_domain": best if scores[best] else "unknown", "domain_scores": scores}


def detect_capabilities(profile: Dict) -> Dict:
    roles = [c["role_guess"] for c in profile["columns"]]
    names = [c["name"] for c in profile["columns"]]
    def has(role): return role in roles
    return {
        "time_analysis": has("date"),
        "money_analysis": has("money") or has("measure"),
        "product_analysis": has("product") or has("dimension"),
        "payment_method_analysis": has("payment_method"),
        "location_analysis": has("location"),
        "status_analysis": has("status"),
        "quantity_price_revenue_possible": has("quantity") and (has("money") or any("price" in n.lower() for n in names)),
        "category_comparison": any(r in roles for r in ["dimension", "product", "location", "payment_method", "status"])
    }
