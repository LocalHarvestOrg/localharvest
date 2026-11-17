"""Turn model output into rows: extraction JSON (with recovery strategies) and triage decisions."""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .schema import BUILTIN_FIELDS, ENTITY_FIELDS, FIELDS, ROW_FIELDS

_FENCE = re.compile(r"^```json|^```|```$", flags=re.MULTILINE)


def parse_extraction(raw: str, fields: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Flatten the compact {"companies": [...]} (or "entities") output into one dict per relationship.

    `fields` are the fields the model was asked for (guide.selected()), in
    schema order. The rows it returned follow that order. Every dict has all of
    FIELDS as keys, None for the ones that were not asked for, so what comes
    after the parser does not change shape.

    Output cut off at the token limit is closed after its last complete row.
    Anything else (e.g. the model answering with one object per relationship)
    goes to parse_legacy_json.
    """
    fields = list(fields) if fields is not None else list(BUILTIN_FIELDS)
    entity_fields = [f for f in ENTITY_FIELDS if f in fields]
    row_fields = [f for f in ROW_FIELDS if f in fields]
    clean = _FENCE.sub("", raw).strip()
    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        data = _close_truncated(clean)

    if isinstance(data, dict) and ("companies" in data or "entities" in data or "relations" in data):
        companies = data.get("companies", data.get("entities", [data]))
        entities = []
        for company in companies if isinstance(companies, list) else []:
            if not isinstance(company, dict):
                continue
            header = {f: (_cell(company.get(f)) if f in entity_fields else None) for f in ENTITY_FIELDS}
            for row in company.get("relations") or []:
                entity = _row_to_entity(row, row_fields)
                if entity is not None:
                    entities.append({**header, **entity})
        return entities if entities else [{}]

    return parse_legacy_json(raw)


def _cell(value: Any) -> Optional[str]:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)


def _row_to_entity(row: Any, row_fields: List[str]) -> Optional[Dict[str, Any]]:
    """One relations row (an array in the order of row_fields) to a dict with every row field."""
    if isinstance(row, dict):
        return {f: (_cell(row.get(f)) if f in row_fields else None) for f in ROW_FIELDS}
    if not isinstance(row, list) or not row:
        return None
    if [str(v).strip().lower() for v in row[:2]] == row_fields[:2]:
        return None  # header row
    values = [_cell(v) for v in row]
    entity = {f: None for f in ROW_FIELDS}
    entity.update(zip(row_fields, values))
    extra = [v for v in values[len(row_fields):] if v]
    if extra:
        entity["NOTES"] = "; ".join(filter(None, [entity.get("NOTES")] + extra))
    return entity


def _close_truncated(text: str) -> Optional[Any]:
    """Parse JSON cut off mid-output by closing it after the last complete array or object."""
    stack, in_string, escaped, cuts = [], False, False, []
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            stack.append(ch)
        elif ch in "]}" and stack:
            stack.pop()
            cuts.append((i + 1, "".join("]" if c == "[" else "}" for c in reversed(stack))))
    for end, closer in reversed(cuts[-50:]):
        try:
            return json.loads(text[:end] + closer)
        except json.JSONDecodeError:
            continue
    return None


def parse_legacy_json(raw: str) -> List[Dict[str, Any]]:
    """Parser for one JSON object per relationship, kept as a fallback."""
    clean = _FENCE.sub("", raw).strip()

    # --- Strategy 1: duplicate-key single object (most common failure mode) ---
    try:
        pairs = []

        def pairs_hook(p):
            pairs.extend(p)
            return {}
        json.loads(clean, object_pairs_hook=pairs_hook)

        relation_name_count = sum(1 for k, _ in pairs if k == "relation_name")
        if relation_name_count > 1:
            print(f"    [parse_json] Detected {relation_name_count} relation_name keys — recovering as {relation_name_count} entities")
            recovered = _recover_duplicate_key_objects_from_pairs(pairs)
            if recovered:
                return recovered

        # No duplicates — parse normally
        result = json.loads(clean)
        if isinstance(result, list):
            return result if result else [{}]
        elif isinstance(result, dict):
            return [result]
        else:
            return [{"_parse_error": f"Unexpected type: {type(result).__name__}"}]

    except json.JSONDecodeError:
        pass  # fall through to other strategies

    # --- Strategy 2: multiple concatenated JSON objects e.g. {...}\n{...}\n} ---
    entities = _recover_concatenated_objects(clean)
    if entities:
        print(f"    [parse_json] Recovered {len(entities)} concatenated objects")
        return entities

    # --- Strategy 3: try wrapping in array brackets (sometimes just missing [...]) ---
    try:
        # Strip any trailing/leading stray braces and try as array
        bracketed = "[" + clean.strip().lstrip("[").rstrip("]") + "]"
        result = json.loads(bracketed)
        if isinstance(result, list) and result:
            print(f"    [parse_json] Recovered by array-wrapping: {len(result)} entities")
            return result
    except json.JSONDecodeError:
        pass

    print("    [parse_json] All strategies failed, returning error row")
    return [{"_parse_error": raw}]


def _recover_concatenated_objects(raw: str) -> Optional[List[Dict[str, Any]]]:
    """
    Parse multiple concatenated JSON objects from a string, e.g.:
      {...},\n{...},\n{...}\n}
    or
      {...}\n{...}\n{...}

    Uses a simple brace-depth scanner to find each complete {...} block.
    """
    entities = []
    depth = 0
    start = None

    for i, ch in enumerate(raw):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                chunk = raw[start:i + 1]
                try:
                    obj = json.loads(chunk)
                    if isinstance(obj, dict):
                        entities.append(obj)
                except json.JSONDecodeError:
                    # chunk itself might have duplicate keys — try pair recovery
                    pairs = []
                    try:
                        json.loads(chunk, object_pairs_hook=lambda p: pairs.extend(p) or {})
                        relation_name_count = sum(1 for k, _ in pairs if k == "relation_name")
                        if relation_name_count > 1:
                            recovered = _recover_duplicate_key_objects_from_pairs(pairs)
                            if recovered:
                                entities.extend(recovered)
                    except json.JSONDecodeError:
                        pass
                start = None
            elif depth < 0:
                # Stray closing brace — reset and keep scanning
                depth = 0
                start = None

    return entities if entities else None


def _recover_duplicate_key_objects_from_pairs(pairs: List[Tuple[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """Split an ordered pair list into one dict per relation_name occurrence."""
    SPLIT_KEY = "relation_name"

    header = {}
    entities = []
    current = None

    for key, value in pairs:
        if key == SPLIT_KEY:
            if current is not None:
                entities.append({**header, **current})
            current = {key: value}
        elif current is None:
            header[key] = value
        else:
            current[key] = value

    if current is not None:
        entities.append({**header, **current})

    return entities if entities else None


_DECISION = re.compile(r'"?triage_output"?\s*:\s*"?(true|false)', re.IGNORECASE)


def parse_triage(raw: str) -> Dict[str, str]:
    """The triage decision as {"triage_output": "true"|"false", "notes": str}.

    A page whose answer cannot be read is kept ("true"), so a parsing problem
    costs an extraction call rather than a missed table.
    """
    clean = _FENCE.sub("", raw).strip()
    data = None
    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        data = _close_truncated(clean)
    if isinstance(data, list):
        data = data[0] if data else None

    if isinstance(data, dict) and "triage_output" in data:
        decision = str(data["triage_output"]).strip().lower()
        if decision in ("true", "false"):
            return {"triage_output": decision, "notes": str(data.get("notes") or "")}

    # Unparseable or truncated JSON that still states a decision
    match = _DECISION.search(clean)
    if match:
        return {"triage_output": match.group(1).lower(), "notes": ""}
    if clean.lower().strip('".') in ("true", "false"):
        return {"triage_output": clean.lower().strip('".'), "notes": ""}

    print(f"    [triage] No decision in the model output, keeping the page: {raw[:200]!r}")
    return {"triage_output": "true", "notes": "triage failed, kept for extraction"}
