#!/usr/bin/env python3
"""
Shared GitHub utilities for gh-helpers tools.
Provides common GitHub CLI-based operations.
"""

import subprocess
import json
import os
import sys
from typing import Any, List, Dict, Optional, Sequence, Tuple


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


def resolve_targets(repo: Optional[str], target: Optional[str] = None) -> Tuple[str, List[str], bool]:
    """Resolve a target specification into (org, repos, used_wildcard).

    Precedence: the ``--repo`` value, then a positional ``target``, then the
    current repository. ``repos`` is the concrete list of repository names
    within ``org`` (org-wide modes are expanded via :func:`get_organization_repos`).
    ``used_wildcard`` is True when operating across a whole organization.

    Exits the process with a helpful message when no target can be determined.
    """
    used_wildcard = False
    repos: Optional[List[str]] = None

    if repo:
        target_pattern, is_wildcard = parse_repo_pattern(repo)
        used_wildcard = is_wildcard
        if is_wildcard:
            org = target_pattern
        else:
            org, repo_name = parse_target(target_pattern)
            repos = [repo_name] if repo_name else None
    elif target:
        org, repo_name = parse_target(target)
        repos = [repo_name] if repo_name else None
        used_wildcard = repo_name is None
    else:
        current_repo = get_current_repository()
        if not current_repo:
            print("Error: Could not detect current repository and no target specified.", file=sys.stderr)
            print("Use --repo owner/repo or run from a git repository with GitHub remote.", file=sys.stderr)
            sys.exit(1)
        org, repo_name = parse_target(current_repo)
        repos = [repo_name]

    if repos is None:
        repos = get_organization_repos(org)

    return org, repos, used_wildcard


def _truncate(text: str, width: int) -> str:
    """Truncate ``text`` to ``width`` columns, marking elision with an ellipsis."""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"


def format_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    mins: Optional[Sequence[int]] = None,
    maxs: Optional[Sequence[Optional[int]]] = None,
    gap: str = "  ",
) -> List[str]:
    """Render aligned columns and return the lines (header row first).

    Each column's width is the longest of its header and cell values, clamped
    to ``mins[i]`` and ``maxs[i]`` when provided; cells exceeding the width are
    truncated with an ellipsis. The final column is left unpadded so there is no
    trailing whitespace.
    """
    n = len(headers)
    mins = list(mins) if mins is not None else [0] * n
    maxs = list(maxs) if maxs is not None else [None] * n
    str_rows = [[str(cell) for cell in row] for row in rows]

    widths = []
    for i in range(n):
        content = max([len(headers[i])] + [len(row[i]) for row in str_rows])
        width = max(content, mins[i])
        if maxs[i] is not None:
            width = min(width, maxs[i])
        widths.append(width)

    def render(cells: Sequence[str]) -> str:
        parts = []
        for i, cell in enumerate(cells):
            cell = _truncate(cell, widths[i])
            # Last column is unpadded to avoid trailing whitespace.
            parts.append(cell if i == n - 1 else f"{cell:<{widths[i]}}")
        return gap.join(parts)

    return [render([str(h) for h in headers])] + [render(row) for row in str_rows]


def parse_repo_pattern(repo_pattern: str) -> Tuple[str, bool]:
    """Parse repo pattern to determine if it's a wildcard for organization."""
    if repo_pattern.endswith("/*"):
        # Remove the /* suffix to get organization name
        org = repo_pattern[:-2]
        return org, True  # True indicates wildcard/organization mode
    else:
        # Specific repository
        return repo_pattern, False


def get_organization_repos(org: str) -> List[str]:
    """Get all repositories for an organization using gh CLI."""
    cmd = ["gh", "repo", "list", org, "--limit", "1000", "--json", "name"]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return []
        
        repos_data = json.loads(result.stdout)
        return [repo["name"] for repo in repos_data]
        
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return []


def get_default_branch(org: str, repo: str) -> Optional[str]:
    """Get the default branch for a repository using gh CLI."""
    cmd = ["gh", "repo", "view", f"{org}/{repo}", "--json", "defaultBranchRef"]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return None
        
        data = json.loads(result.stdout)
        return data.get("defaultBranchRef", {}).get("name")
        
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return None


def check_branch_exists(org: str, repo: str, branch: str) -> bool:
    """Check if a branch exists in a repository using gh CLI."""
    cmd = ["gh", "api", f"repos/{org}/{repo}/branches/{branch}", "--silent"]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0
    except subprocess.SubprocessError:
        return False


def compare_branches(org: str, repo: str, base: str, head: str) -> Optional[Dict]:
    """Compare two branches and return comparison data using gh CLI."""
    cmd = ["gh", "api", f"repos/{org}/{repo}/compare/{base}...{head}"]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return None
        
        return json.loads(result.stdout)
        
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return None


def fetch_merged_prs(owner: str, repo: str, search: Optional[str], branch: Optional[str]) -> List[Dict]:
    """Fetch merged PRs using gh CLI."""
    # Build gh pr list command
    cmd = ["gh", "pr", "list", "--repo", f"{owner}/{repo}", "--state", "merged", "--limit", "1000"]
    
    # Add base branch filter if specified
    if branch:
        cmd.extend(["--base", branch])
    
    # Add search terms if specified
    if search:
        cmd.extend(["--search", search])
    
    # Request JSON output with all fields we need
    cmd.extend(["--json", "number,title,baseRefName,headRefName,mergedAt,url,author"])
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return []
        
        merged_prs_data = json.loads(result.stdout)
        
        # Convert to expected format
        merged_prs = []
        for pr in merged_prs_data:
            merged_prs.append({
                "number": pr["number"],
                "title": pr["title"],
                "html_url": pr["url"],
                "merged_at": pr["mergedAt"],
                "base": {"ref": pr["baseRefName"]},
                "head": {"ref": pr["headRefName"]},
                "user": pr.get("author", {})
            })
        
        return merged_prs
        
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return []


def get_pr_commits(owner: str, repo: str, pr_number: int) -> List[str]:
    """Get all commit SHAs from a PR using gh CLI."""
    cmd = ["gh", "pr", "view", str(pr_number), "--repo", f"{owner}/{repo}", "--json", "commits"]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return []
        
        data = json.loads(result.stdout)
        commits = data.get("commits", [])
        return [commit["oid"] for commit in commits]
        
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return []


def is_commit_in_branch(owner: str, repo: str, commit_sha: str, branch: str) -> bool:
    """Check if a commit exists in the specified branch using gh CLI."""
    try:
        # Use gh api to compare commits
        cmd = ["gh", "api", f"repos/{owner}/{repo}/compare/{commit_sha}...{branch}"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            return False
        
        data = json.loads(result.stdout)
        status = data.get("status", "unknown")
        return status in ["identical", "ahead"]
        
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return False