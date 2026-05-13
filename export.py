#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import sqlite3

import pandas as pd

from repofinder.analysis.plot_utils import filter_data, acronyms


def _load_config(acronym):
    config_path = f"config/config_{acronym.lower()}.json"
    with open(config_path) as f:
        return json.load(f)


def _export_one_gpt(acronym, threshold, db_path, university_name):
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT full_name, affiliation_prediction_gpt_5_mini, size, archived, fork, is_template FROM repositories",
        conn,
    )
    conn.close()
    filtered = filter_data(df, threshold)
    return [
        {
            "full_name": row["full_name"],
            "university": university_name,
            "affiliation_score": round(float(row["affiliation_prediction_gpt_5_mini"]), 4),
        }
        for _, row in filtered.iterrows()
        if pd.notna(row["full_name"])
    ]


def _export_one_sbc(acronym, threshold, db_path, university_name):
    sbc_path = f"results/{acronym}/predictions_sbc_{acronym}.csv"
    if not os.path.exists(sbc_path):
        print(f"Skipping {acronym}: SBC predictions not found at {sbc_path}")
        return []

    sbc_df = pd.read_csv(sbc_path)

    conn = sqlite3.connect(db_path)
    repo_df = pd.read_sql_query(
        "SELECT full_name, html_url, size, archived, fork, is_template FROM repositories",
        conn,
    )
    conn.close()

    merged = repo_df.merge(sbc_df[["html_url", "total_score"]], on="html_url", how="inner")
    filtered = filter_data(merged.rename(columns={"total_score": "affiliation_prediction_gpt_5_mini"}), threshold)
    return [
        {
            "full_name": row["full_name"],
            "university": university_name,
            "affiliation_score": round(float(row["affiliation_prediction_gpt_5_mini"]), 4),
        }
        for _, row in filtered.iterrows()
        if pd.notna(row["full_name"])
    ]


def _export_one(acronym, threshold, predictor):
    db_path = f"Data/db/repository_data_{acronym}_database.db"
    if not os.path.exists(db_path):
        print(f"Skipping {acronym}: database not found at {db_path}")
        return []

    config = _load_config(acronym)
    university_name = config["UNIVERSITY_NAME"]

    if predictor == "sbc":
        return _export_one_sbc(acronym, threshold, db_path, university_name)
    return _export_one_gpt(acronym, threshold, db_path, university_name)


def main():
    parser = argparse.ArgumentParser(
        description="Export university-affiliated repos from repofinder as JSON for repo-pulse."
    )
    parser.add_argument(
        "--acronym",
        nargs="+",
        default=acronyms,
        metavar="ACRONYM",
        help=f"University acronym(s) to export (default: all). Choices: {', '.join(acronyms)}",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.7,
        help="Minimum affiliation score to include (default: 0.7)",
    )
    parser.add_argument(
        "--predictor",
        choices=["gpt", "sbc"],
        default="gpt",
        help="Affiliation predictor to use: 'gpt' (default) or 'sbc' (score-based, no LLM required)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write all results to a single combined JSON file instead of per-university files",
    )
    parser.add_argument(
        "--data-dir",
        default="exports/universities",
        help="Directory for per-university JSON files (default: data/universities)",
    )
    args = parser.parse_args()

    results = []
    for acronym in args.acronym:
        repos = _export_one(acronym.upper(), args.threshold, args.predictor)
        print(f"{acronym.upper()}: {len(repos)} repos exported")
        results.extend(repos)

        if args.output is None:
            os.makedirs(args.data_dir, exist_ok=True)
            out_path = os.path.join(args.data_dir, f"{acronym.lower()}.json")
            with open(out_path, "w") as f:
                json.dump(repos, f, indent=2)

    if args.output is not None:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nTotal: {len(results)} repos written to {args.output}")
    else:
        print(f"\nTotal: {len(results)} repos written to {args.data_dir}/")


if __name__ == "__main__":
    main()
