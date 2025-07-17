#!/usr/bin/env python3
"""
CLI tool to find repositories where a branch is ahead of another branch.
"""

import argparse
import sys
from typing import List, Dict, Optional, Tuple
import requests
import os
import concurrent.futures
import time
import subprocess


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
    parser.add_argument(
        "--token",
        help="GitHub personal access token (or set GITHUB_TOKEN env var)"
    )
    return parser.parse_args()


def get_current_repository() -> Optional[str]:
    """Get the current repository from git remote origin."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            cwd=os.getcwd()
        )
        if result.returncode == 0:
            remote_url = result.stdout.strip()
            # Parse GitHub URLs (both HTTPS and SSH formats)
            if "github.com" in remote_url:
                if remote_url.startswith("git@github.com:"):
                    # SSH format: git@github.com:owner/repo.git
                    repo_path = remote_url.replace("git@github.com:", "").replace(".git", "")
                elif "github.com/" in remote_url:
                    # HTTPS format: https://github.com/owner/repo.git
                    repo_path = remote_url.split("github.com/")[1].replace(".git", "")
                else:
                    return None
                
                if "/" in repo_path:
                    return repo_path
        return None
    except Exception:
        return None


def parse_target(target: str) -> Tuple[str, Optional[str]]:
    """Parse target to extract organization and optional repository."""
    if "/" in target:
        parts = target.split("/", 1)
        return parts[0], parts[1]
    return target, None


def parse_repo_pattern(repo_pattern: str) -> Tuple[str, bool]:
    """Parse repo pattern to determine if it's a wildcard for organization."""
    if repo_pattern.endswith("/*"):
        # Remove the /* suffix to get organization name
        org = repo_pattern[:-2]
        return org, True  # True indicates wildcard/organization mode
    else:
        # Specific repository
        return repo_pattern, False




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


def check_branch_exists(org: str, repo: str, branch: str, token: Optional[str]) -> bool:
    """Check if a branch exists in a repository."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    
    url = f"https://api.github.com/repos/{org}/{repo}/branches/{branch}"
    response = requests.get(url, headers=headers)
    
    # Handle rate limiting
    if response.status_code == 429:
        reset_time = int(response.headers.get('X-RateLimit-Reset', time.time() + 60))
        sleep_time = max(1, reset_time - int(time.time()))
        time.sleep(sleep_time)
        response = requests.get(url, headers=headers)
    
    return response.status_code == 200


def compare_branches(org: str, repo: str, base: str, head: str, token: Optional[str]) -> Optional[Dict]:
    """Compare two branches and return comparison data."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    
    url = f"https://api.github.com/repos/{org}/{repo}/compare/{base}...{head}"
    response = requests.get(url, headers=headers)
    
    # Handle rate limiting
    if response.status_code == 429:
        reset_time = int(response.headers.get('X-RateLimit-Reset', time.time() + 60))
        sleep_time = max(1, reset_time - int(time.time()))
        time.sleep(sleep_time)
        response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    return None


def check_repo_branches(org: str, repo: str, head_branch: str, base_branch: Optional[str], token: Optional[str]) -> Optional[Dict]:
    """Check a single repo for branch comparison."""
    # If base_branch is None, get the default branch
    if base_branch is None:
        base_branch = get_default_branch(org, repo, token)
        if base_branch is None:
            return None
    
    # Check if both branches exist
    head_exists = check_branch_exists(org, repo, head_branch, token)
    if not head_exists:
        return None
        
    base_exists = check_branch_exists(org, repo, base_branch, token)
    if not base_exists:
        return None
    
    # Compare branches (head vs base)
    comparison = compare_branches(org, repo, base_branch, head_branch, token)
    
    if comparison:
        ahead_by = comparison.get("ahead_by", 0)
        behind_by = comparison.get("behind_by", 0)
        status = comparison.get("status", "unknown")
        
        if ahead_by > 0 and status in ["ahead", "diverged"]:
            return {
                "name": repo,
                "url": f"https://github.com/{org}/{repo}",
                "ahead_by": ahead_by,
                "behind_by": behind_by,
                "status": status,
                "head_branch": head_branch,
                "base_branch": base_branch
            }
    
    return None


def main():
    args = parse_arguments()
    token = get_github_token(args.token)
    
    head_branch = args.head
    base_branch = args.base  # Can be None (will use default branch)
    
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
            repos = get_organization_repos(org, token)
        except requests.exceptions.RequestException as e:
            print(f"Error fetching repositories: {e}", file=sys.stderr)
            sys.exit(1)
    
    found_repos = []
    
    # Process repos concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        # Submit all repo checks
        future_to_repo = {
            executor.submit(check_repo_branches, org, repo_name, head_branch, base_branch, token): repo_name
            for repo_name in repos
        }
        
        # Collect results as they complete
        for future in concurrent.futures.as_completed(future_to_repo):
            repo_name = future_to_repo[future]
            try:
                result = future.result()
                if result:
                    found_repos.append(result)
            except Exception:
                continue
    
    # Display results
    if found_repos:
        print(f"\nShowing {len(found_repos)} repositories where {head_branch} is ahead")
        print()
        
        # Sort by repository name
        found_repos.sort(key=lambda x: x['name'])
        
        # Calculate column widths
        repo_width = max(len(repo['name']) for repo in found_repos)
        repo_width = max(repo_width, 4)  # minimum width for "REPO"
        repo_width = min(repo_width, 30)  # cap at 30 chars
        
        head_width = max(len(repo['head_branch']) for repo in found_repos)
        head_width = max(head_width, 4)  # minimum width for "HEAD"
        head_width = min(head_width, 20)  # cap at 20 chars
        
        base_width = max(len(repo['base_branch']) for repo in found_repos)
        base_width = max(base_width, 4)  # minimum width for "BASE"
        base_width = min(base_width, 20)  # cap at 20 chars
        
        # Print header
        print(f"{'REPO':<{repo_width}}  {'HEAD':<{head_width}}  {'BASE':<{base_width}}  AHEAD  BEHIND  STATUS")
        
        # Print repositories
        for repo in found_repos:
            repo_name = repo['name'][:repo_width] if len(repo['name']) > repo_width else repo['name']
            head_name = repo['head_branch'][:head_width] if len(repo['head_branch']) > head_width else repo['head_branch']
            base_name = repo['base_branch'][:base_width] if len(repo['base_branch']) > base_width else repo['base_branch']
            ahead = str(repo['ahead_by'])
            behind = str(repo['behind_by']) if repo['behind_by'] > 0 else "-"
            status = repo['status'] if repo['status'] == "diverged" else ""
            
            print(f"{repo_name:<{repo_width}}  {head_name:<{head_width}}  {base_name:<{base_width}}  {ahead:<5}  {behind:<6}  {status}")
        
        print()  # Empty line after table
    else:
        print("\nNo repositories found where branch is ahead")


if __name__ == "__main__":
    main()