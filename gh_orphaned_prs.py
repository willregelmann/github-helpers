#!/usr/bin/env python3
"""
CLI tool to find merged PRs that contain commits not present in the target branch.
"""

import argparse
import subprocess
import json
import sys
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import requests
from urllib.parse import urlparse
import concurrent.futures
import time
import os


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Find merged PRs with commits not in the target branch",
        prog="gh-orphaned-prs"
    )
    parser.add_argument(
        "target",
        help="Organization or organization/repository"
    )
    parser.add_argument(
        "branch",
        nargs="?",
        help="Target branch name (if omitted, checks all PRs)"
    )
    parser.add_argument(
        "--start-date",
        type=str,
        help="Start date for PR merge window (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end-date",
        type=str,
        help="End date for PR merge window (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--token",
        help="GitHub personal access token (or set GITHUB_TOKEN env var)"
    )
    parser.add_argument(
        "--reopen",
        action="store_true",
        help="Recreate orphaned PRs with the same source/target branches"
    )
    return parser.parse_args()


def parse_target(target: str) -> Tuple[str, Optional[str]]:
    """Parse target to extract organization and optional repository."""
    if "/" in target:
        parts = target.split("/", 1)
        return parts[0], parts[1]
    return target, None


def get_github_token(token_arg: Optional[str]) -> Optional[str]:
    """Get GitHub token from argument, environment, or gh CLI."""
    # First try the provided argument
    if token_arg:
        return token_arg
    
    # Then try environment variable
    env_token = os.environ.get("GITHUB_TOKEN")
    if env_token:
        return env_token
    
    # Finally, try to get token from gh CLI
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    
    return None


def get_organization_repos(org: str, token: Optional[str]) -> List[str]:
    """Get all repositories for an organization that the authenticated user can access."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    
    repos = []
    
    if token:
        # Use the authenticated user repos endpoint to get all repos with org membership
        page = 1
        per_page = 100
        
        while True:
            url = "https://api.github.com/user/repos"
            params = {
                "per_page": per_page,
                "page": page,
                "affiliation": "organization_member,collaborator,owner",
                "sort": "updated",
                "direction": "desc"
            }
            
            response = requests.get(url, headers=headers, params=params)
            
            if response.status_code != 200:
                break
            
            page_repos = response.json()
            if not page_repos:
                break
            
            # Filter repos that belong to the specified organization
            org_repos = [repo for repo in page_repos 
                        if repo.get("owner", {}).get("login") == org]
            repos.extend([repo["name"] for repo in org_repos])
            
            page += 1
            
            # Safety limit
            if page > 100:
                break
    else:
        # Fall back to public org repos if no token
        print("No token provided, falling back to public org repos...")
        page = 1
        per_page = 100
        
        while True:
            url = f"https://api.github.com/orgs/{org}/repos"
            params = {
                "per_page": per_page,
                "page": page,
                "type": "public"
            }
            
            response = requests.get(url, headers=headers, params=params)
            if response.status_code != 200:
                break
            
            page_repos = response.json()
            if not page_repos:
                break
            
            repos.extend([repo["name"] for repo in page_repos])
            page += 1
            
            if page > 100:
                break
    
    return repos


def get_default_branch(org: str, repo: str, token: Optional[str]) -> Optional[str]:
    """Get the default branch for a repository."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    
    url = f"https://api.github.com/repos/{org}/{repo}"
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        return response.json().get("default_branch")
    return None


def fetch_merged_prs(owner: str, repo: str, token: Optional[str], 
                    start_date: Optional[str], end_date: Optional[str],
                    branch: Optional[str]) -> List[Dict]:
    """Fetch merged PRs using GitHub Search API for better performance."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    
    # Build search query - much faster than paginating through all PRs
    query_parts = [f"repo:{owner}/{repo}", "is:pr", "is:merged"]
    if start_date:
        query_parts.append(f"merged:>={start_date}")
    if end_date:
        query_parts.append(f"merged:<={end_date}")
    if branch:
        query_parts.append(f"base:{branch}")
    
    query = " ".join(query_parts)
    
    merged_prs = []
    page = 1
    per_page = 100
    
    while True:
        url = "https://api.github.com/search/issues"
        params = {
            "q": query,
            "sort": "updated",
            "order": "desc",
            "per_page": per_page,
            "page": page
        }
        
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        
        data = response.json()
        items = data.get("items", [])
        
        if not items:
            break
        
        # Convert search results to PR format with concurrent requests
        pr_urls = [item["pull_request"]["url"] for item in items]
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_url = {
                executor.submit(requests.get, url, headers=headers): url 
                for url in pr_urls
            }
            
            for future in concurrent.futures.as_completed(future_to_url):
                try:
                    response = future.result()
                    if response.status_code == 200:
                        merged_prs.append(response.json())
                except Exception:
                    continue
        
        page += 1
        
        # GitHub search API has a 1000 result limit (10 pages of 100)
        if page > 10 or len(items) < per_page:
            break
    
    return merged_prs


def get_pr_commits(owner: str, repo: str, pr_number: int, token: Optional[str]) -> List[str]:
    """Get all commit SHAs from a PR."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/commits"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    
    commits = response.json()
    return [commit["sha"] for commit in commits]


def is_commit_in_branch(owner: str, repo: str, commit_sha: str, branch: str, token: Optional[str]) -> bool:
    """Check if a commit exists in the specified branch using GitHub API."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    
    try:
        # Use the compare API to check if the commit is reachable from the branch
        url = f"https://api.github.com/repos/{owner}/{repo}/compare/{commit_sha}...{branch}"
        response = requests.get(url, headers=headers)
        
        # Handle rate limiting
        if response.status_code == 429:
            reset_time = int(response.headers.get('X-RateLimit-Reset', time.time() + 60))
            sleep_time = max(1, reset_time - int(time.time()))
            time.sleep(sleep_time)
            response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            comparison = response.json()
            ahead_by = comparison.get("ahead_by", 1)
            status = comparison.get("status", "unknown")
            return status in ["identical", "ahead"]
        elif response.status_code == 404:
            return False
        else:
            return False
    except requests.exceptions.RequestException:
        return False


def check_pr_commits_concurrent(owner: str, repo: str, pr_data: Dict, branch: str, token: Optional[str]) -> Optional[Dict]:
    """Check commits for a single PR and return orphaned PR data if any commits are missing."""
    pr_number = pr_data["number"]
    pr_title = pr_data["title"]
    
    try:
        commits = get_pr_commits(owner, repo, pr_number, token)
    except requests.exceptions.RequestException:
        return None
    
    # Check commits concurrently
    missing_commits = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        # Submit all commit checks
        future_to_commit = {
            executor.submit(is_commit_in_branch, owner, repo, commit_sha, branch, token): commit_sha
            for commit_sha in commits
        }
        
        # Collect results
        for future in concurrent.futures.as_completed(future_to_commit):
            commit_sha = future_to_commit[future]
            try:
                in_branch = future.result()
                if not in_branch:
                    missing_commits.append(commit_sha)
            except Exception:
                # If commit check fails, assume it's missing
                missing_commits.append(commit_sha)
    
    if missing_commits:
        return {
            "number": pr_number,
            "title": pr_title,
            "url": pr_data["html_url"],
            "merged_at": pr_data["merged_at"],
            "source_branch": pr_data.get("head", {}).get("ref", "unknown"),
            "target_branch": pr_data.get("base", {}).get("ref", "unknown"),
            "repository": f"{owner}/{repo}"
        }
    
    return None


def check_repository_orphaned_prs(owner: str, repo: str, branch: Optional[str], 
                                  start_date: Optional[str], end_date: Optional[str],
                                  token: Optional[str]) -> List[Dict]:
    """Check a single repository for orphaned PRs."""
    # If no branch specified, use default branch
    if branch is None:
        branch = get_default_branch(owner, repo, token)
        if branch is None:
            print(f"  Could not determine default branch for {owner}/{repo}")
            return []
    
    try:
        merged_prs = fetch_merged_prs(owner, repo, token, start_date, end_date, branch)
    except requests.exceptions.RequestException as e:
        print(f"  Error fetching PRs for {owner}/{repo}: {e}")
        return []
    
    if not merged_prs:
        return []
    
    # Check each PR for orphaned commits
    orphaned_prs = []
    
    # Process PRs concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        # Submit all PR checks
        future_to_pr = {
            executor.submit(check_pr_commits_concurrent, owner, repo, pr, branch, token): pr
            for pr in merged_prs
        }
        
        # Collect results as they complete
        for future in concurrent.futures.as_completed(future_to_pr):
            try:
                result = future.result()
                if result:
                    orphaned_prs.append(result)
            except Exception:
                continue
    
    return orphaned_prs


def recreate_pr(owner: str, repo: str, pr_data: Dict, target_branch: str) -> Dict[str, str]:
    """Recreate a PR using gh CLI."""
    source_branch = pr_data["source_branch"]
    title = f"{pr_data['title']} (reopened)"
    pr_number = pr_data["number"]
    
    try:
        # First check if the source branch exists
        check_cmd = ["gh", "api", f"repos/{owner}/{repo}/branches/{source_branch}", "--silent"]
        check_result = subprocess.run(check_cmd, capture_output=True)
        
        if check_result.returncode != 0:
            return {
                "status": "failed",
                "original_pr": pr_number,
                "source_branch": source_branch,
                "error": f"Source branch '{source_branch}' not found. It may have been deleted."
            }
        
        # Create the PR using gh CLI
        cmd = [
            "gh", "pr", "create",
            "--repo", f"{owner}/{repo}",
            "--base", target_branch,
            "--head", source_branch,
            "--title", title,
            "--body", f"Reopened from PR #{pr_number}"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            # Extract PR URL from output
            pr_url = result.stdout.strip()
            return {
                "status": "success",
                "original_pr": pr_number,
                "new_pr_url": pr_url,
                "source_branch": source_branch
            }
        else:
            error_msg = result.stderr.strip() or "Unknown error"
            # Check for common error patterns
            if "No commits between" in error_msg:
                error_msg = "No new commits between source and target branches."
            elif "Head ref must be a branch" in error_msg:
                error_msg = f"Source branch '{source_branch}' is not a valid branch reference."
            return {
                "status": "failed",
                "original_pr": pr_number,
                "source_branch": source_branch,
                "error": error_msg
            }
    except Exception as e:
        return {
            "status": "failed",
            "original_pr": pr_number,
            "source_branch": source_branch,
            "error": str(e)
        }


def main():
    args = parse_arguments()
    token = get_github_token(args.token)
    
    org, repo = parse_target(args.target)
    
    if repo:
        # Single repository
        repos = [repo]
        print(f"Checking repository {org}/{repo}...")
    else:
        # All repositories in organization
        print(f"Fetching repositories for organization {org}...")
        try:
            repos = get_organization_repos(org, token)
        except requests.exceptions.RequestException as e:
            print(f"Error fetching repositories: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Found {len(repos)} repositories")
    
    if args.start_date or args.end_date:
        print(f"Date range: {args.start_date or 'any'} to {args.end_date or 'any'}")
    
    all_orphaned_prs = []
    
    for repo_name in repos:
        if len(repos) > 1:
            print(f"\nChecking {org}/{repo_name}...")
        
        orphaned_prs = check_repository_orphaned_prs(
            org, repo_name, args.branch, args.start_date, args.end_date, token
        )
        
        if orphaned_prs:
            all_orphaned_prs.extend(orphaned_prs)
            if len(repos) > 1:
                print(f"  Found {len(orphaned_prs)} orphaned PRs")
    
    # Display results
    print(f"\n{'='*60}")
    print(f"RESULTS: Found {len(all_orphaned_prs)} orphaned PRs")
    print(f"{'='*60}\n")
    
    if all_orphaned_prs:
        # Sort by merge date (newest first)
        all_orphaned_prs.sort(key=lambda x: x['merged_at'], reverse=True)
        
        for pr in all_orphaned_prs:
            merged_date = pr['merged_at'].split('T')[0]  # Just the date part
            print(f"PR #{pr['number']}: {pr['title']}")
            print(f"  Repository: {pr['repository']}")
            print(f"  Source branch: {pr['source_branch']}")
            print(f"  Target branch: {pr['target_branch']}")
            print(f"  Merged: {merged_date}")
            print(f"  URL: {pr['url']}")
            print()
        
        # Handle --reopen option
        if args.reopen:
            print(f"\n{'='*60}")
            print("REOPENING ORPHANED PRs")
            print(f"{'='*60}\n")
            
            successful_reopens = []
            failed_reopens = []
            
            for pr in all_orphaned_prs:
                repo_parts = pr['repository'].split('/')
                owner_name, repo_name = repo_parts[0], repo_parts[1]
                
                print(f"Reopening PR #{pr['number']} in {pr['repository']} from branch '{pr['source_branch']}'...")
                result = recreate_pr(owner_name, repo_name, pr, pr['target_branch'])
                
                if result["status"] == "success":
                    successful_reopens.append(result)
                    print(f"  ✓ Success: {result['new_pr_url']}")
                else:
                    failed_reopens.append(result)
                    print(f"  ✗ Failed: {result['error']}")
            
            # Summary
            print(f"\n{'='*60}")
            print("REOPEN SUMMARY")
            print(f"{'='*60}")
            print(f"Successfully reopened: {len(successful_reopens)}")
            print(f"Failed to reopen: {len(failed_reopens)}")
            
            if successful_reopens:
                print("\nSuccessfully reopened PRs:")
                for result in successful_reopens:
                    print(f"  - PR #{result['original_pr']} → {result['new_pr_url']}")
            
            if failed_reopens:
                print("\nFailed to reopen PRs:")
                for result in failed_reopens:
                    print(f"  - PR #{result['original_pr']} ({result['source_branch']}): {result['error']}")
    else:
        branch_desc = args.branch if args.branch else "default branches"
        print(f"All merged PR commits are present in the {branch_desc}.")


if __name__ == "__main__":
    main()