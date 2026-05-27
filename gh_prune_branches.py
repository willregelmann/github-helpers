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
    org, repos, _ = resolve_targets(args.repo)

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

        # Show the repo column only when spanning multiple repositories.
        unique_repos = set(branch['repository'] for branch in all_prunable_branches)
        show_repo_column = len(unique_repos) > 1

        if args.report:
            headers = ["REPO", "BRANCH", "DEFAULT", "BEHIND"]
            maxs = [30, 30, 20, None]
        else:
            headers = ["REPO", "BRANCH", "DEFAULT", "BEHIND", "STATUS"]
            maxs = [30, 30, 20, None, None]

        rows = []
        for branch_info in all_prunable_branches:
            behind = branch_info["behind_by"] if branch_info["behind_by"] > 0 else "-"
            row = [branch_info["repository"], branch_info["branch"], branch_info["default_branch"], behind]
            if not args.report:
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