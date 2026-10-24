"""GitHub REST integration via PyGithub: read source context, open human-reviewed fix PRs.

Least privilege: reading needs `Contents: read`; remediation additionally needs `Contents: write` and
`Pull requests: write` on the allow-listed repositories only. This module never merges, never pushes to the
default branch and never touches `.github/` (workflow files would let a patch escalate CI privileges).
"""

from __future__ import annotations

import logging
import re
import shutil
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

import requests
from github import Auth, Github, GithubException, RateLimitExceededException
from github.Commit import Commit
from github.Repository import Repository

from src.agent.parsing import path_candidates
from src.agent.patching import PatchError, apply_unified_diff, format_code_window
from src.config import settings
from src.errors import ErrorCategory

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 1_000_000  # the contents API only returns inline content up to 1 MB
PR_LABEL = "opspulse-ai"
REVIEW_LABEL = "needs-human-review"
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
FORBIDDEN_PATH_PREFIXES = (".github/",)

# Network-level failures PyGithub can surface; callers degrade gracefully on these.
TRANSPORT_ERRORS = (GithubException, requests.RequestException)


class GitHubError(RuntimeError):
    """A GitHub operation failed. The message is safe to persist (no tokens, no response bodies)."""

    def __init__(self, message: str, category: ErrorCategory = ErrorCategory.GITHUB_UNAVAILABLE) -> None:
        super().__init__(message)
        self.category = category


def api_error(exc: GithubException, prefix: str = "") -> GitHubError:
    """Translate a PyGithub exception into a categorised, body-free GitHubError."""
    if isinstance(exc, RateLimitExceededException) or exc.status == 429:
        category = ErrorCategory.GITHUB_RATE_LIMITED
    elif exc.status in (401, 403, 404):
        category = ErrorCategory.GITHUB_PERMISSION
    else:
        category = ErrorCategory.GITHUB_UNAVAILABLE
    return GitHubError(prefix + describe_github_error(exc), category)


@dataclass(frozen=True)
class SourceContext:
    path: str
    commit_sha: str
    start_line: int
    end_line: int
    failing_line: int
    window_text: str
    recent_commits: list[str]

    def render(self) -> str:
        header = [
            f"FILE: {self.path}",
            f"COMMIT: {self.commit_sha}",
            f"WINDOW: lines {self.start_line}-{self.end_line} (failing line: {self.failing_line})",
        ]
        if self.recent_commits:
            header.append("RECENT COMMITS TOUCHING THIS FILE (14 days):")
            header.extend(f"  - {c}" for c in self.recent_commits)
        header.append("-----")
        return "\n".join(header) + "\n" + self.window_text


@dataclass(frozen=True)
class PullRequestResult:
    pr_url: str
    pr_number: int
    branch: str
    already_existed: bool


def describe_github_error(exc: GithubException) -> str:
    """Map an API error to a short, safe description (never the raw response body)."""
    if isinstance(exc, RateLimitExceededException):
        return "GitHub rate limit exceeded"
    return {
        401: "GitHub rejected the token",
        403: "GitHub token lacks permission (or secondary rate limit)",
        404: "repository or file not found (or token lacks access)",
        409: "GitHub reported a conflict",
        422: "GitHub rejected the request as invalid",
    }.get(exc.status, f"GitHub API error (HTTP {exc.status})")


def is_valid_repo_name(repo_name: str) -> bool:
    return bool(REPO_NAME_RE.match(repo_name or "")) and not any(part in {".", ".."} for part in repo_name.split("/"))


def ensure_repository_allowed(repo_name: str) -> None:
    if not is_valid_repo_name(repo_name):
        raise GitHubError("invalid repository identifier", ErrorCategory.GITHUB_PERMISSION)
    if not settings.is_repository_allowed(repo_name):
        raise GitHubError("repository is not in ALLOWED_REPOSITORIES", ErrorCategory.GITHUB_PERMISSION)


def get_repo(repo_name: str) -> Repository:
    ensure_repository_allowed(repo_name)
    if not settings.github_token:
        raise GitHubError("GITHUB_TOKEN is not configured", ErrorCategory.REMEDIATION_SKIPPED)
    client = Github(auth=Auth.Token(settings.github_token), per_page=100, retry=2, timeout=20)
    try:
        return client.get_repo(repo_name)
    except GithubException as exc:
        raise api_error(exc, "cannot open repository: ") from exc


def resolve_repo_path(repo: Repository, runtime_path: str, ref: str) -> str | None:
    """Map a runtime path (e.g. /app/src/x.py) to a file that really exists in the repository tree."""
    if ".." in runtime_path.replace("\\", "/").split("/"):
        return None
    tree = repo.get_git_tree(ref, recursive=True)
    blobs = [e.path for e in tree.tree if e.type == "blob"]
    blob_set = set(blobs)
    for candidate in path_candidates(runtime_path):
        if candidate in blob_set:
            return candidate
        matches = [p for p in blobs if p.endswith("/" + candidate)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1 and "/" in candidate:
            return min(matches, key=len)
    return None


def _read_text(content_file: Any) -> str:
    if (content_file.size or 0) > MAX_FILE_BYTES:
        raise GitHubError(f"file is larger than {MAX_FILE_BYTES} bytes")
    return str(content_file.decoded_content.decode("utf-8", errors="replace"))


def _recent_commits(repo: Repository, path: str, limit: int = 5) -> list[str]:
    since = datetime.now(UTC) - timedelta(days=14)
    try:
        out: list[str] = []
        commit: Commit
        for commit in repo.get_commits(path=path, since=since)[:limit]:
            when: str = commit.commit.author.date.strftime("%Y-%m-%d")
            subject: str = (commit.commit.message or "").splitlines()[0][:100]
            out.append(f"{commit.sha[:7]} {when} {subject}")
        return out
    except GithubException as exc:
        logger.warning("recent-commit lookup skipped: %s", describe_github_error(exc))
        return []


def fetch_source_context(
    repo_name: str, runtime_path: str, failing_line: int, ref: str | None = None
) -> SourceContext | None:
    """Numbered window of +/- CODE_CONTEXT_RADIUS lines around the failing line, at `ref` (the deployed commit) when
    given, else on the default branch head.

    Returns None when the file cannot be located. Raises GitHubError / TRANSPORT_ERRORS on API failures. A `ref` that
    cannot be read is an error, never a silent fallback to the default branch: the code that failed in production may
    differ from the head, and analysing the wrong code would look like a grounded analysis.
    """
    repo = get_repo(repo_name)
    try:
        if ref:
            try:
                commit_sha = repo.get_commit(ref).sha
            except GithubException as exc:
                raise api_error(exc, f"commit {ref[:12]} could not be read: ") from exc
        else:
            commit_sha = repo.get_branch(repo.default_branch).commit.sha
        path = resolve_repo_path(repo, runtime_path, commit_sha)
        if path is None:
            return None
        content_file = repo.get_contents(path, ref=commit_sha)
    except GithubException as exc:
        raise api_error(exc) from exc
    if isinstance(content_file, list):
        return None
    lines = _read_text(content_file).splitlines()
    if not lines:
        return None
    radius = settings.context_radius
    failing_line = min(max(failing_line, 1), len(lines))
    start, end = max(1, failing_line - radius), min(len(lines), failing_line + radius)
    return SourceContext(
        path=path,
        commit_sha=commit_sha[:12],
        start_line=start,
        end_line=end,
        failing_line=failing_line,
        window_text=format_code_window(lines[start - 1 : end], start),
        recent_commits=_recent_commits(repo, path),
    )


def remediation_branch_name(fingerprint: str) -> str:
    """Deterministic per failure fingerprint, so concurrent or repeated deliveries collide on the branch."""
    return f"opspulse/fix-{fingerprint[:16]}"


def _find_open_pr(repo: Repository, branch: str) -> PullRequestResult | None:
    owner = repo.owner.login
    for pr in repo.get_pulls(state="open", head=f"{owner}:{branch}"):
        return PullRequestResult(pr.html_url, pr.number, branch, already_existed=True)
    return None


def _delete_branch(repo: Repository, branch: str) -> None:
    """Best-effort removal of a branch this service just created; never raises."""
    try:
        repo.get_git_ref(f"heads/{branch}").delete()
    except GithubException as exc:
        logger.warning("could not remove branch %s after a failure: %s", branch, describe_github_error(exc))


def create_fix_pull_request(
    repo_name: str, file_path: str, unified_diff: str, title: str, body: str, fingerprint: str
) -> PullRequestResult:
    """Apply a validated single-file diff on a new branch and open a (draft) PR. Never merges."""
    if file_path.startswith(FORBIDDEN_PATH_PREFIXES) or ".." in file_path.split("/"):
        raise GitHubError("refusing to modify a protected path", ErrorCategory.REMEDIATION_POLICY_VIOLATION)
    repo = get_repo(repo_name)
    branch = remediation_branch_name(fingerprint)
    try:
        existing = _find_open_pr(repo, branch)
        if existing:
            return existing
        base = repo.default_branch
        base_sha = repo.get_branch(base).commit.sha
        current = repo.get_contents(file_path, ref=base_sha)
        if isinstance(current, list):
            raise GitHubError("target path is a directory")
        original = _read_text(current)
        try:
            patched = apply_unified_diff(original, unified_diff)
        except PatchError as exc:
            raise GitHubError(
                f"patch does not apply to the default branch head: {exc}", ErrorCategory.REMEDIATION_POLICY_VIOLATION
            ) from exc
        if patched == original:
            raise GitHubError("patch produces no change")

        try:
            repo.create_git_ref(ref=f"refs/heads/{branch}", sha=base_sha)
        except GithubException as exc:
            if exc.status == 422:  # branch exists: another delivery is handling / handled this fingerprint
                raise GitHubError(
                    f"branch {branch} already exists (concurrent run in progress, or a stale branch to delete)"
                ) from exc
            raise
        try:
            repo.update_file(
                path=file_path,
                message=f"fix: {title[:60]}\n\nProposed by OpsPulse AI (fingerprint {fingerprint[:12]}). Needs review.",
                content=patched,
                sha=current.sha,
                branch=branch,
            )
            pr_footer = (
                "\n\n---\n"
                "> 🤖 **OpsPulse AI Automated Fix**\n"
                f"> **Fingerprint:** `{fingerprint[:12]}`\n"
                "> ⚠️ *Please review the proposed diff carefully before merging.*"
            )
            enriched_body = f"{body.strip()}{pr_footer}"

            try:
                pr = repo.create_pull(
                    base=base, head=branch, title=title[:200], body=enriched_body, draft=settings.github_pr_draft
                )
            except GithubException as exc:
                if not (settings.github_pr_draft and exc.status == 422):
                    raise
                # Draft PRs are not available on every plan; fall back to a regular (still unmerged) PR.
                pr = repo.create_pull(base=base, head=branch, title=title[:200], body=enriched_body, draft=False)
        except GithubException:
            _delete_branch(repo, branch)  # do not leave a half-made branch that would block every retry
            raise
    except GithubException as exc:
        raise api_error(exc) from exc
try:
        try:
            repo.get_label(PR_LABEL)
        except GithubException:
            repo.create_label(PR_LABEL, "0e8a16", "Fix proposed by OpsPulse AI")
        try:
            repo.get_label(REVIEW_LABEL)
        except GithubException:
            repo.create_label(REVIEW_LABEL, "fbca04", "Requires manual engineer review before merge")
        pr.add_to_labels(PR_LABEL, REVIEW_LABEL)
    except GithubException as exc:
        logger.warning("could not label PR: %s", describe_github_error(exc))


def download_repository_snapshot(repo_name: str, dest: Path, max_bytes: int) -> str:
    """Extract the default branch head into `dest` (an empty directory) and return its commit SHA.

    Only regular files and directories are extracted (no links, no absolute or `..` paths) and the download and
    the extracted size are both capped, because the archive comes from a repository we do not control.
    """
    repo = get_repo(repo_name)
    try:
        sha = repo.get_branch(repo.default_branch).commit.sha
        url = repo.get_archive_link("tarball", ref=sha)
    except GithubException as exc:
        raise api_error(exc, "cannot fetch repository archive: ") from exc
    archive = dest.parent / f"{dest.name}.tar.gz"
    try:
        with requests.get(url, stream=True, timeout=30) as response:
            response.raise_for_status()
            size = 0
            with archive.open("wb") as out:
                for chunk in response.iter_content(1 << 16):
                    size += len(chunk)
                    if size > max_bytes:
                        raise GitHubError("repository archive exceeds the sandbox size limit")
                    out.write(chunk)
        _extract_regular_files(archive, dest, max_bytes)
    except (requests.RequestException, tarfile.TarError, OSError) as exc:
        raise GitHubError(f"cannot fetch repository archive: {type(exc).__name__}") from exc
    finally:
        archive.unlink(missing_ok=True)
    return sha


def _extract_regular_files(archive: Path, dest: Path, max_bytes: int) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    total = 0
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            parts = PurePosixPath(member.name).parts[1:]  # drop the "<owner>-<repo>-<sha>/" prefix
            if not parts or not (member.isfile() or member.isdir()):
                continue
            if PurePosixPath(member.name).is_absolute() or ".." in parts:
                raise GitHubError("repository archive contains an unsafe path")
            target = root.joinpath(*parts)
            if not target.resolve().is_relative_to(root):
                raise GitHubError("repository archive contains an unsafe path")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            total += member.size
            if total > max_bytes:
                raise GitHubError("repository contents exceed the sandbox size limit")
            source = tar.extractfile(member)
            if source is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as out:
                shutil.copyfileobj(source, out)
