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
