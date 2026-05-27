"""Tests for gh_prune_branches prunability logic."""

from unittest import mock

import gh_prune_branches


class TestCheckBranchPrunable:
    def test_default_branch_is_never_prunable(self):
        # Should short-circuit without comparing.
        with mock.patch.object(gh_prune_branches, "compare_branches") as compare:
            assert gh_prune_branches.check_branch_prunable("o", "r", "main", "main") is None
            compare.assert_not_called()

    def test_branch_with_no_commits_ahead_is_prunable(self):
        with mock.patch.object(
            gh_prune_branches, "compare_branches",
            return_value={"ahead_by": 0, "behind_by": 12},
        ):
            result = gh_prune_branches.check_branch_prunable("o", "r", "stale", "main")
        assert result == {"branch": "stale", "behind_by": 12, "can_prune": True}

    def test_branch_ahead_is_not_prunable(self):
        with mock.patch.object(
            gh_prune_branches, "compare_branches",
            return_value={"ahead_by": 3, "behind_by": 1},
        ):
            assert gh_prune_branches.check_branch_prunable("o", "r", "feature", "main") is None

    def test_failed_comparison_returns_none(self):
        with mock.patch.object(gh_prune_branches, "compare_branches", return_value=None):
            assert gh_prune_branches.check_branch_prunable("o", "r", "feature", "main") is None
