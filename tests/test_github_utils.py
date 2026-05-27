"""Tests for the shared github_utils helpers."""

from unittest import mock

import pytest

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


class TestFormatTable:
    def test_pads_to_header_width_and_leaves_last_column_unpadded(self):
        lines = github_utils.format_table(
            ["NAME", "AGE"],
            [["al", "30"], ["bob", "5"]],
        )
        # NAME column width is max(len("NAME"), len("bob")) == 4; last column unpadded.
        assert lines == ["NAME  AGE", "al    30", "bob   5"]

    def test_min_width_comes_from_header(self):
        lines = github_utils.format_table(["BRANCH"], [["x"]])
        # Single (last) column is unpadded, so the row is just the value.
        assert lines == ["BRANCH", "x"]

    def test_truncates_with_ellipsis_when_over_max(self):
        lines = github_utils.format_table(
            ["TITLE", "END"],
            [["a-very-long-title-value", "z"]],
            maxs=[10, None],
        )
        assert lines[1].startswith("a-very-lo…")
        assert lines[1].endswith("z")

    def test_last_column_truncated_when_max_set(self):
        lines = github_utils.format_table(["A", "STATUS"], [["1", "x" * 80]], maxs=[None, 5])
        assert lines[1] == "1  xxxx…"

    def test_non_string_cells_are_stringified(self):
        lines = github_utils.format_table(["N"], [[42]])
        assert lines == ["N", "42"]


class TestEnsureGhAvailable:
    def test_passes_when_authenticated(self):
        with mock.patch.object(
            github_utils.subprocess, "run", return_value=mock.Mock(returncode=0),
        ):
            # Should not raise/exit.
            github_utils.ensure_gh_available()

    def test_exits_when_gh_missing(self, capsys):
        with mock.patch.object(
            github_utils.subprocess, "run", side_effect=FileNotFoundError(),
        ):
            with pytest.raises(SystemExit):
                github_utils.ensure_gh_available()
        assert "not installed" in capsys.readouterr().err

    def test_exits_when_not_authenticated(self, capsys):
        with mock.patch.object(
            github_utils.subprocess, "run", return_value=mock.Mock(returncode=1),
        ):
            with pytest.raises(SystemExit):
                github_utils.ensure_gh_available()
        assert "not authenticated" in capsys.readouterr().err


class TestResolveTargets:
    def test_specific_repo_flag(self):
        org, repos, wildcard = github_utils.resolve_targets("owner/repo")
        assert (org, repos, wildcard) == ("owner", ["repo"], False)

    def test_wildcard_flag_expands_org(self):
        with mock.patch.object(
            github_utils, "get_organization_repos", return_value=["r1", "r2"],
        ) as get_repos:
            org, repos, wildcard = github_utils.resolve_targets("owner/*")
        assert (org, repos, wildcard) == ("owner", ["r1", "r2"], True)
        get_repos.assert_called_once_with("owner")

    def test_positional_org_target_expands(self):
        with mock.patch.object(github_utils, "get_organization_repos", return_value=["r1"]):
            org, repos, wildcard = github_utils.resolve_targets(None, "myorg")
        assert (org, repos, wildcard) == ("myorg", ["r1"], True)

    def test_positional_repo_target(self):
        org, repos, wildcard = github_utils.resolve_targets(None, "myorg/myrepo")
        assert (org, repos, wildcard) == ("myorg", ["myrepo"], False)

    def test_falls_back_to_current_repository(self):
        with mock.patch.object(
            github_utils, "get_current_repository", return_value="me/here",
        ):
            org, repos, wildcard = github_utils.resolve_targets(None, None)
        assert (org, repos, wildcard) == ("me", ["here"], False)

    def test_exits_when_no_target_detectable(self):
        with mock.patch.object(github_utils, "get_current_repository", return_value=None):
            with pytest.raises(SystemExit):
                github_utils.resolve_targets(None, None)


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
