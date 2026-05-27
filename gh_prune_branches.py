#!/usr/bin/env python3
"""
CLI tool to delete branches from GitHub that have no commits ahead of the default branch.
"""

import argparse
from typing import List, Dict, Optional, Tuple
import concurrent.futures
import subprocess
import re

from github_utils import (
    ensure_gh_available,
    resolve_targets,
    get_default_branch,
    compare_branches,
    format_table,
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
        "-y", "--yes",
        action="store_true",
        help="Skip the confirmation prompt and delete without prompting"
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


def check_repository_branches(org: str, repo: str, filter_pattern: Optional[str] = None) -> List[Dict]:
    """Return the branches in a repository that can be pruned."""
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

    return prunable_branches


def print_branch_table(branches: List[Dict], show_repo_column: bool, include_status: bool):
    """Print a table of prunable branches, optionally including a deletion STATUS column."""
    headers = ["REPO", "BRANCH", "DEFAULT", "BEHIND"]
    maxs = [30, 30, 20, None]
    if include_status:
        headers.append("STATUS")
        maxs.append(None)

    rows = []
    for branch_info in branches:
        behind = branch_info["behind_by"] if branch_info["behind_by"] > 0 else "-"
        row = [branch_info["repository"], branch_info["branch"], branch_info["default_branch"], behind]
        if include_status:
            status = branch_info.get("deletion_status", "pending")
            if status == "failed":
                status = f"failed ({branch_info.get('deletion_message', 'unknown error')})"
            row.append(status)
        rows.append(row)

    if not show_repo_column:
        headers = headers[1:]
        maxs = maxs[1:]
        rows = [row[1:] for row in rows]

    print("\n".join(format_table(headers, rows, maxs=maxs)))


def main():
    args = parse_arguments()
    ensure_gh_available()

    # Determine target repository/organization
    org, repos, _ = resolve_targets(args.repo)

    all_prunable_branches = []
    for repo_name in repos:
        all_prunable_branches.extend(check_repository_branches(org, repo_name, args.filter))

    if not all_prunable_branches:
        print("\nNo prunable branches found")
        return

    # Sort by repository and branch name
    all_prunable_branches.sort(key=lambda x: (x["repository"], x["branch"]))

    # Show the repo column only when spanning multiple repositories.
    unique_repos = set(branch['repository'] for branch in all_prunable_branches)
    show_repo_column = len(unique_repos) > 1

    # Always show what can be pruned before doing anything destructive.
    print(f"\nShowing {len(all_prunable_branches)} branches that can be pruned")
    print()
    print_branch_table(all_prunable_branches, show_repo_column, include_status=False)

    if args.report:
        return

    # Confirm before deleting unless explicitly skipped.
    if not args.yes:
        answer = input(f"\nDelete these {len(all_prunable_branches)} branches? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Aborted. No branches deleted.")
            return

    # Delete and report results.
    for branch_info in all_prunable_branches:
        success, message = delete_branch(
            *branch_info["repository"].split("/", 1), branch_info["branch"]
        )
        branch_info["deletion_status"] = "success" if success else "failed"
        branch_info["deletion_message"] = message

    print()
    print_branch_table(all_prunable_branches, show_repo_column, include_status=True)

    successful = sum(1 for b in all_prunable_branches if b.get("deletion_status") == "success")
    failed = sum(1 for b in all_prunable_branches if b.get("deletion_status") == "failed")
    print(f"\nDeleted {successful} branches")
    if failed:
        print(f"Failed to delete {failed} branches")


if __name__ == "__main__":
    main()