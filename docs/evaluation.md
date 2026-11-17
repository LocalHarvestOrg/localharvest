# Evaluation

How new models are scored before they are added to the app's curated list (`table_extract/server/models.json`).

- **The benchmark** runs a curated model through the sidecar on the annotated documents and scores it (speed, precision, recall, F1). It is not in this repository: it runs on the server that holds the annotations, which are real documents and never leave it. Its aggregate scores, which name no document, are in `table_extract/server/benchmarks/<model>.json` and are shown in the app's Model step.
- **`utils/calculate_performance.py`** is the fuzzy row matcher and field scorer the benchmark uses, described below. `utils/calculate_performance_triage.py` scores triage, and the notebooks in `utils/` are exploratory.

```bash
uv run --with scikit-learn python utils/calculate_performance.py <predictions_file> <ground_truth_file>
```


---

## Fields evaluated

| Field | Comparison method |
|---|---|
| `company_name` | Cosine similarity (TF-IDF char n-grams) |
| `relation_name` | Cosine similarity (TF-IDF char n-grams) |
| `relation_address` | Cosine similarity (TF-IDF char n-grams) |
| `relation_country` | Exact match after normalization |
| `relation_nationality` | Exact match after normalization |
| `relation_type` | Exact match after normalization |
| `relationship` | Exact match after normalization |

Text normalization (applied before any comparison): lowercase + strip punctuation.

---

## Row matching

Before fields can be compared, each prediction must be paired with its ground-truth counterpart. `find_matching_row` does this by computing a **combined similarity score** for every GT candidate:

$$\text{score} = \text{cosine}(\texttt{relation\_name}) + \text{cosine}(\texttt{relation\_address})$$

The GT row with the highest score is selected, provided it clears a minimum threshold (default **1.0** out of a maximum of 2.0). If no candidate clears the threshold, the prediction is unmatched.

---

## Field-level comparison (`compare_row`)

Once a pair is matched, every field is compared individually. Cosine-scored fields produce both a **raw score** (0–1) and a **boolean match flag** driven by a configurable threshold (default **0.6** for all three). Exact-match fields produce only a boolean flag.

A row is marked `overall_match = True` only when **every** field's boolean match flag is `True`.

---

## Precision / Recall evaluation (`evaluate_predictions`)

The evaluation runs two passes:

**Pass 1 — loop over predictions**
- For each prediction, attempt to find a matching GT row.
- **Match found** → record as a candidate true positive; run `compare_row`.
- **No match found** → record as a **false positive** (FP).

**Pass 2 — loop over ground truth**
- Any GT row that was never claimed by a prediction is a **false negative** (FN).

**Counting TP / FP**

| Case | Classification |
|---|---|
| Matched pair where `overall_match = True` | True Positive (TP) |
| Matched pair where `overall_match = False` | False Positive (FP) — model found the row but got fields wrong |
| Prediction with no GT match | False Positive (FP) |
| GT row with no matching prediction | False Negative (FN) |

**Metrics**

$$\text{Precision} = \frac{TP}{TP + FP}$$

$$\text{Recall} = \frac{TP}{TP + FN}$$

$$F_1 = \frac{2 \cdot \text{Precision} \cdot \text{Recall}}{\text{Precision} + \text{Recall}}$$

---

## Return values

`evaluate_predictions` returns a 4-tuple:

| Variable | Contents |
|---|---|
| `tp_df` | All matched pairs, with `pred_*` and `gt_*` columns side-by-side, plus cosine scores and match flags |
| `fp_df` | Predictions that could not be matched to any GT row |
| `fn_df` | GT rows that no prediction claimed |
| `metrics` | Dict: `TP`, `FP`, `FN`, `precision`, `recall`, `f1` |

---

## Configurable thresholds

All thresholds can be passed to `evaluate_predictions`:

| Parameter | Default | Controls |
|---|---|---|
| `name_threshold` | 0.6 | Minimum cosine score for `relation_name` to count as a match |
| `address_threshold` | 0.6 | Minimum cosine score for `relation_address` to count as a match |
| `company_threshold` | 0.6 | Minimum cosine score for `company_name` to count as a match |
| `match_threshold` *(in `find_matching_row`)* | 1.0 | Minimum combined score (name + address) to accept a GT pairing |
