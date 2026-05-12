#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pandas as pd
import sqlite3
import base64
from repofinder.scraping.repo_scraping_utils import (
    github_api_request, fetch_repo_extras_graphql, GRAPHQL_BATCH_SIZE
)


def get_feature_content(full_name, headers, feature):
    """
    Tries to fetch the content for a given feature (like README, license, etc.)
    from either the GitHub API or from fallback paths in the repo contents.

    Parameters
    ----------
    full_name : str
        The repository full name, e.g., "owner/repo".
    feature : str
        The feature to retrieve (e.g., "readme", "contributing").
    headers : dict
        Headers with auth and API options.

    Returns
    -------
    str or None
        Content string if available, otherwise None.
    """

    base_url = f"https://api.github.com/repos/{full_name}"
    # Direct API endpoints (with special treatment)
    
    if feature == "readme":
        try:
            res, _ = github_api_request(f"{base_url}/readme", headers)
            return base64.b64decode(res['content']).decode('utf-8')
        except Exception as e:
            print(f"Failed to decode README for {full_name}: {e}")
            return None

    
    if feature == "license":
        try:
            repo, _ = github_api_request(base_url, headers)
            return repo.get("license", {}).get("key")
        except Exception as e:
            print(f"Failed to fetch license for {full_name}: {e}")
            return None
 
    if feature == "subscribers_count": # These are the watchers
        try:
            repo_data, _ = github_api_request(f"{base_url}", headers)
            return repo_data.get("subscribers_count", 0)
        except Exception as e:
            print(f"Failed to fetch subscribers count for {full_name}: {e}")
            return None

    if feature == "release_downloads":
        try:
            releases, _ = github_api_request(f"{base_url}/releases", headers)
            total_downloads = 0
            for release in releases:
                for asset in release.get("assets", []):
                    total_downloads += asset.get("download_count", 0)
            return total_downloads
        except Exception as e:
            print(f"Failed to fetch release downloads for {full_name}: {e}")
            return None
    
    
    feature_api_endpoints = {
        "code_of_conduct": f"{base_url}/community/code_of_conduct",
        "security_policy": f"{base_url}/security/policy"
    }

    # Community files endpoints
    
    community_url = f"{base_url}/community/profile"
    community = ["code_of_conduct_file", "contributing", "issue_template", "pull_request_template"]


    if feature in community:
        try:
            res, _ = github_api_request(community_url, headers)

            file_info = res['files'].get(feature)
            if not file_info:
                return None

            res, _ = github_api_request(file_info["url"], headers)
            return base64.b64decode(res["content"]).decode("utf-8")
            
        except Exception as e:
            print(e)
            
    if feature == "security_policy":

        fallback_paths = [
            ".github/SECURITY.md",
            "SECURITY.md",
            "docs/SECURITY.md",
        ]

        for path in fallback_paths:
            try:
                res, status = github_api_request(f"{base_url}/contents/{path}", headers)
                if res is None or status == 404:
                    continue
                return base64.b64decode(res["content"]).decode("utf-8")

            except Exception as e:
                print(e)
                return None

def get_features_data(repo_file, db_file, headers, features_list):
    """
    Iterates over repositories and stores specified features in the database.
    Only processes repositories that are not archived, have size > 0, are not forks, and are not templates.

    Parameters
    ----------
    repo_file : str
        Path to the JSON file containing the list of repositories (unused, reads from DB instead).
    db_file : str
        Path to the SQLite database.
    headers : dict
        HTTP headers for GitHub API.
    features_list : list
        List of features to retrieve and store (e.g., ['readme', 'license']).
    """
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    # Check if features_list is empty
    if not features_list:
        print("No features to process. Exiting.")
        conn.close()
        return
    
    # Add columns if they don't exist (must be done before querying)
    for feature in features_list:
        try:
            if feature == "subscribers_count" or feature== "release_downloads":
                cursor.execute(f"ALTER TABLE repositories ADD COLUMN {feature} INTEGER;")
            else:
                cursor.execute(f"ALTER TABLE repositories ADD COLUMN {feature} TEXT;")
        except sqlite3.OperationalError:
            pass
    
    # Build query to check which features are missing for each repository
    # We'll filter for repos where at least one feature is missing
    feature_conditions = " OR ".join([f"({feature} IS NULL OR {feature} = '')" for feature in features_list])
    
    # Read repositories from database, filtering for non-archived, size > 0, not a fork, not a template
    # and where at least one feature is missing
    query = f"""
        SELECT full_name, {', '.join(features_list)}
        FROM repositories 
        WHERE (archived = 0 OR archived = FALSE OR archived IS NULL)
          AND (size > 0 OR size IS NULL)
          AND (fork = 0 OR fork = FALSE OR fork IS NULL)
          AND (is_template = 0 OR is_template = FALSE OR is_template IS NULL)
          AND ({feature_conditions})
    """
    repo_df = pd.read_sql_query(query, conn)

    total = len(repo_df)
    print(f"Processing {total} repositories for features: {', '.join(features_list)}")
    
    if total == 0:
        print("No repositories found matching the criteria (non-archived and size > 0).")
        conn.close()
        return
    
    # Separate features into GraphQL-batchable and REST-only
    gql_features = {"readme", "release_downloads"}
    gql_requested = [f for f in features_list if f in gql_features]
    rest_only_features = [f for f in features_list if f not in gql_features]

    # --- GraphQL batch pass for readme + release_downloads ---
    if gql_requested:
        repo_tuples = []
        for _, row in repo_df.iterrows():
            needs_fetch = any(
                f in gql_requested and (pd.isna(row.get(f)) or str(row.get(f, "")).strip() == "")
                for f in gql_requested
            )
            if needs_fetch:
                owner, name = row["full_name"].split("/", 1)
                repo_tuples.append((owner, name))

        print(f"Fetching readme/release_downloads via GraphQL for {len(repo_tuples)} repos...")
        needs_rest_readme = []

        for batch_start in range(0, len(repo_tuples), GRAPHQL_BATCH_SIZE):
            batch = repo_tuples[batch_start: batch_start + GRAPHQL_BATCH_SIZE]
            batch_results = fetch_repo_extras_graphql(batch, headers)

            for full_name, extras in batch_results.items():
                updates = {}
                if "readme" in gql_requested and extras["readme"]:
                    updates["readme"] = extras["readme"]
                elif "readme" in gql_requested and extras["needs_rest_readme"]:
                    needs_rest_readme.append(full_name)
                if "release_downloads" in gql_requested:
                    updates["release_downloads"] = extras["release_downloads"]
                if updates:
                    set_clause = ", ".join(f"{k} = ?" for k in updates)
                    cursor.execute(
                        f"UPDATE repositories SET {set_clause} WHERE full_name = ?",
                        (*updates.values(), full_name),
                    )

            conn.commit()
            done = min(batch_start + GRAPHQL_BATCH_SIZE, len(repo_tuples))
            print(f"GraphQL extras: {done}/{len(repo_tuples)} repos processed...")

        # REST fallback for repos where GraphQL returned no README (case-sensitivity misses)
        if needs_rest_readme and "readme" in gql_requested:
            print(f"REST fallback for {len(needs_rest_readme)} repos with missing README...")
            for full_name in needs_rest_readme:
                result = get_feature_content(full_name, headers, "readme")
                cursor.execute(
                    "UPDATE repositories SET readme = ? WHERE full_name = ?",
                    (str(result) if result is not None else None, full_name),
                )
            conn.commit()

    # --- REST pass for remaining features (community files, security policy, etc.) ---
    if rest_only_features:
        rest_conditions = " OR ".join(
            [f"({f} IS NULL OR {f} = '')" for f in rest_only_features]
        )
        rest_query = f"""
            SELECT full_name, {', '.join(rest_only_features)}
            FROM repositories
            WHERE (archived = 0 OR archived = FALSE OR archived IS NULL)
              AND (size > 0 OR size IS NULL)
              AND (fork = 0 OR fork = FALSE OR fork IS NULL)
              AND (is_template = 0 OR is_template = FALSE OR is_template IS NULL)
              AND ({rest_conditions})
        """
        rest_df = pd.read_sql_query(rest_query, conn)
        print(f"Fetching {rest_only_features} via REST for {len(rest_df)} repos...")

        for i, row in rest_df.iterrows():
            full_name = row["full_name"]
            updates = {}
            for feature in rest_only_features:
                existing = row.get(feature)
                if pd.isna(existing) or str(existing).strip() == "":
                    result = get_feature_content(full_name, headers, feature)
                    updates[feature] = str(result) if result is not None else None
            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                cursor.execute(
                    f"UPDATE repositories SET {set_clause} WHERE full_name = ?",
                    (*updates.values(), full_name),
                )
            if (i + 1) % 100 == 0 or (i + 1) == len(rest_df):
                conn.commit()
                print(f"REST extras: {i+1}/{len(rest_df)} repos processed...")

    print(f"\nCompleted: Processed {total}/{total} repositories")

    conn.close()

