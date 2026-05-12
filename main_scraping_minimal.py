#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Minimal scraping pipeline for university affiliation export.
# Skips README, contributor, and org-enrichment fetching to run in minutes
# instead of hours. Suitable for feeding repo slugs to repo-pulse via export.py.
#
# Steps run:
#   1. search_repositories      — find repos by university keywords
#   2. search_organizations     — find affiliated orgs
#   3. get_repos_from_orgs      — expand via org membership
#   4. search_users             — find affiliated users
#   5. get_repos_from_users     — expand via user ownership
#   6. compute_predictions_sbc  — keyword/heuristic affiliation scoring (no LLM)
#
# Steps skipped vs main_scraping.py:
#   - get_features_data     (README, security policy, release data — slow)
#   - get_organization_data (org metadata enrichment — many API calls)
#   - get_contributor_data  (contributor profiles — slow)

from repofinder.scraping.search_repositories import search_repositories
from repofinder.scraping.search_organizations import search_organizations
from repofinder.scraping.search_users import search_users
from repofinder.scraping.repo_scraping_utils import get_repositories_from_organizations, get_repositories_from_users
from repofinder.scraping.json_to_db import create_and_populate_database, populate_organizations, populate_users
from repofinder.filtering.score_based_classifier import compute_predictions_sbc
from dotenv import load_dotenv
import os
import sqlite3

DOTENV = ".env"
load_dotenv(DOTENV)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {
    'Authorization': f'token {GITHUB_TOKEN}',
    'Accept': 'application/vnd.github.v3+json',
}


def scrape_minimal(university_acronyms=["UCSC"]):
    for acronym in university_acronyms:
        config_file = f"config/config_{acronym}.json"
        repo_file = f"Data/json/repository_data_{acronym}.json"
        org_file = f"Data/json/organization_data_{acronym}.json"
        repo_from_orgs_file = f"Data/json/repository_data_org_scraping_{acronym}.json"
        user_file = f"Data/json/user_data_{acronym}.json"
        repo_from_users_file = f"Data/json/repository_data_user_scraping_{acronym}.json"
        db_file = f"Data/db/repository_data_{acronym}_database.db"

        os.makedirs("Data/json", exist_ok=True)
        os.makedirs("Data/db", exist_ok=True)

        print(f"[{acronym}] Searching repositories...")
        search_repositories(config_file, HEADERS)
        create_and_populate_database(repo_file, db_file, search_method='repository_search')

        print(f"[{acronym}] Searching organizations...")
        search_organizations(config_file, HEADERS)
        populate_organizations(org_file, db_file)

        print(f"[{acronym}] Fetching repos from organizations...")
        get_repositories_from_organizations(acronym, org_file, HEADERS)
        create_and_populate_database(repo_from_orgs_file, db_file, search_method='organization_search')

        print(f"[{acronym}] Searching users...")
        search_users(config_file, HEADERS)
        populate_users(user_file, db_file)

        print(f"[{acronym}] Fetching repos from users...")
        get_repositories_from_users(acronym, user_file, HEADERS)
        create_and_populate_database(repo_from_users_file, db_file, search_method='user_search')

        # Ensure columns populated by skipped steps exist (SBC queries them)
        conn = sqlite3.connect(db_file)
        for col in ("organization", "contributors", "readme"):
            try:
                conn.execute(f"ALTER TABLE repositories ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.commit()
        conn.close()

        print(f"[{acronym}] Running score-based affiliation classifier...")
        compute_predictions_sbc(acronym, config_file, db_file)

        print(f"[{acronym}] Done. Run: python3 export.py --acronym {acronym} --predictor sbc")


if __name__ == "__main__":
    scrape_minimal()
