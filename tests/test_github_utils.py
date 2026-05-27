"""Tests for the shared github_utils helpers."""

from unittest import mock

import github_utils


class TestParseTarget:
    def test_org_only(self):
        assert github_utils.parse_target("myorg") == ("myorg", None)

    def test_org_and_repo(self):
        assert github_utils.parse_target("myorg/myrepo") == ("myorg", "myrepo")

    def test_repo_with_slash_in_name(self):
        # Only the first slash separates org from repo.
        assert github_utils.parse_target("myorg/group/repo") == ("myorg", "group/repo")


class TestParseRepoPattern:
    def test_specific_repo(self):
        assert github_utils.parse_repo_pattern("owner/repo") == ("owner/repo", False)

    def test_wildcard_org(self):
        assert github_utils.parse_repo_pattern("owner/*") == ("owner", True)

    def test_bare_org_is_not_wildcard(self):
        assert github_utils.parse_repo_pattern("owner") == ("owner", False)


def _git_remote(url, returncode=0):
    """Build a fake subprocess result for `git remote get-url origin`."""
    return mock.Mock(returncode=returncode, stdout=url)


class TestGetCurrentRepository:
    def test_ssh_remote(self):
        with mock.patch.object(
            github_utils.subprocess, "run",
            return_value=_git_remote("git@github.com:owner/repo.git\n"),
        ):
            assert github_utils.get_current_repository() == "owner/repo"

    def test_https_remote(self):
        with mock.patch.object(
            github_utils.subprocess, "run",
            return_value=_git_remote("https://github.com/owner/repo.git\n"),
        ):
            assert github_utils.get_current_repository() == "owner/repo"

    def test_https_remote_without_git_suffix(self):
        with mock.patch.object(
            github_utils.subprocess, "run",
            return_value=_git_remote("https://github.com/owner/repo\n"),
        ):
            assert github_utils.get_current_repository() == "owner/repo"

    def test_non_github_remote_returns_none(self):
        with mock.patch.object(
            github_utils.subprocess, "run",
            return_value=_git_remote("git@gitlab.com:owner/repo.git\n"),
        ):
            assert github_utils.get_current_repository() is None

    def test_git_failure_returns_none(self):
        with mock.patch.object(
            github_utils.subprocess, "run",
            return_value=_git_remote("", returncode=128),
        ):
            assert github_utils.get_current_repository() is None

    def test_subprocess_exception_returns_none(self):
        with mock.patch.object(
            github_utils.subprocess, "run", side_effect=OSError("git not found"),
        ):
            assert github_utils.get_current_repository() is None
