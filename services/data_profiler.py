import re
from typing import Dict, Tuple, List
import pandas as pd

ROLE_KEYWORDS = {
    "date": ["date", "time", "datetime", "created", "updated", "joined", "invoice date", "order date", "added"],
    "payment_method": ["payment", "cash", "card", "cash_type", "pay", "method"],
    "product": ["product", "item", "coffee", "sku", "service", "menu", "product name", "item name"],
    "customer": ["customer", "client", "buyer"],
    "employee": ["employee", "staff", "worker"],
    "location": ["region", "city", "country", "branch", "location", "area"],
    "status": ["status", "state", "stage", "progress"],
    "quantity": ["qty", "quantity", "units", "count", "stock", "volume"],
    "money": ["money", "amount", "revenue", "sales", "price", "cost", "fee", "salary", "value", "total"],
    "id": ["id", "number", "no", "code", "reference", "invoice", "order_id"],
    "brand": ["brand", "manufacturer", "maker", "vendor"],
    "color": ["color", "colour", "hue"],
    "category": ["category", "categories", "department", "section"],
    "size": ["size", "sizes", "dimension"],
    "rating": ["rating", "score", "stars", "review_score"],
    "url": ["url", "link", "href", "image_url", "source_url"],
}

# Substrings in a column name that strongly indicate text-not-money even if the
# column also matches a money keyword (e.g., "prices.color", "price_currency").
TEXT_OVERRIDE_TOKENS = [
    "color", "colour", "size", "currency", "shipping", "warranty",
    "return", "availability", "merchant", "source", "url", "flavor",
    "policy", "condition", "name",
]

LIST_SEPARATORS = [",", ";", "|", "/"]


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


def _detect_multi_value(series: pd.Series) -> Dict:
    """
    Detect whether a text column holds multi-value lists like "Black,White,Blue".

    Returns: {"is_multi_value": bool, "separator": str|None, "avg_token_count": float, "max_token_count": int}
    """
    sample = series.dropna().astype(str).head(200)
    if len(sample) == 0:
        return {"is_multi_value": False, "separator": None, "avg_token_count": 0.0, "max_token_count": 0}

    best = {"is_multi_value": False, "separator": None, "avg_token_count": 1.0, "max_token_count": 1}
    for sep in LIST_SEPARATORS:
        token_counts = sample.apply(lambda v: len([t for t in v.split(sep) if t.strip()]))
        avg = float(token_counts.mean())
        mx = int(token_counts.max())
        if avg > best["avg_token_count"]:
            best = {
                "is_multi_value": avg >= 1.6 and mx >= 2,
                "separator": sep,
                "avg_token_count": round(avg, 2),
                "max_token_count": mx,
            }
    return best


def _name_says_money(clean: str) -> bool:
    if not any(_keyword_matches(clean, w) for w in ROLE_KEYWORDS["money"]):
        return False
    return not any(tok in clean for tok in TEXT_OVERRIDE_TOKENS)


def _name_says_date(clean: str) -> bool:
    return any(_keyword_matches(clean, w) for w in ROLE_KEYWORDS["date"])


def guess_role(name: str, series: pd.Series, detected_type: str, multi_value_info: Dict | None = None) -> Tuple[str, float]:
    clean = _clean_name(name)

    # Date wins over money when both are in the name (e.g., "price_seen_at")
    if _name_says_date(clean) and detected_type in ("datetime", "text", "unknown"):
        return "date", 0.92

    # Money keyword only counts if the column is numeric AND the name has no
    # text-override token (color/size/currency/url/etc.)
    if _name_says_money(clean) and detected_type == "number":
        return "money", 0.92

    for role, words in ROLE_KEYWORDS.items():
        if role in ("money", "date"):
            continue
        if any(_keyword_matches(clean, w) for w in words):
            if role in ("brand", "color", "category", "size") and (multi_value_info or {}).get("is_multi_value"):
                return "multi_value_list", 0.9
            if role == "color":
                return "color", 0.9
            if role == "brand":
                return "brand", 0.9
            if role == "category":
                return "category", 0.9
            if role == "size":
                return "size", 0.85
            if role == "rating":
                return "rating", 0.9
            if role == "url":
                return "url", 0.95
            return role, 0.90

    # Multi-value list dominates over plain text when detected
    if (multi_value_info or {}).get("is_multi_value"):
        return "multi_value_list", 0.85

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


def _top_values(series: pd.Series, n: int = 10) -> List[Dict]:
    """Return [{value, count, share}] for the top-N most frequent non-null values."""
    s = series.dropna()
    if len(s) == 0:
        return []
    vc = s.astype(str).value_counts().head(n)
    total = len(s)
    return [
        {"value": str(idx), "count": int(cnt), "share": round(float(cnt) / total, 4)}
        for idx, cnt in vc.items()
    ]


def _sample_values(series: pd.Series, n: int = 5) -> List:
    """Return up to n distinct non-null example values (string-cast, truncated)."""
    s = series.dropna()
    if len(s) == 0:
        return []
    samples = s.astype(str).drop_duplicates().head(n).tolist()
    return [v[:120] for v in samples]


def profile_dataframe(df: pd.DataFrame) -> Dict:
    columns = []
    for col in df.columns:
        s = df[col]
        dtype = detect_type(s)

        multi_value_info = _detect_multi_value(s) if dtype == "text" else {
            "is_multi_value": False, "separator": None, "avg_token_count": 1.0, "max_token_count": 1
        }

        role, conf = guess_role(col, s, dtype, multi_value_info)
        missing_count = int(s.isna().sum())
        n_unique = int(s.nunique(dropna=True))
        info = {
            "name": col,
            "detected_type": dtype,
            "role_guess": role,
            "role_confidence": conf,
            "missing_count": missing_count,
            "missing_percent": round(missing_count / max(len(df), 1) * 100, 2),
            "unique_count": n_unique,
            "unique_ratio": round(n_unique / max(len(df), 1), 4),
            "sample_values": _sample_values(s, 5),
            "top_values": _top_values(s, 10) if n_unique <= 50 else _top_values(s, 5),
            "multi_value": multi_value_info,
        }
        if dtype == "number":
            numeric = pd.to_numeric(s, errors="coerce")
            info.update({
                "min": float(numeric.min()) if numeric.notna().any() else None,
                "max": float(numeric.max()) if numeric.notna().any() else None,
                "mean": float(numeric.mean()) if numeric.notna().any() else None,
                "median": float(numeric.median()) if numeric.notna().any() else None,
            })
        if dtype == "datetime":
            dt = pd.to_datetime(s, errors="coerce", format="mixed")
            info.update({
                "min_date": str(dt.min()) if dt.notna().any() else None,
                "max_date": str(dt.max()) if dt.notna().any() else None,
            })
        columns.append(info)

    return {
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "duplicate_rows": int(df.duplicated().sum()),
        "columns": columns
    }


# Domain detection signals — added ecommerce_catalog
ECOMMERCE_CATALOG_NAME_SIGNALS = [
    "asin", "sku", "upc", "ean", "isbn", "manufacturer", "manufacturer_part_number",
    "msrp", "list_price", "merchant", "marketplace", "listing", "offer", "brand",
    "categories", "image_url", "product_id", "prices.", "price_amount", "is_sale",
    "color", "colors", "size", "sizes", "weight",
]


def detect_domain(profile: Dict) -> Dict:
    roles = {c["role_guess"] for c in profile["columns"]}
    names = " ".join(c["name"].lower() for c in profile["columns"])
    name_tokens = set(re.split(r"[^a-z0-9]+", names))

    scores = {
        "sales_or_transactions": 0,
        "hr": 0,
        "inventory": 0,
        "finance": 0,
        "operations": 0,
        "ecommerce_catalog": 0,
        "marketing": 0,
    }

    if {"date", "money"}.issubset(roles): scores["sales_or_transactions"] += 2
    if "product" in roles: scores["sales_or_transactions"] += 1
    if "payment_method" in roles: scores["sales_or_transactions"] += 1

    if any(w in names for w in ["employee", "salary", "department", "join"]): scores["hr"] += 3
    if any(w in names for w in ["stock", "inventory", "reorder", "warehouse"]): scores["inventory"] += 3
    if any(w in names for w in ["invoice", "expense", "account", "budget"]): scores["finance"] += 2
    if any(w in names for w in ["campaign", "channel", "click", "impression", "ctr", "conversion"]): scores["marketing"] += 3

    catalog_hits = sum(1 for sig in ECOMMERCE_CATALOG_NAME_SIGNALS if sig in names)
    if catalog_hits >= 3:
        scores["ecommerce_catalog"] += 3 + min(catalog_hits, 6)
    if "brand" in roles: scores["ecommerce_catalog"] += 1
    if "category" in roles: scores["ecommerce_catalog"] += 1
    if "color" in roles: scores["ecommerce_catalog"] += 1
    if "multi_value_list" in roles: scores["ecommerce_catalog"] += 1

    best = max(scores, key=scores.get)
    return {"possible_business_domain": best if scores[best] else "unknown", "domain_scores": scores}


def detect_capabilities(profile: Dict) -> Dict:
    roles = [c["role_guess"] for c in profile["columns"]]
    names = [c["name"] for c in profile["columns"]]
    def has(role): return role in roles
    has_color = has("color") or any("color" in n.lower() or "colour" in n.lower() for n in names)
    has_brand = has("brand") or any("brand" in n.lower() or "manufacturer" in n.lower() for n in names)
    has_category = has("category") or any("categor" in n.lower() for n in names)
    date_count = sum(1 for c in profile["columns"] if c["role_guess"] == "date")
    return {
        "time_analysis": has("date"),
        "money_analysis": has("money") or has("measure"),
        "product_analysis": has("product") or has("dimension"),
        "payment_method_analysis": has("payment_method"),
        "location_analysis": has("location"),
        "status_analysis": has("status"),
        "quantity_price_revenue_possible": has("quantity") and (has("money") or any("price" in n.lower() for n in names)),
        "category_comparison": any(r in roles for r in ["dimension", "product", "location", "payment_method", "status", "category", "brand"]),
        "color_analysis": has_color,
        "brand_analysis": has_brand,
        "category_analysis": has_category,
        "multi_value_analysis": has("multi_value_list"),
        "cohort_analysis_possible": date_count >= 2,
    }
