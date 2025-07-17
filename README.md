# GitHub Helpers

A collection of GitHub CLI helper tools for repository management and analysis.

## Tools

### `gh-check-ahead`
Find repositories where a branch is ahead of another branch.

**Usage:**
```bash
gh-check-ahead <target> <branches>
```

**Arguments:**
- `target` - Organization (e.g., `myorg`) or specific repository (`myorg/myrepo`)
- `branches` - Branch comparison in format `from_branch[..to_branch]`

**Examples:**
```bash
# Check if 'develop' is ahead of 'main' in all repos in an organization
gh-check-ahead myorg develop..main

# Check if 'feature' is ahead of default branch in a specific repo
gh-check-ahead myorg/myrepo feature

# Check if 'staging' is ahead of 'production' in a specific repo
gh-check-ahead myorg/myrepo staging..production
```

### `gh-orphaned-prs`
Find merged PRs that contain commits not present in the target branch.

**Usage:**
```bash
gh-orphaned-prs <target> [branch] [options]
```

**Arguments:**
- `target` - Organization (e.g., `myorg`) or specific repository (`myorg/myrepo`)
- `branch` - Target branch name (optional, defaults to default branch)

**Options:**
- `--start-date YYYY-MM-DD` - Start date for PR merge window
- `--end-date YYYY-MM-DD` - End date for PR merge window
- `--reopen` - Recreate orphaned PRs with the same source/target branches
- `--token TOKEN` - GitHub personal access token

**Examples:**
```bash
# Find orphaned PRs in all repos in an organization
gh-orphaned-prs myorg

# Find orphaned PRs in a specific repo targeting main branch
gh-orphaned-prs myorg/myrepo main

# Find orphaned PRs merged in the last month
gh-orphaned-prs myorg --start-date 2023-12-01 --end-date 2023-12-31

# Find and automatically reopen orphaned PRs
gh-orphaned-prs myorg/myrepo main --reopen
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
- ✅ Supports both organization and single repository targets
- ✅ Flexible branch comparison syntax
- ✅ Rate limiting handling
- ✅ Clear output with repository URLs
- ✅ Automatic default branch detection

### `gh-orphaned-prs`
- ✅ Fast PR discovery using GitHub Search API
- ✅ Concurrent commit checking for performance
- ✅ Date range filtering for targeted analysis
- ✅ Automatic PR recreation with `--reopen` flag
- ✅ Detailed output with merge dates and branch info
- ✅ Organization-wide or single repository analysis

## Use Cases

### Branch Management
- Identify repositories with unmerged feature branches
- Find branches that need to be merged or cleaned up
- Monitor branch synchronization across organizations

### PR Analysis
- Detect merged PRs whose commits didn't make it to target branches
- Identify potential git workflow issues
- Clean up orphaned work by reopening PRs

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
