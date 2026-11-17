"""The default prompts must not change unless someone decides to change them.

The golden files are the prompts as they were tuned before the guided questions
existed (the only difference: company_name is now main_entity). If a test here
fails after an edit to a template, the default wording changed: either undo it,
or regenerate the golden file on purpose.
"""

from pathlib import Path

from pydantic import ValidationError

from table_extract.prompts import FORMAT_MARKER, ExtractionPrompt, Guide, TriagePrompt

GOLDEN = Path(__file__).parent / "golden"


def golden(name: str) -> str:
    with open(GOLDEN / name, encoding="utf-8", newline="") as f:
        return f.read()


def test_default_extraction_prompt_is_unchanged():
    assert ExtractionPrompt().render(Guide()) == golden("extraction_default.txt")


def test_default_triage_prompt_is_unchanged():
    assert TriagePrompt().render(Guide()) == golden("triage_default.txt")


def test_triage_does_not_change_when_only_extraction_answers_change():
    guide = Guide(columns=["relation_country"], custom_columns=[{"name": "share_percentage"}])
    assert TriagePrompt().render(guide) == golden("triage_default.txt")


def test_changed_example_reaches_triage_whole():
    guide = Guide(example_page="A page about Zed Corp. Table with columns [Name, Stake].")
    assert "- A page about Zed Corp. Table with columns [Name, Stake]." in TriagePrompt().render(guide)


def test_placeholder_in_a_custom_column_description_is_filled_in():
    guide = Guide(
        entity="person",
        custom_columns=[{"name": "amount", "description": "Amount given to the {entity}, as written"}],
    )
    prompt = ExtractionPrompt().render(guide)
    assert "amount: Amount given to the person, as written" in prompt
    assert "{entity}" not in prompt


def test_raw_text_left_as_written_sends_the_tuned_prompt():
    for model, name in ((TriagePrompt, "triage_default.txt"), (ExtractionPrompt, "extraction_default.txt")):
        raw = model().editable(Guide())
        assert raw.count(FORMAT_MARKER) == 1
        assert model(raw=raw).render(Guide()) == golden(name)


def test_raw_text_edited_by_hand_is_sent_as_it_is_with_the_format_written_in():
    raw = "Look for tables of donors.\n\n" + FORMAT_MARKER + "\n\nNo {entity} placeholders here."
    guide = Guide(entity="person")
    prompt = ExtractionPrompt(raw=raw).render(guide)
    assert prompt.startswith("Look for tables of donors.\n\n## Output format")
    assert '{"entities": [' in prompt  # the format still follows the answers
    assert prompt.endswith("No {entity} placeholders here.")  # the raw text itself is not filled in
    assert ExtractionPrompt(raw=raw).render(Guide()) != prompt


def test_raw_text_ignores_the_answers_and_the_templates():
    raw = TriagePrompt().editable(Guide())
    edited = TriagePrompt(raw=raw, instructions="Something else")
    assert edited.render(Guide(document_type="political donations")) == golden("triage_default.txt")


def test_raw_text_must_keep_the_marker_once():
    for raw in ("no marker", f"{FORMAT_MARKER} twice {FORMAT_MARKER}"):
        try:
            TriagePrompt(raw=raw)
        except ValidationError as e:
            assert "exactly once" in str(e)
        else:
            raise AssertionError(f"accepted {raw!r}")
