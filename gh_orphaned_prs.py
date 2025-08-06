#!/usr/bin/env python3
"""
CLI tool to find merged PRs that contain commits not present in the target branch.
"""

import argparse
import subprocess
import json
import sys
from typing import List, Dict, Optional, Tuple
import concurrent.futures
import os

from github_utils import (
    get_current_repository,
    parse_target,
    parse_repo_pattern,
    get_organization_repos,
    get_default_branch,
    fetch_merged_prs,
    get_pr_commits,
    is_commit_in_branch
)


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
    return parser.parse_args()


def check_pr_commits_concurrent(owner: str, repo: str, pr_data: Dict) -> Optional[Dict]:
    """Check commits for a single PR and return orphaned PR data if any commits are missing."""
    pr_number = pr_data["number"]
    pr_title = pr_data["title"]
    target_branch = pr_data.get("base", {}).get("ref", "unknown")
    
    try:
        commits = get_pr_commits(owner, repo, pr_number)
    except Exception:
        return None
    
    # Check commits concurrently against the PR's actual target branch
    missing_commits = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        # Submit all commit checks
        future_to_commit = {
            executor.submit(is_commit_in_branch, owner, repo, commit_sha, target_branch): commit_sha
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
            "target_branch": target_branch,
            "repository": f"{owner}/{repo}",
            "user": pr_data.get("user", {}),
            "missing_commits": missing_commits
        }
    
    return None


def check_repository_orphaned_prs(owner: str, repo: str, branch: Optional[str], 
                                  search: Optional[str]) -> List[Dict]:
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
        # Submit all PR checks - each PR will be checked against its own target branch
        future_to_pr = {
            executor.submit(check_pr_commits_concurrent, owner, repo, pr): pr
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
                return f"skipped - cannot request review from PR author"
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
    elif args.target:
        # Use positional target argument
        org, repo = parse_target(args.target)
        repos = [repo] if repo else None
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
    
    all_orphaned_prs = []
    
    for repo_name in repos:
        orphaned_prs = check_repository_orphaned_prs(
            org, repo_name, args.base, args.search
        )
        
        if orphaned_prs:
            all_orphaned_prs.extend(orphaned_prs)
    
    # Display results
    if all_orphaned_prs:
        print(f"\nShowing {len(all_orphaned_prs)} orphaned pull requests")
        print()
        
        # Sort by merge date (newest first)
        all_orphaned_prs.sort(key=lambda x: x['merged_at'], reverse=True)
        
        # Check if we have multiple repositories
        unique_repos = set(pr['repository'] for pr in all_orphaned_prs)
        show_repo_column = len(unique_repos) > 1
        
        # Calculate column widths
        id_width = max(len(f"#{pr['number']}") for pr in all_orphaned_prs)
        id_width = max(id_width, 2)  # minimum width for "ID"
        
        title_width = max(len(pr['title']) for pr in all_orphaned_prs)
        title_width = max(title_width, 5)  # minimum width for "TITLE"
        title_width = min(title_width, 50)  # cap at 50 chars
        
        author_width = max(len(pr.get('user', {}).get('login', '')) for pr in all_orphaned_prs)
        author_width = max(author_width, 6)  # minimum width for "AUTHOR"
        author_width = min(author_width, 20)  # cap at 20 chars
        
        branch_width = max(len(pr['source_branch']) for pr in all_orphaned_prs)
        branch_width = max(branch_width, 6)  # minimum width for "BRANCH"
        branch_width = min(branch_width, 25)  # cap at 25 chars
        
        target_width = max(len(pr['target_branch']) for pr in all_orphaned_prs)
        target_width = max(target_width, 6)  # minimum width for "TARGET"
        target_width = min(target_width, 20)  # cap at 20 chars
        
        repo_width = 0
        if show_repo_column:
            repo_names = [pr['repository'].split('/')[-1] for pr in all_orphaned_prs]  # Just repo name, not org/repo
            repo_width = max(len(repo_name) for repo_name in repo_names)
            repo_width = max(repo_width, 4)  # minimum width for "REPO"
            repo_width = min(repo_width, 30)  # cap at 30 chars
        
        # Print header
        if show_repo_column:
            print(f"{'ID':<{id_width}}  {'TITLE':<{title_width}}  {'AUTHOR':<{author_width}}  {'REPO':<{repo_width}}  {'BRANCH':<{branch_width}}  {'TARGET':<{target_width}}  MERGED")
        else:
            print(f"{'ID':<{id_width}}  {'TITLE':<{title_width}}  {'AUTHOR':<{author_width}}  {'BRANCH':<{branch_width}}  {'TARGET':<{target_width}}  MERGED")
        
        # Print PRs
        for pr in all_orphaned_prs:
            pr_id = f"#{pr['number']}"
            title = pr['title'][:title_width] if len(pr['title']) > title_width else pr['title']
            author = pr.get('user', {}).get('login', '')
            author_display = author[:author_width] if len(author) > author_width else author
            branch = pr['source_branch'][:branch_width] if len(pr['source_branch']) > branch_width else pr['source_branch']
            target = pr['target_branch'][:target_width] if len(pr['target_branch']) > target_width else pr['target_branch']
            merged_date = pr['merged_at'].split('T')[0]  # Just the date part
            
            if show_repo_column:
                repo_name = pr['repository'].split('/')[-1]  # Just repo name
                repo_display = repo_name[:repo_width] if len(repo_name) > repo_width else repo_name
                print(f"{pr_id:<{id_width}}  {title:<{title_width}}  {author_display:<{author_width}}  {repo_display:<{repo_width}}  {branch:<{branch_width}}  {target:<{target_width}}  {merged_date}")
            else:
                print(f"{pr_id:<{id_width}}  {title:<{title_width}}  {author_display:<{author_width}}  {branch:<{branch_width}}  {target:<{target_width}}  {merged_date}")
        
        print()  # Empty line after table
        
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
                
                # Calculate column widths for reopen table
                id_width = max(len(f"#{result['original_pr']}") for result in reopen_results)
                id_width = max(id_width, 2)  # minimum width for "ID"
                
                title_width = max(len(result['original_title']) for result in reopen_results)
                title_width = max(title_width, 5)  # minimum width for "TITLE"
                title_width = min(title_width, 40)  # cap at 40 chars
                
                author_width = max(len(result['original_author']) for result in reopen_results)
                author_width = max(author_width, 6)  # minimum width for "AUTHOR"
                author_width = min(author_width, 15)  # cap at 15 chars
                
                # Calculate NEW PR column width
                new_pr_urls = [result.get('new_pr_url', '') for result in reopen_results]
                new_pr_width = max(len(url) for url in new_pr_urls) if any(new_pr_urls) else 6
                new_pr_width = max(new_pr_width, 6)  # minimum width for "NEW PR"
                new_pr_width = min(new_pr_width, 50)  # cap at 50 chars
                
                # Calculate STATUS column width
                status_messages = []
                for result in reopen_results:
                    if result["status"] == "success":
                        status = "✓ Success"
                        if result.get("review_requested"):
                            if "success" in result["review_requested"]:
                                status += " (review requested)"
                            elif "failed" in result["review_requested"]:
                                status += " (review failed)"
                    else:
                        status = f"✗ {result['error']}"
                    status_messages.append(status)
                
                status_width = max(len(status) for status in status_messages)
                status_width = max(status_width, 6)  # minimum width for "STATUS"
                status_width = min(status_width, 60)  # cap at 60 chars
                
                # Check if we have multiple repositories
                unique_repos = set(result['original_repo'] for result in reopen_results)
                show_repo_column = len(unique_repos) > 1
                
                repo_width = 0
                if show_repo_column:
                    repo_names = [result['original_repo'].split('/')[-1] for result in reopen_results]
                    repo_width = max(len(repo_name) for repo_name in repo_names)
                    repo_width = max(repo_width, 4)  # minimum width for "REPO"
                    repo_width = min(repo_width, 20)  # cap at 20 chars
                
                # Print header
                if show_repo_column:
                    print(f"{'ID':<{id_width}}  {'TITLE':<{title_width}}  {'AUTHOR':<{author_width}}  {'REPO':<{repo_width}}  {'NEW PR':<{new_pr_width}}  {'STATUS':<{status_width}}")
                else:
                    print(f"{'ID':<{id_width}}  {'TITLE':<{title_width}}  {'AUTHOR':<{author_width}}  {'NEW PR':<{new_pr_width}}  {'STATUS':<{status_width}}")
                
                # Print results
                for i, result in enumerate(reopen_results):
                    pr_id = f"#{result['original_pr']}"
                    title = result['original_title'][:title_width] if len(result['original_title']) > title_width else result['original_title']
                    author = result['original_author'][:author_width] if len(result['original_author']) > author_width else result['original_author']
                    
                    # Handle NEW PR column
                    if result["status"] == "success":
                        new_pr_url = result.get('new_pr_url', '')
                        new_pr_display = new_pr_url[:new_pr_width] if len(new_pr_url) > new_pr_width else new_pr_url
                    else:
                        new_pr_display = '-'
                    
                    # Handle STATUS column
                    status = status_messages[i]
                    status_display = status[:status_width] if len(status) > status_width else status
                    
                    if show_repo_column:
                        repo_name = result['original_repo'].split('/')[-1]
                        repo_display = repo_name[:repo_width] if len(repo_name) > repo_width else repo_name
                        print(f"{pr_id:<{id_width}}  {title:<{title_width}}  {author:<{author_width}}  {repo_display:<{repo_width}}  {new_pr_display:<{new_pr_width}}  {status_display}")
                    else:
                        print(f"{pr_id:<{id_width}}  {title:<{title_width}}  {author:<{author_width}}  {new_pr_display:<{new_pr_width}}  {status_display}")
                
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