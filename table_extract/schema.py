"""The extraction schema: which fields the model returns, in which order.

The field list is fixed in code because it drives three things that must stay
in step: the output format section of the prompt, the parser, and the CSV
columns. What the user chooses (guide.columns, guide.custom_columns) is which
of the optional fields are asked for. The others stay in the structure but are
not sent to the model and are left out of the results.
"""

from typing import Iterable, List

BUILTIN_FIELDS = [
    "main_entity", "jurisdiction",
    "relation_name", "relation_address",
    "relation_country", "relation_nationality", "relation_type",
    "relationship", "NOTES",
]

# Spare columns the user can name and describe (guide.custom_columns). Off unless defined.
CUSTOM_FIELDS = ["custom_1", "custom_2", "custom_3"]

FIELDS = BUILTIN_FIELDS[:-1] + CUSTOM_FIELDS + ["NOTES"]

# Fields stated once per main entity; every relationship row repeats the rest
ENTITY_FIELDS = ["main_entity", "jurisdiction"]
ROW_FIELDS = [f for f in FIELDS if f not in ENTITY_FIELDS]

# Always asked for: they identify the row, or the pipeline relies on them
REQUIRED_FIELDS = ["main_entity", "relation_name", "relationship", "NOTES"]
# The built-in fields the user may switch off
OPTIONAL_FIELDS = [f for f in BUILTIN_FIELDS if f not in REQUIRED_FIELDS]


def selected_fields(columns: Iterable[str], n_custom: int = 0) -> List[str]:
    """The fields the model is asked for, in schema order.

    The required ones, the optional ones in `columns` and the first n_custom spare columns.
    """
    chosen = set(columns) | set(CUSTOM_FIELDS[:n_custom])
    return [f for f in FIELDS if f in REQUIRED_FIELDS or f in chosen]


def results_columns(columns: Iterable[str], n_custom: int = 0) -> List[str]:
    """The columns of the results file for a run: bookkeeping columns plus the selected fields."""
    return ["_filename", "_page", "_entity"] + selected_fields(columns, n_custom)


EXTRACTION_COLUMNS = ["_filename", "_page", "_entity"] + FIELDS
TRIAGE_COLUMNS = ["_filename", "_page", "_triage_output", "_notes"]
TRIAGE_TIMES_COLUMNS = ["filename", "times", "average"]
