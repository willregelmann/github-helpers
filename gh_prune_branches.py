#!/usr/bin/env python3
"""
CLI tool to delete branches from GitHub that have no commits ahead of the default branch.
"""

import argparse
import sys
from typing import List, Dict, Optional, Tuple
import os
import concurrent.futures
import subprocess
import json
import re

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
        description="Delete branches that have no commits ahead of the default branch",
        prog="gh-prune-branches"
    )
    parser.add_argument(
        "-R", "--repo",
        help="Repository to check (owner/repo format, or owner/* for all repos in org)"
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Only report branches that would be deleted without actually deleting them"
    )
    parser.add_argument(
        "--filter",
        help="Regex pattern to filter branch names (only branches matching the pattern will be considered)"
    )
    return parser.parse_args()


def get_all_branches(org: str, repo: str) -> List[str]:
    """Get all branches for a repository using gh CLI."""
    cmd = ["gh", "api", f"repos/{org}/{repo}/branches", "--paginate", "--jq", ".[].name"]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return []
        
        branches = result.stdout.strip().split('\n')
        return [b for b in branches if b]  # Filter out empty strings
        
    except subprocess.SubprocessError:
        return []


def delete_branch(org: str, repo: str, branch: str) -> Tuple[bool, str]:
    """Delete a branch using gh CLI."""
    cmd = ["gh", "api", f"repos/{org}/{repo}/git/refs/heads/{branch}", "--method", "DELETE"]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return True, "deleted"
        else:
            error_msg = result.stderr.strip() or "Unknown error"
            return False, error_msg
    except subprocess.SubprocessError as e:
        return False, str(e)


def check_branch_prunable(org: str, repo: str, branch: str, default_branch: str) -> Optional[Dict]:
    """Check if a branch can be pruned (has no commits ahead of default branch)."""
    # Skip the default branch itself
    if branch == default_branch:
        return None
    
    # Compare branch with default branch
    comparison = compare_branches(org, repo, default_branch, branch)
    
    if comparison:
        ahead_by = comparison.get("ahead_by", 0)
        behind_by = comparison.get("behind_by", 0)
        
        # Only prune if branch has no commits ahead
        if ahead_by == 0:
            return {
                "branch": branch,
                "behind_by": behind_by,
                "can_prune": True
            }
    
    return None


def check_repository_branches(org: str, repo: str, report_only: bool, filter_pattern: Optional[str] = None) -> List[Dict]:
    """Check all branches in a repository and optionally delete prunable ones."""
    # Get default branch
    default_branch = get_default_branch(org, repo)
    if not default_branch:
        print(f"  Error: Could not get default branch for {org}/{repo}")
        return []
    
    # Get all branches
    branches = get_all_branches(org, repo)
    if not branches:
        return []
    
    # Apply filter if provided
    if filter_pattern:
        try:
            pattern = re.compile(filter_pattern)
            branches = [branch for branch in branches if pattern.search(branch)]
        except re.error as e:
            print(f"  Error: Invalid regex pattern '{filter_pattern}': {e}")
            return []
    
    prunable_branches = []
    
    # Check each branch concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        # Submit all branch checks
        future_to_branch = {
            executor.submit(check_branch_prunable, org, repo, branch, default_branch): branch
            for branch in branches
        }
        
        # Collect results as they complete
        for future in concurrent.futures.as_completed(future_to_branch):
            try:
                result = future.result()
                if result:
                    result["repository"] = f"{org}/{repo}"
                    result["default_branch"] = default_branch
                    prunable_branches.append(result)
            except Exception:
                continue
    
    # If not report-only, delete the prunable branches
    if not report_only and prunable_branches:
        for branch_info in prunable_branches:
            success, message = delete_branch(org, repo, branch_info["branch"])
            branch_info["deletion_status"] = "success" if success else "failed"
            branch_info["deletion_message"] = message
    
    return prunable_branches


def main():
    args = parse_arguments()
    # GitHub CLI handles authentication automatically
    
    # Determine target repository/organization
    if args.repo:
        # Use --repo/-R flag
        target_pattern, is_wildcard = parse_repo_pattern(args.repo)
        if is_wildcard:
            # Organization wildcard (e.g., "commandlink/*")
            org = target_pattern
            repos = None  # Will fetch later
        else:
            # Specific repository (e.g., "owner/repo")
            org, repo = parse_target(target_pattern)
            repos = [repo] if repo else None
    else:
        # Use current repository
        current_repo = get_current_repository()
        if not current_repo:
            print("Error: Could not detect current repository.", file=sys.stderr)
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
    
    all_prunable_branches = []
    
    # Process repositories
    for repo_name in repos:
        prunable_branches = check_repository_branches(org, repo_name, args.report, args.filter)
        if prunable_branches:
            all_prunable_branches.extend(prunable_branches)
    
    # Display results
    if all_prunable_branches:
        if args.report:
            print(f"\nShowing {len(all_prunable_branches)} branches that can be pruned")
        else:
            print(f"\nProcessed {len(all_prunable_branches)} branches")
        print()
        
        # Sort by repository and branch name
        all_prunable_branches.sort(key=lambda x: (x["repository"], x["branch"]))
        
        # Check if we have multiple repositories
        unique_repos = set(branch['repository'] for branch in all_prunable_branches)
        show_repo_column = len(unique_repos) > 1
        
        # Calculate column widths
        if show_repo_column:
            repo_width = max(len(branch["repository"]) for branch in all_prunable_branches)
            repo_width = max(repo_width, 4)  # minimum width for "REPO"
            repo_width = min(repo_width, 30)  # cap at 30 chars
        else:
            repo_width = 0
        
        branch_width = max(len(branch["branch"]) for branch in all_prunable_branches)
        branch_width = max(branch_width, 6)  # minimum width for "BRANCH"
        branch_width = min(branch_width, 30)  # cap at 30 chars
        
        default_width = max(len(branch["default_branch"]) for branch in all_prunable_branches)
        default_width = max(default_width, 7)  # minimum width for "DEFAULT"
        default_width = min(default_width, 20)  # cap at 20 chars
        
        # Print header
        if args.report:
            if show_repo_column:
                print(f"{'REPO':<{repo_width}}  {'BRANCH':<{branch_width}}  {'DEFAULT':<{default_width}}  BEHIND")
            else:
                print(f"{'BRANCH':<{branch_width}}  {'DEFAULT':<{default_width}}  BEHIND")
        else:
            if show_repo_column:
                print(f"{'REPO':<{repo_width}}  {'BRANCH':<{branch_width}}  {'DEFAULT':<{default_width}}  BEHIND  STATUS")
            else:
                print(f"{'BRANCH':<{branch_width}}  {'DEFAULT':<{default_width}}  BEHIND  STATUS")
        
        # Print branches
        for branch_info in all_prunable_branches:
            branch = branch_info["branch"][:branch_width] if len(branch_info["branch"]) > branch_width else branch_info["branch"]
            default = branch_info["default_branch"][:default_width] if len(branch_info["default_branch"]) > default_width else branch_info["default_branch"]
            behind = branch_info["behind_by"] if branch_info["behind_by"] > 0 else "-"
            
            if args.report:
                if show_repo_column:
                    repo = branch_info["repository"][:repo_width] if len(branch_info["repository"]) > repo_width else branch_info["repository"]
                    print(f"{repo:<{repo_width}}  {branch:<{branch_width}}  {default:<{default_width}}  {behind}")
                else:
                    print(f"{branch:<{branch_width}}  {default:<{default_width}}  {behind}")
            else:
                status = branch_info.get("deletion_status", "pending")
                if status == "failed":
                    status = f"failed ({branch_info.get('deletion_message', 'unknown error')})"
                
                if show_repo_column:
                    repo = branch_info["repository"][:repo_width] if len(branch_info["repository"]) > repo_width else branch_info["repository"]
                    print(f"{repo:<{repo_width}}  {branch:<{branch_width}}  {default:<{default_width}}  {behind:<6}  {status}")
                else:
                    print(f"{branch:<{branch_width}}  {default:<{default_width}}  {behind:<6}  {status}")
        
        if not args.report:
            # Summary of deletions
            successful = sum(1 for b in all_prunable_branches if b.get("deletion_status") == "success")
            failed = sum(1 for b in all_prunable_branches if b.get("deletion_status") == "failed")
            print(f"\nDeleted {successful} branches")
            if failed:
                print(f"Failed to delete {failed} branches")
    else:
        print("\nNo prunable branches found")


if __name__ == "__main__":
    main()