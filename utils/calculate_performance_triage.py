"""
calculate_performance_triage.py — Compare model predictions against ground truth.

Usage:
    python calculate_performance_triage.py <predictions_file> <ground_truth_file> [options]

Arguments:
    predictions_file   Path to CSV/JSON file with model predictions (optional times file)
    ground_truth_file  Path to CSV/JSON file with ground truth



Outputs (written to --output-dir):
    tp_<timestamp>.csv   — matched pairs percentage and time stats.
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



def calculate_performance(id,fp,fn,matched) -> pd.DataFrame:
    n_fp = len(fp)
    n_fn = len(fn)
    n_matched = len(matched)
    
    precision = n_matched / (n_matched + n_fp) if (n_matched + n_fp) else 0
    recall = n_matched / (n_matched + n_fn) if (n_matched + n_fn) else 0
    f1 = (2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0)
    
    metrics = dict(document=id,
                       precision=round(precision, 4),
                       recall=round(recall, 4),
                       f1=round(f1, 4))
    return pd.DataFrame([metrics])


def convert_df(id,pages) -> pd.DataFrame: 
    
    row = {
        'document': id,
        'pages': list(pages),
    }
    return pd.DataFrame([row])
    


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
        
        

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate model predictions against ground truth.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('predictions_file', nargs='?',  help="Path to predictions CSV/JSON", default="/home/oiho/olsomet-table-extract/output_triage")
    parser.add_argument('ground_truth_file', nargs='?',  help="Path to ground truth CSV/JSON", default="/home/oihho/data/Triage annotation - annotations.csv")
    args = parser.parse_args()

    print(f"Loading predictions : {args.predictions_file}")
    # get  the files (prediction + time if exist)
    predictions_dir = Path(args.predictions_file)
    files = [file for file in predictions_dir.iterdir() if 'triage' in file.name]
    # prediction file 
    pred_file = [file for file in files if 'times' not in file.name]
    if not pred_file:
        raise FileNotFoundError("No prediction file found")
    
    p = load_file(pred_file[0])

    times_file = [file for file in files if 'times' in file.name]
    if times_file:
        t = load_file(times_file[0])
    else: 
        print("no file containing times, will procede without it")
    

    print(f"Loading ground truth: {args.ground_truth_file}")
    gt = load_file(args.ground_truth_file)

    # initialize variables
    fp_all = pd.DataFrame(columns=['document','pages'])
    fn_all = pd.DataFrame(columns=['document','pages'])
    matched_all = pd.DataFrame(columns=['document','pages'])
    metrics_df = pd.DataFrame()
    


    # filter results to a single page
    files = gt._filename.unique()
    for file in files:#['<document-id>.pdf']:#
        
        ground_truth = gt[gt._filename==file]
        gt_pages = set(
                int(page.strip())
                for row in ground_truth['final_flagged'].dropna().astype(str)
                if row.strip()  # skip empty strings
                for page in row.split(',')
                if page.strip()  # skip empty pieces after split
                )
        
       
        pred = p[p._filename==file]
        # get the pages classified as true 
        hits = set(pred.loc[pred['_triage_output'].isin(['true', 'True', True]), '_page'].tolist())
        
        # check 
        fp = hits - gt_pages
        fn = gt_pages - hits 
        matched = gt_pages & hits 
        
        # calculate performance per document
        df_row = calculate_performance(str(file),fp,fn,matched)
        
        # convert them to dictionaries 
        matched_df = convert_df(str(file),matched)
        fp_df = convert_df(str(file),fp)
        fn_df = convert_df(str(file),fn)
        
        # append
        fp_all = pd.concat([fp_all,fp_df], ignore_index=True)
        fn_all = pd.concat([fn_all,fn_df], ignore_index=True)
        matched_all = pd.concat([matched_all,matched_df], ignore_index=True)
        metrics_df = pd.concat([metrics_df,df_row], ignore_index=True)
    



   
    # calculate the overall metrics 
    fp_total = [page for allpages in fp_all['pages'] for page in allpages]
    fn_total = [page for allpages in fn_all['pages'] for page in allpages]
    match_total = [page for allpages in matched_all['pages'] for page in allpages]
    
    metrics_total = calculate_performance('total',fp_total,fn_total,match_total)
    
    # save the data
    out_dir = Path(os.path.join(os.path.split(args.predictions_file)[0], "evaluation"))
    out_dir = Path(os.path.join(args.predictions_file, "evaluation"))
    out_dir.mkdir(parents=True, exist_ok=True)

    for name, df in [("matched", matched_all), ("fp", fp_all), ("fn", fn_all),('metricts',metrics_df)]:
        path = os.path.join(out_dir, f"{name}.csv")
        df.to_csv(path, index=False)
        print(f"  Saved {path}  ({len(df)} rows)")


    

    with open(os.path.join(out_dir, "metrics.txt"), "w") as text_file:
        text_file.write("Matched records: %d\n" % len(match_total))
        text_file.write("Records in ground truth not found in predictions: % d\n" % len(fn_total))
        text_file.write("Records in predictions not found in GT: % d\n" % len(fp_total))
        text_file.write("\n── Metrics ───────────────────────────────\n")
        
        metrict2save = metrics_total.iloc[0].to_dict()
        for k, v in metrict2save.items():
            text_file.write(f"  {k}: {v}\n")
            text_file.write("\n─────────────────────────────────\n")

if __name__ == '__main__':
    main()

