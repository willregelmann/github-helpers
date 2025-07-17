# GitHub Helpers

A collection of GitHub CLI helper tools for repository management and analysis.

## Tools

### `gh-check-ahead`
Find repositories where a branch is ahead of another branch.

**Usage:**
```bash
gh-check-ahead -H <head> [-B <base>] [target]
```

**Arguments:**
- `target` - Organization (e.g., `myorg`) or specific repository (`myorg/myrepo`) - defaults to current repository

**Options:**
- `-H, --head HEAD` - Source branch to check (the branch that might be ahead) - **required**
- `-B, --base BASE` - Target branch to compare against (defaults to repository's default branch)
- `-R, --repo REPO` - Repository to check (owner/repo format, or owner/* for all repos in org)
- `--token TOKEN` - GitHub personal access token

**Examples:**
```bash
# Check if dev is ahead of main in current repository
gh-check-ahead -H dev -B main

# Check if dev is ahead of default branch in specific repository
gh-check-ahead -H dev -R owner/repo

# Check if feature is ahead of develop across all repos in organization
gh-check-ahead -H feature -B develop -R "myorg/*"

# Check if main is ahead of release in current repository
gh-check-ahead -H main -B release
```

### `gh-orphaned-prs`
Find merged PRs whose commits are missing from their target branch (indicates history rewrites, resets, or lost commits).

**Usage:**
```bash
gh-orphaned-prs [target] [options]
```

**Arguments:**
- `target` - Organization (e.g., `myorg`) or specific repository (`myorg/myrepo`) - defaults to current repository

**Options:**
- `-R, --repo REPO` - Repository to check (owner/repo format)
- `-B, --base BASE` - Only check PRs merged to this branch (if omitted, checks all merged PRs)
- `-S, --search SEARCH` - Additional search terms (GitHub search syntax)
- `--reopen` - Recreate orphaned PRs with the same source/target branches
- `--token TOKEN` - GitHub personal access token

**Examples:**
```bash
# Find all orphaned PRs in current repository
gh-orphaned-prs

# Find orphaned PRs in a specific repository
gh-orphaned-prs -R myorg/myrepo

# Find orphaned PRs merged to main branch only
gh-orphaned-prs --base main

# Find orphaned PRs merged after a specific date
gh-orphaned-prs -S "merged:>2024-01-01"

# Find orphaned PRs from a specific author
gh-orphaned-prs -S "author:username"

# Find orphaned PRs across all repos in an organization
gh-orphaned-prs -R "myorg/*"

# Find and automatically reopen orphaned PRs, requesting review from original authors
gh-orphaned-prs --reopen
```

## Installation

### From Source
```bash
git clone https://github.com/your-username/github-helpers.git
cd github-helpers
pip install -e .
```

### Requirements
- Python 3.6+
- `requests` library
- GitHub CLI (`gh`) for authentication (optional)

## Authentication

The tools support multiple authentication methods:

1. **GitHub CLI** (recommended): Run `gh auth login` to authenticate
2. **Environment variable**: Set `GITHUB_TOKEN` environment variable
3. **Command line**: Use `--token` flag with your personal access token

**Required token scopes:**
- `repo` - For accessing private repositories
- `read:org` - For accessing organization repositories

## Features

### `gh-check-ahead`
- ✅ Concurrent processing for fast organization-wide checks
- ✅ GitHub CLI-style interface with `-H/--head` and `-B/--base` flags
- ✅ Current directory repository auto-detection
- ✅ Wildcard organization support with `-R "owner/*"` 
- ✅ Clean tabular output with REPO, HEAD, BASE, AHEAD, BEHIND, STATUS columns
- ✅ Smart default branch detection when base not specified
- ✅ Rate limiting handling
- ✅ Supports single repositories, organizations, and current repo

### `gh-orphaned-prs`
- ✅ Fast PR discovery using GitHub Search API
- ✅ Concurrent commit checking for performance  
- ✅ GitHub CLI-style interface with `-R` and `-B` flags
- ✅ Current directory repository auto-detection
- ✅ Wildcard organization support with `-R "owner/*"`
- ✅ Flexible search filtering with GitHub search syntax (`-S`)
- ✅ Clean tabular output matching `gh pr list` format
- ✅ Automatic PR recreation with `--reopen` flag
- ✅ Review requests to original authors on reopened PRs
- ✅ Proper orphan detection (checks commits against actual target branches)
- ✅ Organization-wide or single repository analysis

## Output Examples

### `gh-check-ahead` Output
```bash
$ gh-check-ahead -H dev -B main -R "myorg/*"
Showing 5 repositories where dev is ahead

REPO              HEAD  BASE  AHEAD  BEHIND  STATUS
analytics-python  dev   main  51     -       
CommandSecurity   dev   main  304    -       
Integrations      dev   main  17     6       diverged
react-cmdlnk-gui  dev   main  122    -       
terraform-jenkins dev   main  6      2       diverged
```

### `gh-orphaned-prs` Output
```bash
$ gh-orphaned-prs
Showing 3 orphaned pull requests

ID   TITLE                         BRANCH                TARGET        MERGED
#16  Test PR for dev branch reset  reset-test            dev           2025-07-17
#12  Test hotfix 2                 hotfix/test-hotfix-2  release/24.1  2024-12-12
#8   Test hotfix commit 3          test-hotfix-3         release/24.7  2024-12-12
```

### `gh-orphaned-prs --reopen` Output
```bash
$ gh-orphaned-prs --reopen
Showing 1 orphaned pull requests

ID   TITLE                         BRANCH      TARGET  MERGED
#16  Test PR for dev branch reset  reset-test  dev     2025-07-17

✓ Reopened PR #16 as https://github.com/owner/repo/pull/20 (review requested)

Reopened 1 of 1 PRs
```

## Use Cases

### Branch Management
- Identify repositories with unmerged feature branches
- Find branches that need to be merged or cleaned up
- Monitor branch synchronization across organizations

### PR Analysis
- Detect merged PRs whose commits are missing from their target branches
- Identify history rewrites, force pushes, and branch resets
- Find lost commits from deleted/recreated release branches
- Recover orphaned work by reopening PRs with review requests
- Validate that merged PRs are properly integrated into their target branches

### DevOps & CI/CD
- Validate deployment readiness across repositories
- Ensure all merged work is properly integrated
- Automate branch hygiene maintenance

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Submit a pull request

## License

MIT License - see LICENSE file for details.
