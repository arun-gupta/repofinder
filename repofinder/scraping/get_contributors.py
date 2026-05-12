#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pandas as pd
import sqlite3
import logging
from repofinder.scraping.repo_scraping_utils import (
    github_api_request, get_next_link,
    fetch_contributor_details_graphql, GRAPHQL_BATCH_SIZE
)

logger = logging.getLogger(__name__)

#TODO: Figure out how to get duplicates

def get_contributors(owner, repo_name, headers, rate_limiter=None):
    """
    Retrieves the list of contributors for a given repository.

    Args:
        owner (str): Owner of the repository.
        repo_name (str): Name of the repository.
        headers (dict): HTTP headers for the request.

    Returns:
        list: A list of contributors.
    """
    url = f"https://api.github.com/repos/{owner}/{repo_name}/contributors?q=contributions&order=desc"
    params = {'per_page': 100}
    contributors = []
    while url:
        try:
            contributors_data, headers_response = github_api_request(url, headers, params, rate_limiter=rate_limiter)
        except:
            break
        if contributors_data:
            contributors.extend(contributors_data)
            next_url = get_next_link(headers_response)
            url = next_url
            params = None
        else:
            break
    return contributors if contributors else []

def get_contributor_details(username, headers, rate_limiter=None):
    """
    Retrieves detailed information about a contributor.

    Args:
        username (str): The GitHub username of the contributor.
        headers (dict): HTTP headers for the request.
        rate_limiter : Semaphore, optional
            Thread-safe rate limiter for concurrent requests (default is None).

    Returns:
        dict or None: A dictionary containing contributor details, or None if not found (404) or error.
    """
    url = f"https://api.github.com/users/{username}"
    try:
        contributor_data, _ = github_api_request(url, headers, rate_limiter=rate_limiter)
        
        # Handle 404 or None response (user not found)
        if contributor_data is None:
            logger.debug(f"Contributor {username} not found (404) or request failed. Skipping.")
            return None
        
        return {
            "login": contributor_data.get("login"),
            "name": contributor_data.get("name"),
            "bio": contributor_data.get("bio"),
            "location": contributor_data.get("location"),
            "company": contributor_data.get("company"),
            "email": contributor_data.get("email"),
            "twitter": contributor_data.get("twitter_username"),
            "organizations": contributor_data.get("organizations_url"),  # This is a URL, requires additional fetch
        }
    except Exception as e:
        logger.debug(f"Error fetching details for user {username}: {e}")
        return None


def get_contributor_data(repo_file, db_file, headers):
    """
    Processes repositories to collect contributor data.
    Only processes repositories that are not archived, have size > 0, are not forks, and are not templates.
    
    Args:
        repo_file (str): Path to the JSON file (unused, reads from DB instead).
        db_file (str): Path to the SQLite database file.
        headers (dict): HTTP headers for authenticated GitHub API requests.
    
    Returns
    -------
    pd.DataFrame
        A DataFrame of the repositories with contributor data.
    """
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    # Read repositories from database, filtering for non-archived, size > 0, not a fork, and not a template
    query = """
        SELECT full_name, owner
        FROM repositories 
        WHERE (archived = 0 OR archived = FALSE OR archived IS NULL)
          AND (size > 0 OR size IS NULL)
          AND (fork = 0 OR fork = FALSE OR fork IS NULL)
          AND (is_template = 0 OR is_template = FALSE OR is_template IS NULL)
    """
    repo_df = pd.read_sql_query(query, conn)
    repo_df = repo_df.reset_index(drop=True)
    repo_df["contributors"] = None
    try:
        cursor.execute("ALTER TABLE repositories ADD COLUMN contributors TEXT;")  # Adjust the column type as needed
    except:
        pass
   
    # Create the contributors table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contributors (
        login TEXT PRIMARY KEY,
        name TEXT,
        bio TEXT,
        location TEXT,
        company TEXT,
        email TEXT,
        twitter TEXT,
        organizations TEXT
    )
    """)
    
    # Create the contributions table (join table)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contributions (
        repository_name TEXT NOT NULL,
        contributor_login TEXT NOT NULL,
        PRIMARY KEY (repository_name, contributor_login)
    )
    """)
    
    # List of bot usernames/patterns to skip
    bots_to_skip = ["copilot", "dependabot[bot]", "github-actions[bot]", "dependabot", "github-actions"]
    
    total_repos = len(repo_df)
    print(f"Processing {total_repos} repositories for contributor data...")

    # Pass 1 — collect contributor logins for every repo via REST (no GraphQL equivalent)
    repo_contributor_logins = {}  # full_name -> [login, ...]
    all_unique_logins = set()

    for idx, row in repo_df.iterrows():
        full_name = row["full_name"]
        owner, repo_name = full_name.split("/", 1)
        try:
            contributors = get_contributors(owner, repo_name, headers)
            logins = [
                c["login"] for c in contributors
                if not any(bot.lower() in c["login"].lower() for bot in bots_to_skip)
                and not c["login"].endswith("[bot]")
            ]
            repo_contributor_logins[full_name] = logins
            all_unique_logins.update(logins)
        except Exception as e:
            logger.error(f"Error fetching contributor list for {full_name}: {e}")
            repo_contributor_logins[full_name] = []

        processed_count = idx + 1
        if processed_count % 100 == 0 or processed_count == total_repos:
            print(f"Contributor lists: {processed_count}/{total_repos} repos done...")

    # Pass 2 — fetch contributor details in GraphQL batches
    unique_logins = list(all_unique_logins)
    print(f"Fetching details for {len(unique_logins)} unique contributors via GraphQL...")
    contributor_details_map = {}  # login -> details dict

    for batch_start in range(0, len(unique_logins), GRAPHQL_BATCH_SIZE):
        batch = unique_logins[batch_start: batch_start + GRAPHQL_BATCH_SIZE]
        details_list = fetch_contributor_details_graphql(batch, headers)
        for details in details_list:
            contributor_details_map[details["login"]] = details
        done = min(batch_start + GRAPHQL_BATCH_SIZE, len(unique_logins))
        if done % 500 == 0 or done == len(unique_logins):
            print(f"Contributor details: {done}/{len(unique_logins)} fetched...")

    # Pass 3 — write to DB
    for full_name, logins in repo_contributor_logins.items():
        valid_logins = []
        for login in logins:
            details = contributor_details_map.get(login)
            if not details:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO contributors "
                "(login, name, bio, location, company, email, twitter) "
                "VALUES (:login, :name, :bio, :location, :company, :email, :twitter)",
                details,
            )
            conn.execute(
                "INSERT OR IGNORE INTO contributions (repository_name, contributor_login) VALUES (?, ?)",
                (full_name, login),
            )
            valid_logins.append(login)

        conn.execute(
            "UPDATE repositories SET contributors = ? WHERE full_name = ?",
            (str(valid_logins), full_name),
        )

    conn.commit()
    print(f"Completed: {total_repos}/{total_repos} repositories processed")

    conn.close()
# TODO: Should I try to build a JSON object with this too?
    return repo_df

