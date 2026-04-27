"""
Optional Ollama Local AI metadata enhancer.

Purpose:
- Runs locally on the user's machine through Ollama.
- Receives safe metadata only, not raw dataset rows.
- Improves column semantic roles, business domain, and analysis capabilities.
- If Ollama is not installed/running, the pipeline continues using local fallback rules.
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()


OLLAMA_GENERATE_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_CHAT_URL = OLLAMA_GENERATE_URL.replace("/api/generate", "/api/chat")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:4b")
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120"))


class OllamaLocalAIError(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    """Extract JSON from an Ollama text response."""
    if not text or not text.strip():
        raise OllamaLocalAIError("Empty Ollama response.")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        raise OllamaLocalAIError(f"Ollama response did not contain valid JSON: {text[:500]}")


def _call_ollama_json(prompt: str) -> dict[str, Any]:
    """Call local Ollama and return parsed JSON.

    Tries several Ollama request styles because some models return empty text in
    strict JSON mode through /api/generate. The pipeline still falls back safely
    if all attempts fail.
    """
    attempts = [
        {
            "url": OLLAMA_GENERATE_URL,
            "payload": {
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.1},
            },
            "response_key": "response",
        },
        {
            "url": OLLAMA_GENERATE_URL,
            "payload": {
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1},
            },
            "response_key": "response",
        },
        {
            "url": OLLAMA_CHAT_URL,
            "payload": {
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": "Return valid JSON only. No markdown."},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.1},
            },
            "response_key": "message.content",
        },
        {
            "url": OLLAMA_CHAT_URL,
            "payload": {
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": "Return valid JSON only. No markdown."},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {"temperature": 0.1},
            },
            "response_key": "message.content",
        },
    ]

    last_error = None
    for attempt in attempts:
        try:
            response = requests.post(
                attempt["url"],
                json=attempt["payload"],
                timeout=OLLAMA_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()

            if attempt["response_key"] == "message.content":
                text = (data.get("message") or {}).get("content", "")
            else:
                text = data.get("response", "")

            if not text or not text.strip():
                last_error = OllamaLocalAIError("Empty Ollama response.")
                continue

            return _extract_json(text)
        except Exception as exc:
            last_error = exc

    raise OllamaLocalAIError(str(last_error))


def _column_names(metadata: dict[str, Any]) -> list[str]:
    return [str(col.get("name", "")) for col in metadata.get("columns", [])]


def _fallback_domain(metadata: dict[str, Any]) -> dict[str, Any]:
    """Guess broad business domain using simple local rules."""
    names = " ".join(name.lower() for name in _column_names(metadata))

    if any(key in names for key in ["coffee", "product", "qty", "quantity", "price", "money", "sales", "revenue", "cash", "payment"]):
        return {"possible_business_domain": "sales_transactions", "domain_confidence": 0.75, "short_domain_reason": "Sales or transaction-related column names were detected."}

    if any(key in names for key in ["employee", "salary", "department", "join", "hire", "status"]):
        return {"possible_business_domain": "hr", "domain_confidence": 0.70, "short_domain_reason": "HR-related column names were detected."}

    if any(key in names for key in ["stock", "inventory", "sku", "supplier", "reorder", "unit_cost"]):
        return {"possible_business_domain": "inventory", "domain_confidence": 0.70, "short_domain_reason": "Inventory-related column names were detected."}

    if any(key in names for key in ["ticket", "issue", "priority", "closed", "opened", "resolved"]):
        return {"possible_business_domain": "operations_or_support", "domain_confidence": 0.65, "short_domain_reason": "Operations/support-related column names were detected."}

    return {"possible_business_domain": "general_business_dataset", "domain_confidence": 0.50, "short_domain_reason": "No specific domain was strongly detected."}


def _fallback_capabilities(metadata: dict[str, Any]) -> dict[str, bool]:
    """Build conservative analysis capabilities using existing metadata only."""
    columns = metadata.get("columns", [])
    names = {str(col.get("name", "")).lower() for col in columns}
    types = {str(col.get("detected_type", "")).lower() for col in columns}
    roles = {str(col.get("role_guess", "")).lower() for col in columns}
    joined_names = " ".join(names)

    has_date = "date" in types or "date" in roles or any(key in joined_names for key in ["date", "time", "datetime", "created", "closed"])
    has_measure = "number" in types or "measure" in roles
    has_dimension = "dimension" in roles or any(key in joined_names for key in ["product", "category", "region", "branch", "department", "status", "type", "method"])
    has_money = any(key in joined_names for key in ["money", "amount", "sales", "revenue", "price", "cost", "salary"])
    has_product = any(key in joined_names for key in ["product", "item", "coffee", "sku", "name"])
    has_payment = any(key in joined_names for key in ["payment", "cash", "card", "method"])
    has_status = "status" in joined_names
    has_employee = any(key in joined_names for key in ["employee", "staff", "department", "salary"])
    has_inventory = any(key in joined_names for key in ["stock", "inventory", "sku", "reorder", "supplier"])

    return {
        "time_analysis": has_date,
        "measure_analysis": has_measure,
        "category_analysis": has_dimension,
        "sales_analysis": has_money,
        "product_analysis": has_product,
        "payment_method_analysis": has_payment,
        "status_analysis": has_status,
        "hr_analysis": has_employee,
        "inventory_analysis": has_inventory,
    }


def _fallback_column_semantics(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Create conservative semantic role guesses without a local LLM."""
    enhanced_columns = []

    for col in metadata.get("columns", []):
        name = str(col.get("name", ""))
        lower = name.lower()
        detected_type = col.get("detected_type")
        role_guess = col.get("role_guess")
        semantic_role = role_guess or "unknown"
        business_role = "unknown"
        confidence = 0.55

        if any(key in lower for key in ["date", "time", "datetime", "created", "closed", "joined", "join"]):
            semantic_role = "date_or_timestamp"
            business_role = "time"
            confidence = 0.80
        elif any(key in lower for key in ["money", "amount", "sales", "revenue", "price", "cost", "salary"]):
            semantic_role = "financial_amount"
            business_role = "measure"
            confidence = 0.82
        elif any(key in lower for key in ["qty", "quantity", "count", "units", "stock"]):
            semantic_role = "quantity"
            business_role = "measure"
            confidence = 0.82
        elif any(key in lower for key in ["product", "item", "coffee", "sku"]):
            semantic_role = "product_or_item"
            business_role = "dimension"
            confidence = 0.82
        elif any(key in lower for key in ["payment", "cash", "card", "method", "type"]):
            semantic_role = "payment_method_or_type"
            business_role = "dimension"
            confidence = 0.78
        elif any(key in lower for key in ["region", "city", "country", "branch", "location"]):
            semantic_role = "location"
            business_role = "dimension"
            confidence = 0.78
        elif any(key in lower for key in ["department", "status", "category", "supplier"]):
            semantic_role = "business_category"
            business_role = "dimension"
            confidence = 0.75
        elif detected_type == "number":
            semantic_role = "numeric_measure"
            business_role = "measure"
            confidence = 0.65
        elif role_guess == "dimension":
            semantic_role = "categorical_dimension"
            business_role = "dimension"
            confidence = 0.65

        enhanced_columns.append({
            "name": name,
            "detected_type": detected_type,
            "missing_percent": col.get("missing_percent"),
            "unique_count": col.get("unique_count"),
            "role_guess": role_guess,
            "semantic_role": semantic_role,
            "business_role": business_role,
            "confidence": confidence,
            "safe_to_send": True,
        })

    return enhanced_columns


def build_fallback_enhanced_metadata(metadata: dict[str, Any], reason: str = "ollama_not_used") -> dict[str, Any]:
    """Return enhanced metadata using local rules only."""
    domain = _fallback_domain(metadata)
    capabilities = _fallback_capabilities(metadata)

    enhanced = dict(metadata)
    enhanced["local_ai"] = {
        "enabled": False,
        "source": "local_rules_fallback",
        "reason": reason,
    }
    enhanced["dataset_summary"] = {
        **metadata.get("dataset_summary", {}),
        **domain,
    }
    enhanced["columns"] = _fallback_column_semantics(metadata)
    enhanced["capabilities"] = capabilities

    return enhanced


def _correct_semantic_role(
    name: str,
    detected_type: str | None,
    role_guess: str | None,
    ai_semantic: str,
    ai_business: str,
) -> tuple[str, str, float]:
    """
    Correct Ollama's semantic interpretation using deterministic local rules.

    This protects the pipeline from weak local models that may return broad or
    incorrect labels such as business_role='money' or semantic_role='text'.
    """
    lower = name.lower()
    allowed_business_roles = {"measure", "dimension", "time", "identifier", "status", "text", "unknown"}

    if any(key in lower for key in ["date", "time", "datetime", "created", "closed", "joined", "join"]):
        return "date_or_timestamp", "time", 0.90

    if any(key in lower for key in ["coffee", "product", "item", "sku", "menu"]):
        return "product_name", "dimension", 0.90

    if any(key in lower for key in ["cash", "payment", "card", "method", "type"]):
        return "payment_method", "dimension", 0.88

    if any(key in lower for key in ["money", "amount", "sales", "revenue", "price", "cost", "salary"]):
        return "financial_amount", "measure", 0.90

    if any(key in lower for key in ["qty", "quantity", "units", "stock", "count"]):
        return "quantity", "measure", 0.88

    if any(key in lower for key in ["region", "city", "country", "branch", "location"]):
        return "location", "dimension", 0.85

    if any(key in lower for key in ["department", "category", "supplier", "status"]):
        return "business_category", "dimension", 0.82

    if any(key in lower for key in ["id", "uuid", "code", "number", "no"]):
        return "identifier", "identifier", 0.78

    if detected_type == "number":
        return "numeric_measure", "measure", 0.70

    if role_guess in ["dimension", "category"]:
        return "categorical_dimension", "dimension", 0.70

    if role_guess == "date":
        return "date_or_timestamp", "time", 0.80

    safe_business_role = ai_business if ai_business in allowed_business_roles else "unknown"
    safe_semantic_role = ai_semantic or role_guess or "unknown"
    return safe_semantic_role, safe_business_role, 0.55


def _normalize_ollama_columns(ai_columns: list[dict[str, Any]], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Ensure Ollama returns only existing columns, preserve basic profile fields,
    and correct weak/invalid semantic role outputs using local rules.
    """
    original_by_name = {str(col.get("name", "")): col for col in metadata.get("columns", [])}
    ai_by_name = {str(item.get("name", "")): item for item in ai_columns}
    normalized = []

    for name, original in original_by_name.items():
        item = ai_by_name.get(name, {})
        detected_type = original.get("detected_type")
        role_guess = original.get("role_guess")
        ai_semantic = str(item.get("semantic_role", "") or "")
        ai_business = str(item.get("business_role", "") or "")

        semantic_role, business_role, confidence = _correct_semantic_role(
            name=name,
            detected_type=detected_type,
            role_guess=role_guess,
            ai_semantic=ai_semantic,
            ai_business=ai_business,
        )

        normalized.append({
            "name": name,
            "detected_type": detected_type,
            "missing_percent": original.get("missing_percent"),
            "unique_count": original.get("unique_count"),
            "role_guess": role_guess,
            "semantic_role": semantic_role,
            "business_role": business_role,
            "confidence": confidence,
            "safe_to_send": bool(item.get("safe_to_send", True)),
        })

    return normalized


def enhance_metadata_with_ollama(metadata: dict[str, Any]) -> dict[str, Any]:
    """
    Enhance safe metadata using a local Ollama model.

    If Ollama fails, returns a local-rules fallback so the pipeline never stops.
    """
    prompt = f"""
You are a Local AI metadata interpreter for a privacy-first data analytics platform.

You receive safe metadata only. You do not receive raw private rows.
Your job is to interpret the likely business meaning of columns and dataset capabilities.

Return valid JSON only. No markdown. No explanation outside JSON.

Safe metadata:
{json.dumps(metadata, indent=2, ensure_ascii=False, default=str)}

Return JSON using exactly this structure:
{{
  "dataset_summary": {{
    "possible_business_domain": "one of: sales_transactions, hr, inventory, finance, operations_or_support, education, healthcare, general_business_dataset",
    "domain_confidence": 0.0,
    "short_domain_reason": "short reason"
  }},
  "columns": [
    {{
      "name": "existing column name",
      "semantic_role": "specific business meaning, e.g. transaction_amount, product_name, payment_method, employee_id, stock_quantity",
      "business_role": "measure | dimension | time | identifier | status | text | unknown",
      "confidence": 0.0,
      "safe_to_send": true
    }}
  ],
  "capabilities": {{
    "time_analysis": true,
    "measure_analysis": true,
    "category_analysis": true,
    "sales_analysis": false,
    "product_analysis": false,
    "payment_method_analysis": false,
    "status_analysis": false,
    "hr_analysis": false,
    "inventory_analysis": false
  }}
}}

Rules:
- Use only columns that exist in the safe metadata.
- Return one columns item for every existing column.
- Do not invent columns.
- Do not ask for raw data.
- Keep confidence conservative.
"""

    try:
        ai_result = _call_ollama_json(prompt)
    except Exception as exc:
        return build_fallback_enhanced_metadata(metadata, reason=str(exc))

    enhanced = dict(metadata)
    enhanced["local_ai"] = {
        "enabled": True,
        "source": "ollama",
        "model": OLLAMA_MODEL,
        "generate_url": OLLAMA_GENERATE_URL,
    }
    fallback_domain = _fallback_domain(metadata)
    ai_dataset_summary = ai_result.get("dataset_summary", {}) or {}

    possible_domain = ai_dataset_summary.get("possible_business_domain")
    domain_confidence = ai_dataset_summary.get("domain_confidence", 0)

    allowed_domains = {
        "sales_transactions",
        "hr",
        "inventory",
        "finance",
        "operations_or_support",
        "education",
        "healthcare",
        "general_business_dataset",
    }

    if (
        not possible_domain
        or "|" in str(possible_domain)
        or "one of" in str(possible_domain).lower()
        or str(possible_domain) not in allowed_domains
        or not isinstance(domain_confidence, (int, float))
        or domain_confidence < fallback_domain.get("domain_confidence", 0)
    ):
        ai_dataset_summary = fallback_domain

    enhanced["dataset_summary"] = {
        **metadata.get("dataset_summary", {}),
        **ai_dataset_summary,
    }
    enhanced["columns"] = _normalize_ollama_columns(ai_result.get("columns", []), metadata)
    fallback_capabilities = _fallback_capabilities(metadata)
    ai_capabilities = ai_result.get("capabilities") or {}

    # Local rules are used as the safety baseline. Ollama is allowed to add true
    # capabilities, but it is not allowed to turn a rule-based true capability
    # into false because small local models may be overly conservative.
    merged_capabilities = dict(fallback_capabilities)
    for key, value in ai_capabilities.items():
        if isinstance(value, bool) and value is True:
            merged_capabilities[key] = True

    enhanced["capabilities"] = merged_capabilities

    return enhanced