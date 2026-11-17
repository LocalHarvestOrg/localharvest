"""Extraction: one row per (related entity, relationship) found on each page."""

import csv
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from . import pdf
from .config import ExtractionConfig
from .events import EventLog
from .llm import ChatClient, image_to_base64, page_content
from .parsing import parse_extraction
from .prompts import Guide
from .schema import EXTRACTION_COLUMNS


class ContextBudget:
    """Warns once if the largest output budget does not fit in the server's context.

    llama-server stops generating when a slot's context is full, so a budget
    larger than (context - input tokens) is silently cut short.
    """

    def __init__(self, n_ctx: Optional[int], max_output: int, events: EventLog):
        self.n_ctx = n_ctx
        self.max_output = max_output
        self.events = events
        self.warned = False
        self._lock = threading.Lock()

    def check(self, input_tokens: int, what: str) -> None:
        if not self.n_ctx or not input_tokens or input_tokens + self.max_output <= self.n_ctx:
            return
        with self._lock:
            if self.warned:
                return
            self.warned = True
        self.events.warning(
            f"{what} take about {input_tokens} input tokens, so with a {self.n_ctx}-token context "
            f"per server slot, output budgets above {self.n_ctx - input_tokens} are cut short "
            f"(largest budget: {self.max_output}). Lower max_tokens_schedule, or raise the "
            f"server's context (-c, divided by -np slots).",
            n_ctx=self.n_ctx, input_tokens=input_tokens, max_output=self.max_output,
        )


def is_plausible_entity_count(entity_count: int, completion_tokens: int, limit: int) -> bool:
    """Decide whether the parsed entity_count is reasonable for the amount of
    output the model produced.

    A high tokens-per-entity ratio means the model generated a lot of text but we
    only recovered a few entities, which usually signals missed/merged entities.
    """
    if entity_count <= 0:
        return False
    return (completion_tokens / entity_count) <= limit


def extract_page(client: ChatClient, pdf_path: Path, page: int, config: ExtractionConfig, prompt: str,
                 events: EventLog, budget: Optional[ContextBudget] = None,
                 guide: Optional[Guide] = None) -> List[Dict[str, Any]]:
    """Extract entities from a single page.

    Each attempt uses a larger output budget from max_tokens_schedule
    (4096 -> 8192 -> 12288 by default), so retries get both a stronger prompt
    and more room.

    Retry logic per attempt:
      - If the response was truncated (finish_reason == "length"), the JSON is
        incomplete, so we do not accept it: we retry at the next, larger budget.
      - Otherwise we judge the parsed entity count against completion_tokens. A low
        tokens-per-entity ratio is plausible (e.g. ~140 tokens for one entity); a
        high ratio (e.g. ~3000 tokens for one entity) means the model produced far
        more text than we extracted, so we escalate the prompt and retry.
      - As soon as a complete, plausible result is found, we trust it and stop.
      - We always keep whichever attempt found the most entities.
    """
    guide = guide or Guide()
    fields = guide.selected()
    b64 = image_to_base64(pdf.render_page(pdf_path, page, config.dpi))
    schedule = config.max_tokens_schedule
    limit = config.tokens_per_entity_limit
    best_result = None

    for attempt, max_tokens in enumerate(schedule):
        completion = client.complete(page_content(prompt, b64, config.prompt.reminder(attempt, guide)), max_tokens)
        if budget is not None:
            budget.check(completion.prompt_tokens, "Pages")

        entities = parse_extraction(completion.content, fields)
        entity_count = len(entities)
        tokens_per_entity = completion.completion_tokens / entity_count if entity_count else float("inf")

        events.info(
            f"  Page {page}: attempt {attempt + 1} → {entity_count} entity(ies) "
            f"({completion.completion_tokens}/{max_tokens} tokens, {tokens_per_entity:.0f} tok/entity, "
            f"finish={completion.finish_reason})"
        )

        # Keep the best attempt seen so far.
        if best_result is None or entity_count > len(best_result):
            best_result = entities

        # Truncated → the JSON is incomplete; retry at the next (larger) budget.
        if completion.finish_reason == "length":
            if attempt < len(schedule) - 1:
                events.info(f"  Page {page}: truncated at {max_tokens} tokens — retrying with {schedule[attempt + 1]}")
                continue
            events.info(
                f"  Page {page}: still truncated at the largest budget ({max_tokens}) — "
                "parsing what we have (consider raising max_tokens_schedule)"
            )
            break

        # Complete response → accept if the entity count is plausible for its size.
        if is_plausible_entity_count(entity_count, completion.completion_tokens, limit):
            return best_result

        # Implausible (under-extracted) → escalate prompt + budget and retry.
        if attempt < len(schedule) - 1:
            events.info(f"  Page {page}: {tokens_per_entity:.0f} tok/entity exceeds limit ({limit}) — "
                        "escalating prompt and retrying")

    events.info(f"  Page {page}: final result — {len(best_result)} entity(ies)")
    return best_result


def pages_by_file(output_csv: Path) -> Dict[str, Set[int]]:
    """1-based pages already saved, per filename."""
    done: Dict[str, Set[int]] = {}
    if not output_csv.exists():
        return done
    with open(output_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            done.setdefault(row["_filename"], set()).add(int(row["_page"]))
    return done


def results_fields(guide: Guide) -> List[str]:
    """The keys of the rows saved to the results file, in column order: only the fields that were asked for."""
    return ["_filename", "_page", "_entity"] + guide.selected()


def results_header(guide: Guide) -> List[str]:
    """The header of the results file. An extra column is called what the user named it."""
    return ["_filename", "_page", "_entity"] + list(guide.headers().values())


def append_page(output_csv: Path, filename: str, page: int, entities: List[Dict[str, Any]],
                fields: Optional[List[str]] = None) -> None:
    with open(output_csv, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields or EXTRACTION_COLUMNS, extrasaction="ignore")
        for entity_index, data in enumerate(entities):
            if "_parse_error" in data:
                data = {"NOTES": "ERROR: could not parse the model output"}
            writer.writerow({"_filename": filename, "_page": page, "_entity": entity_index + 1, **data})


def run_extraction(pdf_files: List[Path], output_csv: Path, client: ChatClient, config: ExtractionConfig,
                   events: EventLog, pages: Optional[Dict[str, Set[int]]] = None,
                   n_ctx: Optional[int] = None, guide: Optional[Guide] = None) -> int:
    """Extract every page of every PDF, or only the given 1-based pages per filename
    (the pages that passed triage). Each page is saved as soon as it is done.

    Pages already in output_csv are skipped, so an interrupted run resumes.
    Failed pages are not recorded, so the next run retries them. Returns the
    number of failed pages.
    """
    guide = guide or Guide()
    fields = results_fields(guide)
    if not output_csv.exists():
        with open(output_csv, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(results_header(guide))
    prompt = config.prompt.render(guide)
    budget = ContextBudget(n_ctx, max(config.max_tokens_schedule), events)
    already = pages_by_file(output_csv)
    if already:
        events.info(f"Resuming — skipping {sum(len(p) for p in already.values())} already processed page(s)")

    plans = []
    for pdf_path in pdf_files:
        if pages is not None:
            todo = sorted(pages.get(pdf_path.name, set()))
        else:
            try:
                todo = list(range(1, pdf.page_count(pdf_path) + 1))
            except Exception as e:
                events.error(f"Cannot open {pdf_path.name}: {e}", stage="extraction", file=pdf_path.name)
                continue
        plans.append((pdf_path, [p for p in todo if p not in already.get(pdf_path.name, set())]))

    total = sum(len(todo) for _, todo in plans)
    events.emit("stage_started", f"Extraction: {total} page(s) to send to the model", stage="extraction", total=total)
    sent = failed = 0
    for i, (pdf_path, todo) in enumerate(plans):
        filename = pdf_path.name
        if not todo:
            continue
        events.info(f"[{i + 1}/{len(plans)}] Extracting {filename}, pages {todo}")
        entity_rows = 0
        with ThreadPoolExecutor(max_workers=config.workers, thread_name_prefix="page_worker") as executor:
            futures = {executor.submit(extract_page, client, pdf_path, p, config, prompt, events, budget, guide): p
                       for p in todo}
            try:
                for future in as_completed(futures):
                    page = futures[future]
                    sent += 1
                    try:
                        entities = future.result()
                    except Exception as e:
                        failed += 1
                        events.error(f"  Page {page} error: {e}", stage="extraction", file=filename, page=page)
                        continue
                    append_page(output_csv, filename, page, entities, fields)
                    entity_rows += len(entities)
                    events.emit("page_done", stage="extraction", file=filename, page=page, done=sent, total=total,
                                rows=len(entities))
            except KeyboardInterrupt:
                events.info("\nInterrupted — cancelling pending pages...")
                executor.shutdown(wait=False, cancel_futures=True)
                raise
        events.emit("file_done", f"  Saved {entity_rows} entity row(s) for {filename}",
                    stage="extraction", file=filename, rows=entity_rows)

    events.emit("stage_done", f"Extraction done. Results saved to {output_csv}"
                + (f" ({failed} page(s) failed; run again to retry them)" if failed else ""),
                stage="extraction", output=str(output_csv), failed=failed)
    return failed
