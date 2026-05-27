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


def _prunable(branch="stale", repo="o/r"):
    return {"repository": repo, "branch": branch, "default_branch": "main", "behind_by": 2}


def _args(report=False, yes=False, filter=None, repo=None):
    return mock.Mock(report=report, yes=yes, filter=filter, repo=repo)


def _run_main(args, branches, input_value=None):
    """Run main() with all GitHub access and argument parsing mocked out."""
    with mock.patch.object(gh_prune_branches, "ensure_gh_available"), \
         mock.patch.object(gh_prune_branches, "resolve_targets", return_value=("o", ["r"], False)), \
         mock.patch.object(gh_prune_branches, "check_repository_branches", return_value=branches), \
         mock.patch.object(gh_prune_branches, "delete_branch", return_value=(True, "deleted")) as delete, \
         mock.patch.object(gh_prune_branches, "parse_arguments", return_value=args), \
         mock.patch("builtins.input", return_value=input_value) as prompt:
        gh_prune_branches.main()
    return delete, prompt


class TestMainConfirmation:
    def test_declining_prompt_skips_deletion(self, capsys):
        delete, _ = _run_main(_args(), [_prunable()], input_value="n")
        delete.assert_not_called()
        assert "Aborted" in capsys.readouterr().out

    def test_accepting_prompt_deletes(self):
        delete, _ = _run_main(_args(), [_prunable()], input_value="y")
        delete.assert_called_once_with("o", "r", "stale")

    def test_report_mode_never_prompts_or_deletes(self):
        delete, prompt = _run_main(_args(report=True), [_prunable()])
        delete.assert_not_called()
        prompt.assert_not_called()

    def test_yes_flag_deletes_without_prompt(self):
        delete, prompt = _run_main(_args(yes=True), [_prunable()])
        delete.assert_called_once_with("o", "r", "stale")
        prompt.assert_not_called()
