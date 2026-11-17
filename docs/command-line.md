# Command line

`python -m table_extract` runs the same pipeline as the app: triage, then extraction of the pages triage kept. It needs a model server on this computer, see [model-servers.md](model-servers.md).

## Setup

```bash
python3.12 -m venv .venv_extract && source .venv_extract/bin/activate
pip install uv
pip install -r requirements_extract.txt
```

No system packages are needed: PDFs are read with pdfium, which ships inside the `pypdfium2` wheel.

## Running

```bash
python -m table_extract defaults > run.json   # every setting at its default
python -m table_extract run run.json
```

Edit `inputs` (PDFs or folders), `output_dir` and `server.model` in `run.json` first. `server.model` must be the name the server knows the model by (llama-server's `--alias`). The shortcuts `qwen`, `qwen4` and `qianfan` stand for `models/qwen3-vl-8b`, `models/qwen3-vl-4b` and `models/qianfan-ocr`, the paths used when serving with vLLM.

- **Resume**: run the same config again, or point at the `run.json` in the output folder: `python -m table_extract run path/to/results/run.json`. Pages already done are skipped.
- **Different settings, same folder**: refused, unless you pass `--overwrite`, which deletes the old results first.
- **Choosing pages by hand**: `"triage": {"pages": {"annual-report.pdf": "3, 7-12"}}` extracts only those pages of that file (counted from the start of the PDF) and skips triage for it. `triage.csv` records them as "Chosen by hand". With `"triage": {"enabled": false}`, the other documents have every page extracted.
- **Always triage** unless you chose the pages: extraction treats a page with no entities as a failed attempt and retries it up to three times, which is slow on a laptop.

## Output

The output folder holds:

| File | Contents |
|---|---|
| `relationships.csv` | The extracted relationships, one row each |
| `triage.csv` | One decision per page: `_filename`, `_page`, `_triage_output` (`true`/`false`), `_notes` |
| `triage_times.csv` | Seconds per page, per file |
| `run.json` | The full config, prompt hashes, server and code versions, status |
| `prompt_triage.txt`, `prompt_extraction.txt` | The prompts exactly as sent |
| `events.jsonl` | Machine-readable progress |

`relationships.csv` columns:

| Column | Meaning |
|---|---|
| `_filename`, `_page`, `_entity` | Source PDF, 1-based page, 1-based entity on that page |
| `main_entity` | The company the document is about |
| `jurisdiction` | Where it is registered, if stated |
| `relation_name` | The related person or company |
| `relation_address`, `relation_country`, `relation_nationality` | If stated |
| `relation_type` | `Person` or `Company` |
| `relationship` | What `relation_name` is to `main_entity`: `Owner`, `Director`, `Secretary`… |
| `custom_1` … `custom_3` | Extra fields you define in the prompt settings |
| `NOTES` | Anything else; also `ERROR: ...` when the model's answer could not be parsed |

Pages that fail outright (for example when the server stops) are not written, so running again retries them.

## How extraction works

- **Escalating retries.** Up to three attempts per page, each with a firmer prompt and a larger output budget (`max_tokens_schedule`: 4096, 8192, 12288 tokens). The attempt that found the most entities is kept.
- **Plausibility check.** A complete answer is accepted only if it used at most `tokens_per_entity_limit` (200) output tokens per entity found. A fully filled entity takes about 120–150 tokens, so a higher ratio means the model wrote a lot and extracted little.
- **Truncation.** An answer cut off by the output limit is never accepted; the page is retried with the next budget.
- **JSON recovery.** The parser copes with code fences, duplicate keys, concatenated objects and missing brackets.
- **Parallel pages.** As many pages are sent at once as the server has slots (llama-server's `-np`), or 4 if it does not say (vLLM). Override with `workers`.

## Tuning

Every setting is in `run.json`; the defaults are in `table_extract/config.py`.

- `extraction.max_tokens_schedule`: its length is the number of attempts. Keep the largest value within the server's context per slot minus the page and prompt (about 5,800 tokens at 200 DPI); the run warns when it does not fit.
- `extraction.tokens_per_entity_limit`: lower it to retry more eagerly, raise it to tolerate verbose single entities.
- `triage.dpi` (150) and `extraction.dpi` (200): raise for small print, lower for speed and memory.
- `sampling` (temperature, top_p, top_k, seed): unset keeps the server's defaults.
- Prompts: the defaults are text files in `table_extract/default_prompts/`. The role mapping (Shareholder → Owner, UBO → Owner…) is in the field descriptions, `extraction/fields.json`. The field list and output format are fixed in `table_extract/schema.py` and `prompts.py`, because the parser depends on them.

Each attempt logs `completion/budget tokens` and `tok/entity`, the quickest way to calibrate the budgets and the limit on a real run.

## Keeping documents local

- Server addresses not on this computer (`localhost`, `127.0.0.1`, `::1`) are refused unless `server.allow_remote` is `true`. Leave it off for sensitive documents.
- If the server needs an API key, export it as `TABLE_EXTRACT_API_KEY`. It is never written to the output folder.
- The pipeline makes no other network calls.
