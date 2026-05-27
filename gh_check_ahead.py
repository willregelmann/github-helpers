#!/usr/bin/env python3
"""
CLI tool to find repositories where a branch is ahead of another branch.
"""

import argparse
from typing import Dict, Optional
import concurrent.futures

from github_utils import (
    ensure_gh_available,
    resolve_targets,
    get_default_branch,
    check_branch_exists,
    compare_branches,
    format_table,
)


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Find repositories where a branch is ahead of another branch",
        prog="gh-check-ahead"
    )
    parser.add_argument(
        "-H", "--head",
        required=True,
        help="Source branch to check (the branch that might be ahead)"
    )
    parser.add_argument(
        "-B", "--base",
        help="Target branch to compare against (defaults to repository's default branch)"
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="Organization or organization/repository (defaults to current repository)"
    )
    parser.add_argument(
        "-R", "--repo",
        help="Repository to check (owner/repo format, or owner/* for all repos in org)"
    )
    return parser.parse_args()


def check_repo_branches(org: str, repo: str, head_branch: str, base_branch: Optional[str]) -> Optional[Dict]:
    """Check a single repo for branch comparison."""
    # If base_branch is None, get the default branch
    if base_branch is None:
        base_branch = get_default_branch(org, repo)
        if base_branch is None:
            return None
    
    # Check if both branches exist
    head_exists = check_branch_exists(org, repo, head_branch)
    if not head_exists:
        return None
        
    base_exists = check_branch_exists(org, repo, base_branch)
    if not base_exists:
        return None
    
    # Compare branches (head vs base)
    comparison = compare_branches(org, repo, base_branch, head_branch)
    
    if comparison:
        ahead_by = comparison.get("ahead_by", 0)
        behind_by = comparison.get("behind_by", 0)
        status = comparison.get("status", "unknown")
        
        # Only return if head branch is ahead
        if ahead_by > 0:
            return {
                "repository": repo,
                "head_branch": head_branch,
                "base_branch": base_branch,
                "ahead_by": ahead_by,
                "behind_by": behind_by,
                "status": status
            }
    
    return None


def main():
    args = parse_arguments()
    ensure_gh_available()

    head_branch = args.head
    base_branch = args.base  # Can be None (will use default branch)

    # Determine target repository/organization
    org, repos, used_wildcard = resolve_targets(args.repo, args.target)

    found_repos = []
    
    # Process repos concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        # Submit all repo checks
        future_to_repo = {
            executor.submit(check_repo_branches, org, repo_name, head_branch, base_branch): repo_name
            for repo_name in repos
        }
        
        # Collect results as they complete
        for future in concurrent.futures.as_completed(future_to_repo):
            try:
                result = future.result()
                if result:
                    found_repos.append(result)
            except Exception:
                continue
    
    # Display results
    if found_repos:
        print(f"Showing {len(found_repos)} repositories where {head_branch} is ahead")
        print()

        # Sort by repository name
        found_repos.sort(key=lambda x: x["repository"])

        # Show the repo column when spanning multiple repos or a whole org.
        show_repo_column = len(found_repos) > 1 or used_wildcard

        headers = ["REPO", "HEAD", "BASE", "AHEAD", "BEHIND", "STATUS"]
        maxs = [25, 20, 20, None, None, None]
        rows = []
        for repo_info in found_repos:
            has_behind = repo_info["behind_by"] > 0
            rows.append([
                repo_info["repository"],
                repo_info["head_branch"],
                repo_info["base_branch"],
                repo_info["ahead_by"],
                repo_info["behind_by"] if has_behind else "-",
                repo_info["status"] if has_behind else "",
            ])

        if not show_repo_column:
            headers = headers[1:]
            maxs = maxs[1:]
            rows = [row[1:] for row in rows]

        print("\n".join(format_table(headers, rows, maxs=maxs)))
    else:
        print("No repositories found where branch is ahead")


if __name__ == "__main__":
    main()