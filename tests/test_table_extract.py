"""Unit tests for the table_extract package. Run with: python -m unittest discover tests"""

import csv
import json
import tempfile
import unittest
from pathlib import Path

import pypdfium2 as pdfium
from pydantic import ValidationError

from table_extract.config import AUTO_WORKERS, RunConfig, ServerConfig, TriageConfig, parse_pages, resolve_workers
from table_extract.events import EventLog
from table_extract.extract import ContextBudget
from table_extract.parsing import parse_extraction, parse_triage
from table_extract.prompts import ExtractionPrompt, Guide, TriagePrompt, prompt_hash
from table_extract.runner import find_pdfs, fingerprint, same_run
from table_extract.schema import ROW_FIELDS
from table_extract.triage import pages_by_file, pages_without_triage, run_triage


def make_pdf(path: Path, pages: int = 1) -> Path:
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(200, 200)
    pdf.save(str(path))
    return path


class Prompts(unittest.TestCase):
    def test_defaults_unchanged(self):
        # The default prompts, filled from the default answers to the guided questions
        # (tests/golden has the full texts). Update these hashes when changing a prompt on purpose.
        self.assertEqual(prompt_hash(ExtractionPrompt().render(Guide())), "5b268f256e030072")
        self.assertEqual(prompt_hash(TriagePrompt().render(Guide())), "4c672648b68dd631")

    def test_output_format_follows_schema_not_edits(self):
        descriptions = {**ExtractionPrompt().field_descriptions, "relation_name": "WHO"}
        guide = Guide()
        prompt = ExtractionPrompt(field_descriptions=descriptions, rules="", task="t").render(guide)
        self.assertIn("1. relation_name: WHO", prompt)
        asked = [f for f in ROW_FIELDS if f in guide.selected()]
        self.assertIn(f"exactly {len(asked)} values", prompt)

    def test_field_list_is_guarded(self):
        with self.assertRaises(ValidationError):
            ExtractionPrompt(field_descriptions={"company_name": "x"})
        descriptions = {**ExtractionPrompt().field_descriptions, "extra_field": "x"}
        with self.assertRaises(ValidationError):
            ExtractionPrompt(field_descriptions=descriptions)

    def test_reminders(self):
        prompt = ExtractionPrompt(retry_reminders=["second", "third"])
        self.assertEqual([prompt.reminder(a, Guide()) for a in range(5)], ["", "second", "third", "third", "third"])


class ExtractionParsing(unittest.TestCase):
    def test_compact_format(self):
        raw = ('```json\n{"companies": [{"main_entity": "Acme Ltd", "jurisdiction": null, "relations": ['
               '["Alice", "1 Road", "UK", null, "Person", "Owner", null],'
               '["relation_name", "relation_address"],'
               '["Bob", null, null, null, "Person", "Director", "note", "extra"]]}]}\n```')
        rows = parse_extraction(raw)
        self.assertEqual([r["relation_name"] for r in rows], ["Alice", "Bob"])  # header row dropped
        self.assertEqual(rows[0]["main_entity"], "Acme Ltd")
        self.assertEqual(rows[1]["NOTES"], "note; extra")

    def test_truncated_output_keeps_complete_rows(self):
        raw = ('{"companies": [{"company_name": "Acme", "jurisdiction": null, "relations": ['
               '["Alice", null, null, null, "Person", "Owner", null], ["Bo')
        rows = parse_extraction(raw)
        self.assertEqual([r["relation_name"] for r in rows], ["Alice"])

    def test_legacy_duplicate_keys(self):
        raw = '{"company_name": "Acme", "relation_name": "A", "relationship": "Owner", "relation_name": "B"}'
        rows = parse_extraction(raw)
        self.assertEqual([r["relation_name"] for r in rows], ["A", "B"])
        self.assertTrue(all(r["company_name"] == "Acme" for r in rows))

    def test_no_entities(self):
        self.assertEqual(parse_extraction('{"companies": []}'), [{}])


class TriageParsing(unittest.TestCase):
    def test_decisions(self):
        self.assertEqual(parse_triage('{"triage_output":"false","notes":""}')["triage_output"], "false")
        self.assertEqual(parse_triage('```json\n{"triage_output": "True"}\n```')["triage_output"], "true")
        self.assertEqual(parse_triage('[{"triage_output":"false"}]')["triage_output"], "false")
        self.assertEqual(parse_triage('{"triage_output":"false","notes":"a long')["triage_output"], "false")

    def test_unreadable_answer_keeps_the_page(self):
        result = parse_triage("I cannot tell")
        self.assertEqual(result["triage_output"], "true")
        self.assertIn("triage failed", result["notes"])


class Config(unittest.TestCase):
    def test_remote_servers_refused(self):
        with self.assertRaises(ValidationError):
            ServerConfig(url="http://192.168.1.20:8000/v1/chat/completions")
        ServerConfig(url="http://192.168.1.20:8000/v1/chat/completions", allow_remote=True)
        ServerConfig(url="http://127.0.0.1:8080/v1/chat/completions")

    def test_automatic_workers(self):
        self.assertIsNone(RunConfig(inputs=[Path("in")], output_dir=Path("out")).extraction.workers)
        self.assertEqual(resolve_workers(None, 3), 3)  # one page per server slot
        self.assertEqual(resolve_workers(None, None), AUTO_WORKERS)  # the server does not say
        self.assertEqual(resolve_workers(2, 8), 2)  # set by hand

    def test_fingerprint_ignores_speed_settings(self):
        base = RunConfig(inputs=[Path("in")], output_dir=Path("out"))
        faster = base.model_copy(deep=True)
        faster.extraction.workers = 8
        faster.output_dir = Path("elsewhere")
        self.assertEqual(fingerprint(base), fingerprint(faster))
        other = base.model_copy(deep=True)
        other.extraction.dpi = 300
        self.assertNotEqual(fingerprint(base), fingerprint(other))

    def test_configs_from_before_the_prefilter_was_removed(self):
        config = RunConfig(inputs=[Path("in")], output_dir=Path("out"))
        stored = json.loads(config.model_dump_json())
        stored["prefilter"] = {"enabled": False, "embed_url": "http://127.0.0.1:8001/v1/embeddings", "top_k": 3}
        self.assertEqual(RunConfig.model_validate(stored), config)  # the old key is ignored

        # Its fingerprint covered the prefilter: the run is the same only if the prefilter was off
        record = {"fingerprint": "from-an-older-version", "config": stored}
        self.assertTrue(same_run(record, config))
        stored["prefilter"]["enabled"] = True
        self.assertFalse(same_run(record, config))
        stored["prefilter"]["enabled"] = False
        stored["extraction"]["dpi"] = 300
        self.assertFalse(same_run(record, config))
        self.assertFalse(same_run({"fingerprint": "other", "config": json.loads(config.model_dump_json())}, config))


class Inputs(unittest.TestCase):
    def test_find_pdfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").mkdir()
            (root / "a" / "sub").mkdir()
            for path in ["a/one.pdf", "a/TWO.PDF", "a/notes.txt", "a/sub/three.pdf", "b.pdf"]:
                (root / path).write_bytes(b"%PDF")
            names = [p.name for p in find_pdfs([root / "a", root / "b.pdf"])]
            self.assertEqual(names, ["TWO.PDF", "one.pdf", "b.pdf"])
            self.assertIn("three.pdf", [p.name for p in find_pdfs([root / "a"], recursive=True)])
            (root / "c").mkdir()
            (root / "c" / "one.pdf").write_bytes(b"%PDF")
            with self.assertRaisesRegex(ValueError, "share a file name"):
                find_pdfs([root / "a", root / "c"])


class Budget(unittest.TestCase):
    def test_warns_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "events.jsonl"
            budget = ContextBudget(16384, 12288, EventLog(log, echo=False))
            budget.check(3000, "Pages")  # fits
            budget.check(5800, "Pages")
            budget.check(5900, "Pages")
            self.assertEqual(log.read_text().count('"warning"'), 1)


class ChosenPages(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_pages("3, 7-9"), {3, 7, 8, 9})
        self.assertEqual(parse_pages(" 2 4;5 "), {2, 4, 5})
        self.assertEqual(parse_pages(""), set())
        for text, message in [("x", "ranges such as"), ("0", "start at 1"), ("-3", "ranges such as"),
                              ("9-7", "lower page"), ("1-999999", "highest page")]:
            with self.assertRaisesRegex(ValueError, message):
                parse_pages(text)

    def test_config(self):
        triage = TriageConfig(pages={"a.pdf": "9,7 8, 3", "b.pdf": ""})
        self.assertEqual(triage.pages, {"a.pdf": "3, 7-9"})  # one way of writing it; empty means not chosen
        self.assertEqual(triage.chosen_pages(), {"a.pdf": {3, 7, 8, 9}})
        with self.assertRaises(ValidationError) as caught:
            TriageConfig(pages={"a.pdf": "3, seven"})
        self.assertEqual(caught.exception.errors()[0]["loc"], ("pages", "a.pdf"))

    def test_same_pages_are_the_same_run(self):
        base = RunConfig(inputs=[Path("in")], output_dir=Path("out"), triage={"pages": {"a.pdf": "7-9"}})
        retyped = RunConfig(inputs=[Path("in")], output_dir=Path("out"), triage={"pages": {"a.pdf": "7, 8,9"}})
        self.assertEqual(fingerprint(base), fingerprint(retyped))
        other = RunConfig(inputs=[Path("in")], output_dir=Path("out"), triage={"pages": {"a.pdf": "7-10"}})
        self.assertNotEqual(fingerprint(base), fingerprint(other))

    def test_triage_skips_the_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdfs = [make_pdf(root / "a.pdf", pages=4), make_pdf(root / "b.pdf", pages=2)]
            events = EventLog(root / "events.jsonl", echo=False)
            output = root / "triage.csv"
            config = TriageConfig(workers=1, pages={"a.pdf": "2-3", "b.pdf": "2, 5"})
            # Every document has chosen pages, so the model is never asked: no client needed
            self.assertEqual(run_triage(pdfs, output, None, None, config, events), 0)
            self.assertEqual(pages_by_file(output, positive_only=True), {"a.pdf": {2, 3}, "b.pdf": {2}})
            with open(output, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 6)  # one decision per page
            self.assertEqual({r["_notes"] for r in rows}, {"Chosen by hand", "Not chosen by hand"})
            self.assertIn("leaving out the pages chosen beyond it (5)", (root / "events.jsonl").read_text())

            run_triage(pdfs, output, None, None, config, events)  # resumed: nothing written twice
            with open(output, newline="", encoding="utf-8") as f:
                self.assertEqual(len(list(csv.DictReader(f))), 6)

    def test_without_triage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdfs = [make_pdf(root / "a.pdf", pages=4), make_pdf(root / "b.pdf", pages=2)]
            events = EventLog(root / "events.jsonl", echo=False)
            self.assertIsNone(pages_without_triage(pdfs, TriageConfig(enabled=False), events))  # every page
            config = TriageConfig(enabled=False, pages={"a.pdf": "4"})
            self.assertEqual(pages_without_triage(pdfs, config, events), {"a.pdf": {4}, "b.pdf": {1, 2}})


if __name__ == "__main__":
    unittest.main()
