"""Vision triage: decide page by page whether a page holds a relationship table worth extracting.

Documents whose pages were chosen by hand (TriageConfig.pages) skip the model:
those pages are kept and the others are not.
"""

import csv
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Set

from . import pdf
from .config import TriageConfig, format_pages
from .events import EventLog
from .llm import ChatClient, image_to_base64, page_content
from .parsing import parse_triage
from .prompts import Guide
from .schema import TRIAGE_COLUMNS, TRIAGE_TIMES_COLUMNS


def triage_page(client: ChatClient, pdf_path: Path, page: int, config: TriageConfig, prompt: str) -> Dict[str, str]:
    image = pdf.render_page(pdf_path, page, config.dpi)
    completion = client.complete(page_content(prompt, image_to_base64(image)), config.max_tokens)
    return parse_triage(completion.content)


def pages_by_file(triage_csv: Path, positive_only: bool = False) -> Dict[str, Set[int]]:
    """1-based pages recorded in a triage CSV, per filename; with positive_only, the "true" ones."""
    pages: Dict[str, Set[int]] = {}
    if not triage_csv.exists():
        return pages
    with open(triage_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            found = pages.setdefault(row["_filename"].strip(), set())
            if not positive_only or str(row["_triage_output"]).strip().lower() == "true":
                found.add(int(row["_page"]))
    return pages


def _within(chosen: Set[int], filename: str, total_pages: int, events: EventLog) -> Set[int]:
    """The chosen pages that the document has; the others are reported and left out."""
    beyond = {p for p in chosen if p > total_pages}
    if beyond:
        events.warning(f"{filename} has {total_pages} page(s): leaving out the pages chosen beyond it "
                       f"({format_pages(beyond)})", file=filename)
    return chosen - beyond


def pages_without_triage(pdf_files: List[Path], config: TriageConfig,
                         events: EventLog) -> Optional[Dict[str, Set[int]]]:
    """When the model triage is off: the chosen pages of some documents, every page of the others.
    None when no pages were chosen, which means every page of every document."""
    chosen = config.chosen_pages()
    if not chosen:
        return None
    pages: Dict[str, Set[int]] = {}
    for pdf_path in pdf_files:
        try:
            total_pages = pdf.page_count(pdf_path)
        except Exception as e:
            events.error(f"Cannot open {pdf_path.name}: {e}", stage="extraction", file=pdf_path.name)
            continue
        if pdf_path.name in chosen:
            pages[pdf_path.name] = _within(chosen[pdf_path.name], pdf_path.name, total_pages, events)
            events.info(f"{pdf_path.name}: extracting the pages chosen by hand ({format_pages(pages[pdf_path.name])})")
        else:
            pages[pdf_path.name] = set(range(1, total_pages + 1))
    return pages


def _append(path: Path, columns: List[str], rows: List[dict]) -> None:
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=columns, extrasaction="ignore").writerows(rows)


def _init(path: Path, columns: List[str]) -> None:
    if not path.exists():
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=columns).writeheader()


def run_triage(pdf_files: List[Path], output_csv: Path, times_csv: Optional[Path], client: ChatClient,
               config: TriageConfig, events: EventLog, guide: Optional[Guide] = None) -> int:
    """Triage every page of every PDF, appending each decision to output_csv as soon as it is made.

    Documents with pages chosen by hand are recorded without asking the model. Pages already in output_csv
    are skipped, so an interrupted run resumes. Failed pages are not recorded, so the next run retries them.
    Returns the number of failed pages.
    """
    _init(output_csv, TRIAGE_COLUMNS)
    if times_csv is not None:
        _init(times_csv, TRIAGE_TIMES_COLUMNS)
    prompt = config.prompt.render(guide or Guide())
    already = pages_by_file(output_csv)
    if already:
        events.info(f"Resuming — skipping {sum(len(p) for p in already.values())} already processed page(s)")

    chosen = config.chosen_pages()
    plans = []  # (pdf_path, pages to send to the model)
    for pdf_path in pdf_files:
        try:
            total_pages = pdf.page_count(pdf_path)
        except Exception as e:
            events.error(f"Cannot open {pdf_path.name}: {e}", stage="triage", file=pdf_path.name)
            continue
        done = already.get(pdf_path.name, set())
        todo = [p for p in range(1, total_pages + 1) if p not in done]
        if pdf_path.name in chosen:
            kept = _within(chosen[pdf_path.name], pdf_path.name, total_pages, events)
            _append(output_csv, TRIAGE_COLUMNS, [{
                "_filename": pdf_path.name, "_page": p, "_triage_output": "true" if p in kept else "false",
                "_notes": "Chosen by hand" if p in kept else "Not chosen by hand",
            } for p in todo])
            events.info(f"Triage {pdf_path.name}: pages chosen by hand ({format_pages(kept) or 'none'}), "
                        "the model does not look at it")
            continue
        plans.append((pdf_path, todo))

    total = sum(len(send) for _, send in plans)
    events.emit("stage_started", f"Triage: {total} page(s) to send to the model", stage="triage", total=total)
    sent = failed = 0
    for i, (pdf_path, send) in enumerate(plans):
        filename = pdf_path.name
        events.info(f"[{i + 1}/{len(plans)}] Triage {filename}: {len(send)} page(s) to send")
        if not send:
            continue

        start = time.perf_counter()
        hits = 0
        with ThreadPoolExecutor(max_workers=config.workers, thread_name_prefix="page_worker") as executor:
            futures = {executor.submit(triage_page, client, pdf_path, p, config, prompt): p for p in send}
            try:
                for future in as_completed(futures):
                    page = futures[future]
                    sent += 1
                    try:
                        result = future.result()
                    except Exception as e:
                        failed += 1
                        events.error(f"  Page {page} error: {e}", stage="triage", file=filename, page=page)
                        continue
                    _append(output_csv, TRIAGE_COLUMNS, [{
                        "_filename": filename, "_page": page,
                        "_triage_output": result["triage_output"], "_notes": result["notes"],
                    }])
                    hits += result["triage_output"] == "true"
                    events.emit("page_done", f"  Page {page}: {result['triage_output']} [{sent}/{total}]",
                                stage="triage", file=filename, page=page, done=sent, total=total,
                                decision=result["triage_output"])
            except KeyboardInterrupt:
                events.info("\nInterrupted — cancelling pending pages...")
                executor.shutdown(wait=False, cancel_futures=True)
                raise

        seconds_per_page = (time.perf_counter() - start) / len(send)
        if times_csv is not None:
            _append(times_csv, TRIAGE_TIMES_COLUMNS, [
                {"filename": filename, "times": f"{seconds_per_page:.4f}", "average": seconds_per_page}
            ])
        events.emit("file_done", f"  {hits}/{len(send)} pages found that need data extraction "
                    f"({seconds_per_page:.1f} s/page)", stage="triage", file=filename)

    events.emit("stage_done", f"Triage done. Results saved to {output_csv}"
                + (f" ({failed} page(s) failed; run again to retry them)" if failed else ""),
                stage="triage", output=str(output_csv), failed=failed)
    return failed
