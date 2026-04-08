"""Git integration for the Test Intelligence Platform.

Commits generated .robot files to feature branches named after User Story IDs,
enabling traceability from test to requirement in CI/CD pipelines.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def commit_test_to_branch(
    repo_path: str | Path,
    file_path: str | Path,
    user_story_id: str,
    test_name: str = "Generated Test",
) -> bool:
    """Stage and commit a .robot file to ``feature/{user_story_id}``.

    Creates the branch if it doesn't exist, otherwise checks it out.
    Returns ``True`` on successful commit, ``False`` if anything went wrong
    (missing git repo, dirty index, etc.).  Never raises — logs warnings instead.
    """
    try:
        from git import Repo, InvalidGitRepositoryError, GitCommandNotFound
    except ImportError:
        logger.warning("GitPython is not installed — skipping git commit.")
        return False

    repo_path = Path(repo_path).resolve()
    file_path = Path(file_path).resolve()

    try:
        repo = Repo(repo_path)
    except InvalidGitRepositoryError:
        logger.warning("No git repository at %s — skipping commit.", repo_path)
        return False
    except Exception:
        logger.warning("Could not open git repo at %s.", repo_path, exc_info=True)
        return False

    branch_name = f"feature/{user_story_id}"
    try:
        if branch_name in [ref.name for ref in repo.branches]:
            repo.git.checkout(branch_name)
        else:
            repo.git.checkout("-b", branch_name)
    except Exception:
        logger.warning("Could not create/checkout branch %s.", branch_name, exc_info=True)
        return False

    try:
        rel = file_path.relative_to(repo_path)
    except ValueError:
        rel = file_path
    try:
        repo.index.add([str(rel)])
        repo.index.commit(f"test: add automation for {test_name} [{user_story_id}]")
    except Exception:
        logger.warning("Git commit failed for %s.", rel, exc_info=True)
        return False

    logger.info("Committed %s to branch %s.", rel, branch_name)
    return True


def push_branch_to_remote(
    repo_path: str | Path,
    branch_name: str,
    remote_name: str = "origin",
) -> bool:
    """Push *branch_name* to the named remote.

    Returns ``True`` on success.  Logs a warning and returns ``False`` if the
    remote is missing, credentials fail, or GitPython is unavailable.
    """
    try:
        from git import Repo, InvalidGitRepositoryError
    except ImportError:
        logger.warning("GitPython is not installed — skipping push.")
        return False

    try:
        repo = Repo(Path(repo_path).resolve())
    except (InvalidGitRepositoryError, Exception):
        logger.warning("No git repository at %s — skipping push.", repo_path, exc_info=True)
        return False

    if remote_name not in [r.name for r in repo.remotes]:
        logger.warning("Remote '%s' not found — skipping push.", remote_name)
        return False

    try:
        remote = repo.remotes[remote_name]
        remote.push(branch_name)
        logger.info("Pushed branch %s to %s.", branch_name, remote_name)
        return True
    except Exception:
        logger.warning("Push failed for %s → %s.", branch_name, remote_name, exc_info=True)
        return False


def sync_local_workspace(
    repo_path: str | Path,
    target_branch: str = "main",
    remote_name: str = "origin",
) -> bool:
    """Fetch from *remote_name*, checkout *target_branch*, and pull latest.

    Returns ``True`` on success, ``False`` on any failure.
    """
    try:
        from git import Repo, InvalidGitRepositoryError
    except ImportError:
        logger.warning("GitPython is not installed — skipping sync.")
        return False

    try:
        repo = Repo(Path(repo_path).resolve())
    except (InvalidGitRepositoryError, Exception):
        logger.warning("No git repository at %s — skipping sync.", repo_path, exc_info=True)
        return False

    if remote_name not in [r.name for r in repo.remotes]:
        logger.warning("Remote '%s' not found — skipping sync.", remote_name)
        return False

    try:
        remote = repo.remotes[remote_name]
        remote.fetch()
        repo.git.checkout(target_branch)
        remote.pull(target_branch)
        logger.info("Synced workspace to %s/%s.", remote_name, target_branch)
        return True
    except Exception:
        logger.warning("Sync failed for %s/%s.", remote_name, target_branch, exc_info=True)
        return False
