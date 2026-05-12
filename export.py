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


def _export_one(acronym, threshold):
    db_path = f"Data/db/repository_data_{acronym}_database.db"
    if not os.path.exists(db_path):
        print(f"Skipping {acronym}: database not found at {db_path}")
        return []

    config = _load_config(acronym)
    university_name = config["UNIVERSITY_NAME"]

    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT full_name, affiliation_prediction_gpt_5_mini, size, archived, fork, is_template FROM repositories",
        conn,
    )
    conn.close()

    filtered = filter_data(df, threshold)
    return [
        {"full_name": row["full_name"], "university": university_name}
        for _, row in filtered.iterrows()
        if pd.notna(row["full_name"])
    ]


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
        "--output",
        default="repos.json",
        help="Output JSON file path (default: repos.json)",
    )
    args = parser.parse_args()

    results = []
    for acronym in args.acronym:
        repos = _export_one(acronym.upper(), args.threshold)
        print(f"{acronym.upper()}: {len(repos)} repos exported")
        results.extend(repos)

    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nTotal: {len(results)} repos written to {args.output}")


if __name__ == "__main__":
    main()
