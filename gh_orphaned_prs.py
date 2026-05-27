#!/usr/bin/env python3
"""
CLI tool to find merged PRs whose merge commit is no longer present on the
target branch, which indicates the branch history was rewritten, reset, or
otherwise lost the merge. Works across merge, squash, and rebase merges.
"""

import argparse
import subprocess
from typing import List, Dict, Optional
import concurrent.futures
from collections import defaultdict

from github_utils import (
    resolve_targets,
    fetch_merged_prs,
    is_commit_in_branch,
    format_table,
)


def sort_prs(prs: List[Dict], order: str) -> List[Dict]:
    """Sort PRs based on the specified order."""
    if order == "merged":
        return sorted(prs, key=lambda x: x['merged_at'], reverse=True)
    elif order == "title":
        return sorted(prs, key=lambda x: x['title'].lower())
    elif order == "author":
        return sorted(prs, key=lambda x: x.get('user', {}).get('login', '').lower())
    elif order == "repo":
        return sorted(prs, key=lambda x: x['repository'].lower())
    elif order == "number":
        return sorted(prs, key=lambda x: x['number'])
    else:
        return prs


def group_prs(prs: List[Dict], group_by: str) -> Dict[str, List[Dict]]:
    """Group PRs based on the specified grouping."""
    if group_by == "none":
        return {"": prs}
    
    grouped = defaultdict(list)
    
    for pr in prs:
        if group_by == "repo":
            key = pr['repository']
        elif group_by == "author":
            key = pr.get('user', {}).get('login', 'Unknown')
        elif group_by == "target":
            key = pr['target_branch']
        else:
            key = ""
        
        grouped[key].append(pr)
    
    return dict(grouped)


def display_pr_group(prs: List[Dict], group_name: str, show_repo_column: bool):
    """Display a group of PRs in table format."""
    if not prs:
        return

    # Print group header if not empty
    if group_name:
        print(f"=== {group_name} ({len(prs)} PRs) ===")

    headers = ["ID", "TITLE", "AUTHOR", "REPO", "BRANCH", "TARGET", "MERGED"]
    maxs = [None, 50, 20, 30, 25, 20, None]
    rows = []
    for pr in prs:
        rows.append([
            f"#{pr['number']}",
            pr['title'],
            pr.get('user', {}).get('login', ''),
            pr['repository'].split('/')[-1],  # repo name only, not org/repo
            pr['source_branch'],
            pr['target_branch'],
            pr['merged_at'].split('T')[0],  # date part only
        ])

    if not show_repo_column:
        del headers[3]
        del maxs[3]
        rows = [row[:3] + row[4:] for row in rows]

    print("\n".join(format_table(headers, rows, maxs=maxs)))
    print()  # Empty line after each group


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Find merged PRs whose commits are missing from their target branch (indicates history rewrites, resets, or lost commits)",
        prog="gh-orphaned-prs"
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
    parser.add_argument(
        "-B", "--base",
        help="Only check PRs merged to this branch (if omitted, checks all merged PRs)"
    )
    parser.add_argument(
        "-S", "--search",
        help="Additional search terms (GitHub search syntax, e.g., 'merged:>2024-01-01', 'author:username')"
    )
    parser.add_argument(
        "--reopen",
        action="store_true",
        help="Recreate orphaned PRs with the same source/target branches"
    )
    parser.add_argument(
        "--group",
        choices=["repo", "author", "target", "none"],
        default="none",
        help="Group results by repository, author, target branch, or no grouping (default: none)"
    )
    parser.add_argument(
        "--order",
        choices=["merged", "title", "author", "repo", "number"],
        default="merged",
        help="Sort results by merge date, title, author, repository, or PR number (default: merged)"
    )
    parser.add_argument(
        "-H", "--head",
        help="Branch to check for commit existence (defaults to same as --base)"
    )
    return parser.parse_args()


def check_pr_orphaned(owner: str, repo: str, pr_data: Dict, head_branch: Optional[str] = None) -> Optional[Dict]:
    """Return orphaned-PR data if the PR's merge commit is missing from its target branch.

    A merged PR records a ``mergeCommit`` regardless of merge method (merge,
    squash, or rebase). Checking that single commit's reachability avoids the
    false positives that arise from comparing the PR's original commit SHAs —
    which never land on the target branch under squash/rebase merges — while
    still detecting genuine history rewrites, resets, and lost commits.
    """
    target_branch = pr_data.get("base", {}).get("ref", "unknown")
    merge_commit = pr_data.get("merge_commit")

    # Without a merge commit we can't make a reliable determination, so skip
    # rather than risk a false positive.
    if not merge_commit:
        return None

    # Check against an explicit head branch if given, else the target branch.
    check_branch = head_branch if head_branch else target_branch

    if is_commit_in_branch(owner, repo, merge_commit, check_branch):
        return None

    return {
        "number": pr_data["number"],
        "title": pr_data["title"],
        "url": pr_data["html_url"],
        "merged_at": pr_data["merged_at"],
        "source_branch": pr_data.get("head", {}).get("ref", "unknown"),
        "target_branch": target_branch,
        "repository": f"{owner}/{repo}",
        "user": pr_data.get("user", {}),
        "merge_commit": merge_commit,
    }


def check_repository_orphaned_prs(owner: str, repo: str, branch: Optional[str], 
                                  search: Optional[str], head_branch: Optional[str] = None) -> List[Dict]:
    """Check a single repository for orphaned PRs."""
    try:
        merged_prs = fetch_merged_prs(owner, repo, search, branch)
    except Exception as e:
        print(f"  Error fetching PRs for {owner}/{repo}: {e}")
        return []
    
    if not merged_prs:
        return []
    
    # Check each PR for orphaned commits
    orphaned_prs = []
    
    # Process PRs concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        # Submit all PR checks - each PR will be checked against the specified head branch
        future_to_pr = {
            executor.submit(check_pr_orphaned, owner, repo, pr, head_branch): pr
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


def request_review_from_author(owner: str, repo: str, pr_url: str, author: str) -> str:
    """Request a review from the original PR author using gh CLI."""
    try:
        # Extract PR number from URL
        pr_number = pr_url.split('/')[-1]
        
        # Request review using gh CLI
        cmd = [
            "gh", "api", 
            f"repos/{owner}/{repo}/pulls/{pr_number}/requested_reviewers",
            "--method", "POST",
            "--field", f"reviewers[]={author}"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            return f"success - requested review from {author}"
        else:
            error_msg = result.stderr.strip() or "Unknown error"
            if "Review cannot be requested from pull request author" in error_msg:
                return "skipped - cannot request review from PR author"
            return f"failed - {error_msg}"
            
    except Exception as e:
        return f"failed - {str(e)}"


def recreate_pr(owner: str, repo: str, pr_data: Dict, target_branch: str) -> Dict[str, str]:
    """Recreate a PR using gh CLI."""
    source_branch = pr_data["source_branch"]
    title = f"{pr_data['title']} (reopened)"
    pr_number = pr_data["number"]
    original_author = pr_data.get("user", {}).get("login")
    
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
            
            # Request review from original author if available
            review_status = None
            if original_author:
                review_status = request_review_from_author(owner, repo, pr_url, original_author)
            
            return {
                "status": "success",
                "original_pr": pr_number,
                "new_pr_url": pr_url,
                "source_branch": source_branch,
                "review_requested": review_status
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
    # GitHub CLI handles authentication automatically

    # Determine target repository/organization
    org, repos, used_wildcard = resolve_targets(args.repo, args.target)

    # Determine head branch for commit checking
    head_branch = args.head if args.head else args.base

    all_orphaned_prs = []

    # Check repositories concurrently (org mode can span many repos).
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(
                check_repository_orphaned_prs, org, repo_name, args.base, args.search, head_branch
            )
            for repo_name in repos
        ]
        for future in concurrent.futures.as_completed(futures):
            try:
                all_orphaned_prs.extend(future.result())
            except Exception:
                continue
    
    # Display results
    if all_orphaned_prs:
        print(f"\nShowing {len(all_orphaned_prs)} orphaned pull requests")
        print()
        
        # Sort PRs based on specified order
        sorted_prs = sort_prs(all_orphaned_prs, args.order)
        
        # Group PRs if requested
        grouped_prs = group_prs(sorted_prs, args.group)
        
        # Check if we have multiple repositories or used a wildcard
        unique_repos = set(pr['repository'] for pr in all_orphaned_prs)
        show_repo_column = len(unique_repos) > 1 or used_wildcard
        
        # Display grouped results
        for group_name in sorted(grouped_prs.keys()):
            group_prs_list = grouped_prs[group_name]
            display_pr_group(group_prs_list, group_name, show_repo_column)
        
        # Handle --reopen option
        if args.reopen:
            # Collect all reopen results first
            reopen_results = []
            
            for pr in all_orphaned_prs:
                repo_parts = pr['repository'].split('/')
                owner_name, repo_name = repo_parts[0], repo_parts[1]
                
                result = recreate_pr(owner_name, repo_name, pr, pr['target_branch'])
                
                # Add original PR data to result for display
                result['original_title'] = pr['title']
                result['original_author'] = pr.get('user', {}).get('login', '')
                result['original_repo'] = pr['repository']
                result['original_merged_date'] = pr['merged_at'].split('T')[0]
                
                reopen_results.append(result)
            
            # Display consolidated table with reopen results
            if reopen_results:
                print("\nReopen Results:")
                print()

                # Show the repo column when spanning multiple repos or a whole org.
                unique_repos = set(result['original_repo'] for result in reopen_results)
                show_repo_column = len(unique_repos) > 1 or used_wildcard

                headers = ["ID", "TITLE", "AUTHOR", "REPO", "NEW PR", "STATUS"]
                maxs = [None, 40, 15, 20, 50, 60]
                rows = []
                for result in reopen_results:
                    if result["status"] == "success":
                        status = "✓ Success"
                        review = result.get("review_requested") or ""
                        if "success" in review:
                            status += " (review requested)"
                        elif "failed" in review:
                            status += " (review failed)"
                        new_pr = result.get('new_pr_url', '')
                    else:
                        status = f"✗ {result['error']}"
                        new_pr = '-'
                    rows.append([
                        f"#{result['original_pr']}",
                        result['original_title'],
                        result['original_author'],
                        result['original_repo'].split('/')[-1],
                        new_pr,
                        status,
                    ])

                if not show_repo_column:
                    del headers[3]
                    del maxs[3]
                    rows = [row[:3] + row[4:] for row in rows]

                print("\n".join(format_table(headers, rows, maxs=maxs)))

                # Summary
                successful_count = sum(1 for result in reopen_results if result["status"] == "success")
                failed_count = len(reopen_results) - successful_count
                
                print(f"\nReopened {successful_count} of {len(reopen_results)} PRs")
                if failed_count > 0:
                    print(f"Failed to reopen {failed_count} PRs")
    else:
        print("\nNo orphaned pull requests found")


if __name__ == "__main__":
    main()