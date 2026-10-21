"""Installation identity must degrade gracefully outside a Git checkout."""

import subprocess
from types import SimpleNamespace

import pytest

from chefmate_playground.versioning import current_commit


@pytest.mark.parametrize("code,output,expected", [(0, "abc1234\n", "abc1234"), (1, "abc1234", None),
                                               (0, "unexpected text", None), (0, "", None)])
def test_commit_requires_success_and_a_real_hash(monkeypatch, tmp_path, code, output, expected):
    def run(args, **kwargs):
        assert args == ["git", "-C", str(tmp_path), "rev-parse", "--short", "HEAD"]
        assert kwargs["timeout"] == 2
        return SimpleNamespace(returncode=code, stdout=output)

    monkeypatch.setattr(subprocess, "run", run)
    assert current_commit(tmp_path) == expected


@pytest.mark.parametrize("error", [FileNotFoundError(), subprocess.TimeoutExpired("git", 2)])
def test_git_unavailable_does_not_break_about(monkeypatch, tmp_path, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(subprocess, "run", fail)
    assert current_commit(tmp_path) is None
