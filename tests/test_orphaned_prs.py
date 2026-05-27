"""Tests for gh_orphaned_prs sorting and grouping logic."""

from unittest import mock

import gh_orphaned_prs


def _pr(number, title, merged_at, login, repository, target_branch="main"):
    return {
        "number": number,
        "title": title,
        "merged_at": merged_at,
        "user": {"login": login},
        "repository": repository,
        "target_branch": target_branch,
    }


SAMPLE = [
    _pr(3, "Banana fix", "2024-03-01", "alice", "repo-b", target_branch="dev"),
    _pr(1, "apple change", "2024-05-01", "carol", "repo-a", target_branch="main"),
    _pr(2, "Cherry tweak", "2024-01-01", "bob", "repo-a", target_branch="main"),
]


class TestSortPrs:
    def test_sort_by_merged_is_newest_first(self):
        result = gh_orphaned_prs.sort_prs(SAMPLE, "merged")
        assert [p["number"] for p in result] == [1, 3, 2]

    def test_sort_by_title_is_case_insensitive(self):
        result = gh_orphaned_prs.sort_prs(SAMPLE, "title")
        assert [p["title"] for p in result] == ["apple change", "Banana fix", "Cherry tweak"]

    def test_sort_by_author(self):
        result = gh_orphaned_prs.sort_prs(SAMPLE, "author")
        assert [p["user"]["login"] for p in result] == ["alice", "bob", "carol"]

    def test_sort_by_number(self):
        result = gh_orphaned_prs.sort_prs(SAMPLE, "number")
        assert [p["number"] for p in result] == [1, 2, 3]

    def test_unknown_order_returns_input_unchanged(self):
        result = gh_orphaned_prs.sort_prs(SAMPLE, "bogus")
        assert result == SAMPLE


class TestGroupPrs:
    def test_group_none_returns_single_empty_key(self):
        result = gh_orphaned_prs.group_prs(SAMPLE, "none")
        assert result == {"": SAMPLE}

    def test_group_by_repo(self):
        result = gh_orphaned_prs.group_prs(SAMPLE, "repo")
        assert set(result) == {"repo-a", "repo-b"}
        assert {p["number"] for p in result["repo-a"]} == {1, 2}

    def test_group_by_target(self):
        result = gh_orphaned_prs.group_prs(SAMPLE, "target")
        assert set(result) == {"main", "dev"}
        assert [p["number"] for p in result["dev"]] == [3]

    def test_group_by_author_missing_login_is_unknown(self):
        prs = [{"number": 9, "user": {}}]
        result = gh_orphaned_prs.group_prs(prs, "author")
        assert "Unknown" in result


def _merged_pr(merge_commit="abc123", base="main"):
    return {
        "number": 7,
        "title": "Some change",
        "html_url": "https://github.com/o/r/pull/7",
        "merged_at": "2024-05-01T00:00:00Z",
        "base": {"ref": base},
        "head": {"ref": "feature"},
        "user": {"login": "alice"},
        "merge_commit": merge_commit,
    }


class TestCheckPrOrphaned:
    def test_merge_commit_present_is_not_orphaned(self):
        with mock.patch.object(gh_orphaned_prs, "is_commit_in_branch", return_value=True) as in_branch:
            result = gh_orphaned_prs.check_pr_orphaned("o", "r", _merged_pr())
        assert result is None
        # Detection checks the single merge commit, not each PR commit.
        in_branch.assert_called_once_with("o", "r", "abc123", "main")

    def test_merge_commit_missing_is_orphaned(self):
        with mock.patch.object(gh_orphaned_prs, "is_commit_in_branch", return_value=False):
            result = gh_orphaned_prs.check_pr_orphaned("o", "r", _merged_pr())
        assert result is not None
        assert result["number"] == 7
        assert result["target_branch"] == "main"
        assert result["merge_commit"] == "abc123"

    def test_missing_merge_commit_is_skipped(self):
        # A null mergeCommit (e.g. squash where data is unavailable) must not
        # produce a false positive.
        with mock.patch.object(gh_orphaned_prs, "is_commit_in_branch") as in_branch:
            result = gh_orphaned_prs.check_pr_orphaned("o", "r", _merged_pr(merge_commit=None))
        assert result is None
        in_branch.assert_not_called()

    def test_head_branch_override_is_checked(self):
        with mock.patch.object(gh_orphaned_prs, "is_commit_in_branch", return_value=True) as in_branch:
            gh_orphaned_prs.check_pr_orphaned("o", "r", _merged_pr(), head_branch="release")
        in_branch.assert_called_once_with("o", "r", "abc123", "release")


def _display_pr(number, title, login, repository, source, target, merged_at):
    return {
        "number": number,
        "title": title,
        "user": {"login": login},
        "repository": repository,
        "source_branch": source,
        "target_branch": target,
        "merged_at": merged_at,
    }


class TestDisplayPrGroup:
    def test_repo_column_shown_when_requested(self, capsys):
        prs = [_display_pr(5, "Fix", "alice", "org/repo-a", "feat", "main", "2024-01-02T00:00:00Z")]
        gh_orphaned_prs.display_pr_group(prs, "", show_repo_column=True)
        header = capsys.readouterr().out.splitlines()[0]
        assert "REPO" in header
        # Repository is shown as bare name, not org/repo.
        assert "org/repo-a" not in header

    def test_repo_column_hidden_when_not_requested(self, capsys):
        prs = [_display_pr(5, "Fix", "alice", "org/repo-a", "feat", "main", "2024-01-02T00:00:00Z")]
        gh_orphaned_prs.display_pr_group(prs, "", show_repo_column=False)
        out = capsys.readouterr().out
        header = out.splitlines()[0]
        assert "REPO" not in header
        # Merged date is rendered as date only.
        assert "2024-01-02" in out
        assert "T00:00:00Z" not in out
