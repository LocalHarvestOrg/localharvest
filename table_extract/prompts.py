"""Prompts, split into editable text and fixed output-format sections.

The editable parts (instructions, examples, field descriptions, retry
reminders) default to the files in default_prompts/. They are templates:
{placeholders} are filled from the Guide, the answers to the guided
questions, when the prompt is rendered.

Until the user changes an answer, every placeholder is filled with the
original wording of the prompt (default_prompts/guide/legacy.json), so the
default prompts are exactly the ones that were tuned. Once an answer changes,
the placeholders it drives are written from the new answer. The output-format
sections are generated here, so no prompt edit can change the shape of the
answer the parsers expect.

The app shows each prompt as one raw text (editable()): the templates filled
from the answers, with FORMAT_MARKER where the output format goes. Once that
text is edited by hand it is kept in `raw` and used as it is: the answers no
longer change it, only the output format is still written from them.
"""

import copy
import json
import re
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schema import (
    BUILTIN_FIELDS, CUSTOM_FIELDS, ENTITY_FIELDS, OPTIONAL_FIELDS, ROW_FIELDS, selected_fields,
)

DEFAULTS = Path(__file__).parent / "default_prompts"

# Placeholders written from one answer once it is no longer the default (placeholder -> answer)
_ANSWER_SLOTS = {
    "triage_document_type": "document_type",
    "triage_table_types": "table_types",
    "intro_table_types": "table_types",
    "triage_exclusions": "exclusions",
    "triage_example": "example_page",
    "triage_relationships": "relationships",
    "triage_relationship_names": "relationships",
    "intro_examples": "relationships",
    "intro_roles": "relationships",
    "relationship_choice": "relationships",
    "relationship_rules": "relationships",
    "checklist_relationship": "relationships",
}
# Placeholders that disappear when the field they talk about is not asked for (placeholder -> field)
_FIELD_SLOTS = {
    "address_rules": "relation_address",
    "country_rules": "relation_country",
    "checklist_address": "relation_address",
    "checklist_nationality": "relation_nationality",
}
# What a template may use: {name} is replaced by Guide.values()[name]
PLACEHOLDERS = ("entity", "entities", "example", *_ANSWER_SLOTS, *_FIELD_SLOTS)
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

# Stands for the output format in a raw prompt: the format is written there when the prompt is sent
FORMAT_MARKER = "[The answer format is added here]"


def _default(path: str) -> str:
    with open(DEFAULTS / path, encoding="utf-8", newline="") as f:
        return f.read()


def _default_fields() -> Dict[str, str]:
    return json.loads(_default("extraction/fields.json"))


@lru_cache(maxsize=1)
def _guide_defaults() -> dict:
    data = json.loads(_default("guide/guide.json"))
    data["exclusions"] = _default("guide/exclusions.txt").rstrip("\n")
    data["example_page"] = _default("guide/example_page.txt").rstrip("\n")
    return data


@lru_cache(maxsize=1)
def _legacy() -> Dict[str, str]:
    return json.loads(_default("guide/legacy.json"))


def _guide_default(key: str):
    return Field(default_factory=lambda: copy.deepcopy(_guide_defaults()[key]))


def prompt_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()[:16]


def fill(template: str, values: Dict[str, str]) -> str:
    """Replace {name} placeholders in one pass.

    Only {word} is touched, so the JSON braces in the examples are safe, and
    text inserted for one placeholder is never scanned for more.
    """
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def _check(text: str) -> str:
    unknown = sorted({name for name in _PLACEHOLDER.findall(text) if name not in PLACEHOLDERS})
    if unknown:
        raise ValueError(
            f"unknown placeholder {', '.join('{' + u + '}' for u in unknown)}; "
            f"available: {', '.join('{' + p + '}' for p in PLACEHOLDERS)}"
        )
    return text


def _check_raw(text: Optional[str]) -> Optional[str]:
    if text is not None and text.count(FORMAT_MARKER) != 1:
        raise ValueError(f"keep the line {FORMAT_MARKER} exactly once: it marks where the answer format goes")
    return text


def _with_marker(before: str, output_format: str, after: str) -> str:
    """The raw text: before + the marker + after, spaced like the format would be."""
    text = output_format.rstrip("\n")
    return f"{before}{FORMAT_MARKER}{output_format[len(text):]}{after}"


def _with_format(raw: str, output_format: str) -> str:
    return raw.replace(FORMAT_MARKER, output_format.rstrip("\n"), 1)


def _or_list(items: List[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " or " + items[-1]


class Relationship(BaseModel):
    name: str
    description: str = ""


class CustomColumn(BaseModel):
    name: str
    description: str = ""


class Guide(BaseModel):
    """The answers to the guided questions. They fill the placeholders of both prompts."""

    model_config = ConfigDict(validate_default=True)

    document_type: str = _guide_default("document_type")  # 1. type of document
    table_types: str = _guide_default("table_types")  # 2. type of tables
    entity: str = _guide_default("entity")  # 3. type of main entity
    relationships: List[Relationship] = _guide_default("relationships")  # 4. allowed values of "relationship"
    columns: List[str] = _guide_default("columns")  # 5. the optional fields to extract...
    custom_columns: List[CustomColumn] = _guide_default("custom_columns")  # ...and up to 3 of the user's own
    example_page: str = _guide_default("example_page")  # 6. what a page looks like...
    example_main_entity: str = _guide_default("example_main_entity")
    example_jurisdiction: Optional[str] = _guide_default("example_jurisdiction")
    example_rows: List[Dict[str, Optional[str]]] = _guide_default("example_rows")  # ...and the rows it gives
    exclusions: str = _guide_default("exclusions")  # pages triage should skip

    @field_validator("document_type", "table_types", "entity")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("this answer cannot be empty")
        return value

    @field_validator("relationships")
    @classmethod
    def _relationships(cls, value: List[Relationship]) -> List[Relationship]:
        names = [r.name.strip() for r in value]
        if not names:
            raise ValueError("add at least one relationship")
        if "" in names:
            raise ValueError("every relationship needs a name")
        if len({n.lower() for n in names}) != len(names):
            raise ValueError("each relationship needs a different name")
        return value

    @field_validator("columns")
    @classmethod
    def _known_columns(cls, value: List[str]) -> List[str]:
        unknown = [c for c in value if c not in OPTIONAL_FIELDS]
        if unknown:
            raise ValueError(f"cannot choose {unknown}; the choices are {OPTIONAL_FIELDS}")
        return [f for f in OPTIONAL_FIELDS if f in value]

    @field_validator("custom_columns")
    @classmethod
    def _custom_columns(cls, value: List[CustomColumn]) -> List[CustomColumn]:
        if len(value) > len(CUSTOM_FIELDS):
            raise ValueError(f"at most {len(CUSTOM_FIELDS)} extra columns")
        names = [c.name.strip() for c in value]
        if "" in names:
            raise ValueError("every extra column needs a name")
        taken = {f.lower() for f in BUILTIN_FIELDS + CUSTOM_FIELDS}
        if any(n.lower() in taken or n.startswith("_") for n in names):
            raise ValueError("an extra column cannot be named like a standard column or start with an underscore")
        if len({n.lower() for n in names}) != len(names):
            raise ValueError("each extra column needs a different name")
        return value

    @field_validator("example_rows")
    @classmethod
    def _known_row_keys(cls, value: List[Dict[str, Optional[str]]]) -> List[Dict[str, Optional[str]]]:
        for row in value:
            unknown = sorted(set(row) - set(ROW_FIELDS))
            if unknown:
                raise ValueError(f"unknown fields in an example row: {unknown}")
        return value

    # ---- which fields, and what they are called ----

    def selected(self) -> List[str]:
        return selected_fields(self.columns, len(self.custom_columns))

    def label(self, field: str) -> str:
        """What a field is called in the prompt and in the results file."""
        if field in CUSTOM_FIELDS:
            return self.custom_columns[CUSTOM_FIELDS.index(field)].name.strip()
        return field

    def headers(self) -> Dict[str, str]:
        """Selected field -> column header of the results file."""
        return {f: self.label(f) for f in self.selected()}

    def custom_description(self, field: str) -> str:
        return self.custom_columns[CUSTOM_FIELDS.index(field)].description.strip()

    # ---- the text that fills the placeholders ----

    def _at_default(self, key: str) -> bool:
        value = getattr(self, key)
        if key == "relationships":
            value = [r.model_dump() for r in value]
        return value == _guide_defaults()[key]

    def _plural(self) -> str:
        return "companies" if self.entity.strip().lower() == "company" else "entities"

    def values(self) -> Dict[str, str]:
        """What each {placeholder} in a template stands for."""
        pairs = [(r.name.strip(), r.description.strip()) for r in self.relationships]
        names = [n for n, _ in pairs]
        written = {
            "triage_document_type": self.document_type.strip(),
            "triage_table_types": self.table_types.strip(),
            "intro_table_types": self.table_types.strip(),
            "triage_exclusions": self.exclusions.strip(),
            "triage_example": self.example_page.strip(),
            "triage_relationships": ", ".join(f"{n} ({d})" if d else n for n, d in pairs),
            "triage_relationship_names": ", ".join(names),
            "intro_examples": ", ".join(names) + ", or other related entities",
            "intro_roles": _or_list(names),
            "relationship_choice": (
                "Select relationship from this list: " + "[" + ", ".join(f"'{n}'" for n in names) + "]. "
                "How to choose:\n"
                + "\n".join(f'- "{n}": {d}' if d else f'- "{n}"' for n, d in pairs)
                + "\nIf the document uses a different word for one of these, use the closest value from the list. "
                "If the relationship is not clear, use your best judgement to infer the most likely relationship "
                "based on the document content."
            ),
            "relationship_rules": (
                "- Use what is stated in the document to choose the relationship, following the guidance in the "
                "description of the relationship field above.\n"
            ),
            "checklist_relationship": (
                "   - Is relationship filled in if visible in the document? Did you use the closest value from the list?\n"
            ),
        }
        legacy = _legacy()
        chosen = self.selected()
        values = {
            "entity": self.entity.strip(),
            "entities": self._plural(),
            "example": self.example_text(),
        }
        for slot, answer in _ANSWER_SLOTS.items():
            values[slot] = legacy[slot] if self._at_default(answer) else written[slot]
        for slot, field in _FIELD_SLOTS.items():
            values[slot] = legacy[slot] if field in chosen else ""
        return values

    def example_output(self) -> str:
        """The example rows in the exact answer format, for the selected fields only."""
        chosen = self.selected()
        entity = {"main_entity": self.example_main_entity, "jurisdiction": self.example_jurisdiction}
        head = ", ".join(
            f'"{f}": {json.dumps(entity[f], ensure_ascii=False)}' for f in ENTITY_FIELDS if f in chosen
        )
        row_fields = [f for f in ROW_FIELDS if f in chosen]
        rows = ",\n".join(
            "  " + json.dumps([row.get(f) for f in row_fields], ensure_ascii=False) for row in self.example_rows
        )
        return f'{{"{self._plural()}": [{{{head}, "relations": [\n{rows}\n]}}]}}'

    def example_text(self) -> str:
        if not self.example_rows:
            return ""
        return (
            f"{self.example_page.strip()}\n\n\n"
            f"This is how the output from this example should look like:\n\n{self.example_output()}"
        )


class ExtractionPrompt(BaseModel):
    introduction: str = Field(default_factory=lambda: _default("extraction/introduction.txt"))
    task: str = Field(default_factory=lambda: _default("extraction/task.txt"))
    field_descriptions: Dict[str, str] = Field(default_factory=_default_fields)
    rules: str = Field(default_factory=lambda: _default("extraction/rules.txt"))
    # Sent after the page image on the 2nd and 3rd (and any later) attempt
    retry_reminders: List[str] = Field(default_factory=lambda: [
        _default("extraction/retry_attempt_2.txt"),
        _default("extraction/retry_attempt_3.txt"),
    ])
    # The instructions edited by hand, as editable() gives them: used instead of introduction, task and rules
    raw: Optional[str] = None

    @field_validator("raw")
    @classmethod
    def _marker_kept(cls, value: Optional[str]) -> Optional[str]:
        return _check_raw(value)

    @field_validator("introduction", "task", "rules")
    @classmethod
    def _known_placeholders(cls, value: str) -> str:
        return _check(value)

    @field_validator("retry_reminders")
    @classmethod
    def _known_placeholders_in_reminders(cls, value: List[str]) -> List[str]:
        return [_check(v) for v in value]

    @field_validator("field_descriptions", mode="before")
    @classmethod
    def _renamed_field(cls, value):
        # Settings saved before company_name became main_entity
        if isinstance(value, dict) and "company_name" in value and "main_entity" not in value:
            value = {("main_entity" if k == "company_name" else k): v for k, v in value.items()}
        return value

    @field_validator("field_descriptions")
    @classmethod
    def _all_fields_described(cls, value: Dict[str, str]) -> Dict[str, str]:
        missing = [f for f in BUILTIN_FIELDS if not (value.get(f) or "").strip()]
        unknown = sorted(set(value) - set(BUILTIN_FIELDS))
        if missing or unknown:
            raise ValueError(
                f"field descriptions must cover exactly {BUILTIN_FIELDS}; "
                f"missing or empty: {missing}, unknown: {unknown}"
            )
        return {f: _check(value[f]) for f in BUILTIN_FIELDS}

    def output_format(self, guide: Guide, values: Optional[Dict[str, str]] = None) -> str:
        values = values or guide.values()
        chosen = guide.selected()
        entity_fields = [f for f in ENTITY_FIELDS if f in chosen]
        row_fields = [f for f in ROW_FIELDS if f in chosen]

        def described(field: str) -> str:
            if field in CUSTOM_FIELDS:
                return fill(guide.custom_description(field), values)
            return fill(self.field_descriptions[field], values)

        skeleton = ", ".join(f'"{f}": ...' for f in entity_fields)
        entity_lines = "\n".join(f'- "{f}": {described(f)}' for f in entity_fields)
        row_lines = "\n".join(f"{i}. {guide.label(f)}: {described(f)}" for i, f in enumerate(row_fields, 1))
        return (
            "## Output format\n\n"
            f"Return ONLY a valid JSON object. State each main {values['entity']} once, followed by its relationships as rows:\n\n"
            f'{{"{values["entities"]}": [{{{skeleton}, "relations": [[...], [...]]}}]}}\n\n'
            f"{entity_lines}\n"
            f'- "relations": one row per relationship. Each row is an array of exactly {len(row_fields)} values, in this order:\n'
            f"{row_lines}\n\n"
            "Do not add a header row, and do not use keys inside the rows."
        )

    def _parts(self, values: Dict[str, str]):
        before = f"{fill(self.introduction, values)}\n\n## Your task\n\n{fill(self.task, values)}\n\n"
        return before, f"\n\n{fill(self.rules, values)}"

    def editable(self, guide: Guide) -> str:
        """The instructions written from the answers, with FORMAT_MARKER for the output format (ignores raw)."""
        values = guide.values()
        before, after = self._parts(values)
        return _with_marker(before, self.output_format(guide, values), after)

    def render(self, guide: Guide) -> str:
        """The instructions sent before every page image."""
        values = guide.values()
        output_format = self.output_format(guide, values)
        if self.raw is not None:
            return _with_format(self.raw, output_format)
        before, after = self._parts(values)
        return f"{before}{output_format}{after}"

    def reminder(self, attempt: int, guide: Guide) -> str:
        """Extra instructions for a retry (attempt is 0-based), sent after the page image."""
        if attempt == 0 or not self.retry_reminders:
            return ""
        text = self.retry_reminders[min(attempt, len(self.retry_reminders)) - 1]
        return fill(text, guide.values())


TRIAGE_OUTPUT_FORMAT = """## Output format:

Respond with exactly a JSON object with the fields "triage_output" and "notes".
- "triage_output" must be a lowercase one-word string: "true" or "false". No punctuation, explanation, or other text.
- "notes" is optional. Leave it as an empty string ("") unless the classification was ambiguous enough to need a one-sentence justification. If used, keep it to one short sentence.
- Output raw JSON only. Do not wrap it in markdown code fences (no ```), and do not include any text before or after the JSON object.


"""


class TriagePrompt(BaseModel):
    instructions: str = Field(default_factory=lambda: _default("triage/instructions.txt"))
    examples: str = Field(default_factory=lambda: _default("triage/examples.txt"))
    # The instructions edited by hand, as editable() gives them: used instead of instructions and examples
    raw: Optional[str] = None

    @field_validator("instructions", "examples")
    @classmethod
    def _known_placeholders(cls, value: str) -> str:
        return _check(value)

    @field_validator("raw")
    @classmethod
    def _marker_kept(cls, value: Optional[str]) -> Optional[str]:
        return _check_raw(value)

    def editable(self, guide: Guide) -> str:
        """The instructions written from the answers, with FORMAT_MARKER for the output format (ignores raw)."""
        values = guide.values()
        return _with_marker(fill(self.instructions, values), TRIAGE_OUTPUT_FORMAT, fill(self.examples, values))

    def render(self, guide: Guide) -> str:
        if self.raw is not None:
            return _with_format(self.raw, TRIAGE_OUTPUT_FORMAT)
        values = guide.values()
        return f"{fill(self.instructions, values)}{TRIAGE_OUTPUT_FORMAT}{fill(self.examples, values)}"
