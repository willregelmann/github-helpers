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
        "target",
        help="Organization or organization/repository"
    )
    parser.add_argument(
        "branches",
        help="Branch comparison in format 'from_branch[..to_branch]'"
    )
    parser.add_argument(
        "--token",
        help="GitHub personal access token (or set GITHUB_TOKEN env var)"
    )
    return parser.parse_args()


def parse_target(target: str) -> Tuple[str, Optional[str]]:
    """Parse target to extract organization and optional repository."""
    if "/" in target:
        parts = target.split("/", 1)
        return parts[0], parts[1]
    return target, None


def parse_branches(branches: str) -> Tuple[str, str]:
    """Parse branch specification to extract from and to branches."""
    if ".." in branches:
        from_branch, to_branch = branches.split("..", 1)
        return from_branch, to_branch
    return branches, None


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


def check_repo_branches(org: str, repo: str, from_branch: str, to_branch: str, token: Optional[str]) -> Optional[Dict]:
    """Check a single repo for branch comparison."""
    # If to_branch is None, get the default branch
    if to_branch is None:
        to_branch = get_default_branch(org, repo, token)
        if to_branch is None:
            return None
    
    # Check if both branches exist
    from_exists = check_branch_exists(org, repo, from_branch, token)
    if not from_exists:
        return None
        
    to_exists = check_branch_exists(org, repo, to_branch, token)
    if not to_exists:
        return None
    
    # Compare branches
    comparison = compare_branches(org, repo, to_branch, from_branch, token)
    
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
                "from_branch": from_branch,
                "to_branch": to_branch
            }
    
    return None


def main():
    args = parse_arguments()
    token = get_github_token(args.token)
    
    org, repo = parse_target(args.target)
    from_branch, to_branch = parse_branches(args.branches)
    
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
    
    if to_branch:
        print(f"Looking for repos where '{from_branch}' is ahead of '{to_branch}'...")
    else:
        print(f"Looking for repos where '{from_branch}' is ahead of default branch...")
    print()
    
    found_repos = []
    
    # Process repos concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        # Submit all repo checks
        future_to_repo = {
            executor.submit(check_repo_branches, org, repo_name, from_branch, to_branch, token): repo_name
            for repo_name in repos
        }
        
        # Collect results as they complete
        for future in concurrent.futures.as_completed(future_to_repo):
            repo_name = future_to_repo[future]
            try:
                result = future.result()
                if result:
                    if result["status"] == "diverged":
                        print(f"✓ {result['name']} ({result['url']}): {result['from_branch']} is {result['ahead_by']} commits ahead, {result['behind_by']} commits behind {result['to_branch']} (diverged)")
                    else:
                        print(f"✓ {result['name']} ({result['url']}): {result['from_branch']} is {result['ahead_by']} commits ahead of {result['to_branch']}")
                    
                    found_repos.append(result)
            except Exception as e:
                print(f"  Error checking {repo_name}: {e}")
    
    # Display summary
    print()
    print("Summary:")
    if not found_repos:
        if to_branch:
            print(f"No repositories found where {from_branch} is ahead of {to_branch}.")
        else:
            print(f"No repositories found where {from_branch} is ahead of default branch.")
    else:
        summary_msg = f"Found {len(found_repos)} repositories where {from_branch} is ahead"
        if to_branch:
            summary_msg += f" of {to_branch}:"
        else:
            summary_msg += " of default branch:"
        print(summary_msg)
        for repo in found_repos:
            print(f"  - {repo['name']} ({repo['url']})")


if __name__ == "__main__":
    main()