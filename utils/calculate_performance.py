"""
calculate_performance.py — Compare model predictions against ground truth.

Usage:
    python calculate_performance.py <predictions_file> <ground_truth_file> [options]

Arguments:
    predictions_file   Path to CSV/JSON file with model predictions
    ground_truth_file  Path to CSV/JSON file with ground truth

Options:
    --name-threshold    float  Cosine threshold for relation_name    (default: 0.6)
    --address-threshold float  Cosine threshold for relation_address (default: 0.6)
    --company-threshold float  Cosine threshold for company_name     (default: 0.6)
    --match-threshold   float  Combined score threshold for row pairing (default: 1.0)

Outputs (written to --output-dir):
    tp_<timestamp>.csv   — matched pairs with field-level scores
    fp_<timestamp>.csv   — predictions with no GT match
    fn_<timestamp>.csv   — GT rows never predicted
"""

import argparse
import re
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# utils

def normalize_text(s: str) -> str:
    """Lowercase and strip punctuation."""
    # convert it first to string just in case
    s = str(s)
    s = s.lower()
    s = re.sub(r'[^\w\s]', '', s)
    return s.strip()


def text_match(s1: str, s2: str) -> bool:
    """Exact match after normalization."""
    return normalize_text(str(s1)) == normalize_text(str(s2))


def cosine_sim(s1: str, s2: str) -> float:
    # check if they are nans and convert them to zero
    if not s1:
        s1 = float('nan')
    if not s2:
        s2 = float('nan')
    """Cosine similarity between two strings using TF-IDF character n-grams."""
    if not s1 and not s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    vec = TfidfVectorizer(analyzer='char_wb', ngram_range=(2, 3))
    try:
        tfidf = vec.fit_transform([normalize_text(s1), normalize_text(s2)])
        return float(cosine_similarity(tfidf[0], tfidf[1])[0][0])
    except ValueError:
        return 0.0


# ── row matching ───────────────────────────────────────────────────────────────

def find_matching_row(source_row: pd.Series, target_df: pd.DataFrame,
                      match_threshold: float = 0.6) -> pd.Series | None:
    """
    Find the best-matching row in `target_df` for `source_row` using:
      - relation_name    : cosine similarity → 0..1
      - relation_address : cosine similarity → 0..1

    Returns the best candidate above `match_threshold`, or None.
    Used symmetrically for both pred→GT and GT→pred lookups.
    """
    best_row = None
    best_score = -1.0

    for _, row in target_df.iterrows():
        name_score    = cosine_sim(source_row['relation_name'],    row['relation_name'])
        # address_score = cosine_sim(source_row['relation_address'], row['relation_address'])
        total = name_score # + address_score

        if total > best_score:
            best_score = total
            best_row   = row

    return best_row if best_score >= match_threshold else None


# ── field-level comparison ─────────────────────────────────────────────────────

def compare_row(pred_row: pd.Series, gt_row: pd.Series,
                address_threshold: float = 0.6,
                name_threshold: float = 0.6,
                company_threshold: float = 0.6) -> dict:
    """
    Compare one prediction row against its matched ground-truth row field by field.

    Returns a dict with cosine scores, boolean match flags, and overall_match.
    """
    exact_fields = ['relation_country', 'relation_nationality',
                    'relation_type', 'relationship']
    result = {}

    company_score = cosine_sim(pred_row['company_name'], gt_row['company_name'])
    result['company_name_cosine'] = round(company_score, 4)
    result['company_name_match']  = company_score >= company_threshold

    name_score = cosine_sim(pred_row['relation_name'], gt_row['relation_name'])
    result['relation_name_cosine'] = round(name_score, 4)
    result['relation_name_match']  = name_score >= name_threshold

    addr_score = cosine_sim(pred_row['relation_address'], gt_row['relation_address'])
    result['relation_address_cosine'] = round(addr_score, 4)
    result['relation_address_match']  = addr_score >= address_threshold

    for field in exact_fields:
        result[f'{field}_match'] = text_match(pred_row[field], gt_row[field])

    result['overall_match'] = all(v for k, v in result.items() if k.endswith('_match'))
    return result


# ── full evaluation ────────────────────────────────────────────────────────────

_COSINE_FIELDS = ['company_name', 'relation_name', 'relation_address']
_EXACT_FIELDS  = ['relation_country', 'relation_nationality', 'relation_type', 'relationship']
_ALL_FIELDS    = _COSINE_FIELDS + _EXACT_FIELDS

def _empty_comparison(overall: bool = False) -> dict:
    """Return a zeroed-out comparison dict (used for FP / FN rows)."""
    d = {f'{f}_match': False for f in _ALL_FIELDS}
    d.update({f'{f}_cosine': 0.0 for f in _COSINE_FIELDS})
    d['overall_match'] = overall
    return d


def evaluate_predictions(model_predictions: pd.DataFrame,
                         ground_truth: pd.DataFrame,
                         address_threshold: float = 0.6,
                         name_threshold: float = 0.6,
                         company_threshold: float = 0.6,
                         ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """
    Full precision/recall evaluation.

    Returns
    -------
    tp_df   : matched pairs with field-level comparison (true positives)
    fp_df   : predictions with no GT match              (false positives)
    fn_df   : GT rows never predicted                   (false negatives)
    metrics : dict with precision, recall, F1
    """
    kw = dict(address_threshold=address_threshold,
              name_threshold=name_threshold,
              company_threshold=company_threshold)

    tp_records, fp_records = [], []
    matched_gt_indices = set()

    # ── loop over predictions → find TP and FP ────────────────────────────────
    for _, pred_row in model_predictions.iterrows():
        gt_row = find_matching_row(pred_row, ground_truth)

        base = {f'pred_{k}': v for k, v in pred_row.items()}

        if gt_row is not None:
            matched_gt_indices.add(gt_row.name)
            gt_base = {f'gt_{k}': v for k, v in gt_row.items()}
            comparison = compare_row(pred_row, gt_row, **kw)
            tp_records.append({**base, **gt_base, **comparison})
        else:
            fp_records.append({**base, **_empty_comparison()})

    # ── loop over GT → find FN (GT rows never matched) ────────────────────────
    fn_records = []
    for idx, gt_row in ground_truth.iterrows():
        if idx not in matched_gt_indices:
            fn_records.append({f'gt_{k}': v for k, v in gt_row.items()})

    tp_df = pd.DataFrame(tp_records)
    fp_df = pd.DataFrame(fp_records)
    fn_df = pd.DataFrame(fn_records)

    return tp_df, fp_df, fn_df

def calc_metrics(fp_df, tp_df, fn_df, col='overall_match'):
    tp = len(tp_df[tp_df[col]]) if not tp_df.empty else 0
    fp = len(fp_df) + (len(tp_df) - tp)   # unmatched preds + matched-but-wrong fields
    fn = len(fn_df)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1        = (2 * precision * recall / (precision + recall)
                 if (precision + recall) > 0 else 0.0)

    metrics = dict(field=col,
                   TP=tp, FP=fp, FN=fn,
                   precision=round(precision, 4),
                   recall=round(recall, 4),
                   f1=round(f1, 4))
    return metrics

def load_file(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        sys.exit(f"Error: file not found: {path}")
    suffix = p.suffix.lower()
    if suffix == '.csv':
        data = pd.read_csv(p)
        #data.fillna("", inplace=True)
        string_cols = data.select_dtypes(include=['object', 'string']).columns
        data[string_cols] = data[string_cols].fillna("")
        return data
    elif suffix == '.json':
        print("JSON support not implemented")
        return pd.read_json(p)
    else:
        sys.exit(f"Error: unsupported file format '{suffix}'. Use .csv or .json.")

def reorder_cols(df):
    match_col_reorder = ['gt_Document', 'pred__filename', 'pred__page', 'pred__entity', 
        'gt_company_name', 'pred_company_name', 'company_name_cosine', 'company_name_match',
        'gt_jurisdiction','pred_jurisdiction', 'gt_relation_name', 'pred_relation_name','relation_name_cosine',
        'relation_name_match','gt_relation_address', 'pred_relation_address', 'relation_address_cosine',
        'relation_address_match','gt_relation_country','pred_relation_country','relation_country_match',
        'gt_relation_nationality','pred_relation_nationality','relation_nationality_match','gt_relation_type','pred_relation_type', 'relation_type_match',
        'gt_relationship','pred_relationship','relationship_match', 'overall_match','pred_NOTES', 'gt_Responsible', 'gt_NOTES'  ]
    return df[match_col_reorder]

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate model predictions against ground truth.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('predictions_file', nargs='?',  help="Path to predictions CSV/JSON", default="/home/mfridman/olsomet-table-extract/output_qianfan.csv")
    parser.add_argument('ground_truth_file', nargs='?',  help="Path to ground truth CSV/JSON", default="/home/ubuntu/data/template_extraction_0/Template_KYC_Manual - Annotation.csv")
    parser.add_argument('--name-threshold',    type=float, default=0.6)
    parser.add_argument('--address-threshold', type=float, default=0.6)
    parser.add_argument('--company-threshold', type=float, default=0.6)
    args = parser.parse_args()

    print(f"Loading predictions : {args.predictions_file}")
    p = load_file(args.predictions_file)

    print(f"Loading ground truth: {args.ground_truth_file}")
    gt = load_file(args.ground_truth_file)

    # initialize variables
    fp_all = []
    fn_all = []
    matched_all = []

    # filter results to a single page
    files = gt.Document.unique()
    for file in files:
        
        ground_truth = gt[gt.Document==file]

        if "_" in file:
            fn, pg = os.path.splitext(file)[0].split("_")
            fn = fn[2:]
            predictions = p[(p._filename.str.contains(fn)) & (p._page==int(pg))]
        else:
            fn = os.path.splitext(file)[0]
            fn = fn[2:]
            predictions = p[(p._filename.str.contains(fn))]

        # calculate performance
        matched_df, fp_df, fn_df = evaluate_predictions(
            predictions, ground_truth,
            address_threshold=args.address_threshold,
            name_threshold=args.name_threshold,
            company_threshold=args.company_threshold,
        )
        fp_all.append(fp_df)
        fn_all.append(fn_df)
        matched_all.append(matched_df)
    matched_df = reorder_cols(pd.concat(matched_all).sort_values(by=["pred__filename", "pred__page"]))
    fp_df = pd.concat(fp_all).sort_values(by=["pred__filename", "pred__page"])
    print()
    if fn_all[0].empty:
        fn_df = pd.DataFrame()
        
    else:
        fn_df = pd.concat(fn_all).sort_values(by="gt_Document")
    metrics = calc_metrics(fp_df, matched_df, fn_df, 'overall_match')

    out_dir = Path(os.path.join(os.path.split(args.predictions_file)[0], "evaluation"))
    out_dir.mkdir(parents=True, exist_ok=True)

    for name, df in [("matched", matched_df), ("fp", fp_df), ("fn", fn_df)]:
        path = os.path.join(out_dir, f"{name}.csv")
        df.to_csv(path, index=False)
        print(f"  Saved {path}  ({len(df)} rows)")

    with open(os.path.join(out_dir, "metrics.txt"), "w") as text_file:
        text_file.write("Matched records: %d\n" % matched_df.shape[0])
        text_file.write("Records in ground truth not found in predictions: % d\n" % fn_df.shape[0])
        text_file.write("Records in predictions not found in GT: % d\n" % fp_df.shape[0])
        text_file.write("\n── Metrics ───────────────────────────────\n")
        matched_cols = ["overall_match", "company_name_match", 
                        "relation_name_match", "relation_address_match", 
                        "relation_country_match", "relation_nationality_match", 
                        "relation_type_match", "relationship_match"]
        for col in matched_cols:
            metrics = calc_metrics(fp_df, matched_df, fn_df, col)
            for k, v in metrics.items():
                text_file.write(f"  {k}: {v}\n")
            text_file.write("\n─────────────────────────────────\n")

if __name__ == '__main__':
    main()
