#!/usr/bin/env python3
"""
CLI tool to find repositories where a branch is ahead of another branch.
"""

import argparse
import sys
from typing import List, Dict, Optional, Tuple
import os
import concurrent.futures
import subprocess
import json

from github_utils import (
    get_current_repository,
    parse_target,
    parse_repo_pattern,
    get_organization_repos,
    get_default_branch,
    check_branch_exists,
    compare_branches
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
    # GitHub CLI handles authentication automatically
    
    head_branch = args.head
    base_branch = args.base  # Can be None (will use default branch)
    
    # Determine target repository/organization
    used_wildcard = False
    if args.repo:
        # Use --repo/-R flag
        target_pattern, is_wildcard = parse_repo_pattern(args.repo)
        used_wildcard = is_wildcard
        if is_wildcard:
            # Organization wildcard (e.g., "commandlink/*")
            org = target_pattern
            repos = None  # Will fetch later
        else:
            # Specific repository (e.g., "owner/repo")
            org, repo = parse_target(target_pattern)
            repos = [repo] if repo else None
    elif args.target:
        # Use positional target argument
        org, repo = parse_target(args.target)
        repos = [repo] if repo else None
        # Check if target is an organization (no specific repo)
        used_wildcard = repo is None
    else:
        # Use current repository
        current_repo = get_current_repository()
        if not current_repo:
            print("Error: Could not detect current repository and no target specified.", file=sys.stderr)
            print("Use --repo owner/repo or run from a git repository with GitHub remote.", file=sys.stderr)
            sys.exit(1)
        org, repo = parse_target(current_repo)
        repos = [repo]
    
    # Fetch repositories if needed
    if repos is None:
        # Organization mode - fetch all repos
        try:
            repos = get_organization_repos(org)
        except Exception as e:
            print(f"Error fetching repositories: {e}", file=sys.stderr)
            sys.exit(1)
    
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
        
        # Check if we have multiple repositories or used a wildcard
        show_repo_column = len(found_repos) > 1 or used_wildcard
        
        # Calculate column widths
        if show_repo_column:
            repo_width = max(len(repo["repository"]) for repo in found_repos)
            repo_width = max(repo_width, 4)  # minimum width for "REPO"
            repo_width = min(repo_width, 25)  # cap at 25 chars
        else:
            repo_width = 0
        
        head_width = max(len(repo["head_branch"]) for repo in found_repos)
        head_width = max(head_width, 4)  # minimum width for "HEAD"
        head_width = min(head_width, 20)  # cap at 20 chars
        
        base_width = max(len(repo["base_branch"]) for repo in found_repos)
        base_width = max(base_width, 4)  # minimum width for "BASE"
        base_width = min(base_width, 20)  # cap at 20 chars
        
        # Print header
        if show_repo_column:
            print(f"{'REPO':<{repo_width}}  {'HEAD':<{head_width}}  {'BASE':<{base_width}}  AHEAD  BEHIND  STATUS")
        else:
            print(f"{'HEAD':<{head_width}}  {'BASE':<{base_width}}  AHEAD  BEHIND  STATUS")
        
        # Print repositories
        for repo_info in found_repos:
            head = repo_info["head_branch"][:head_width] if len(repo_info["head_branch"]) > head_width else repo_info["head_branch"]
            base = repo_info["base_branch"][:base_width] if len(repo_info["base_branch"]) > base_width else repo_info["base_branch"]
            ahead = repo_info["ahead_by"]
            behind = repo_info["behind_by"] if repo_info["behind_by"] > 0 else "-"
            status = repo_info["status"] if repo_info["behind_by"] > 0 else ""
            
            if show_repo_column:
                repo = repo_info["repository"][:repo_width] if len(repo_info["repository"]) > repo_width else repo_info["repository"]
                print(f"{repo:<{repo_width}}  {head:<{head_width}}  {base:<{base_width}}  {ahead:<5}  {behind:<6}  {status}")
            else:
                print(f"{head:<{head_width}}  {base:<{base_width}}  {ahead:<5}  {behind:<6}  {status}")
    else:
        print("No repositories found where branch is ahead")


if __name__ == "__main__":
    main()