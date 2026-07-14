from pathlib import PurePosixPath

from dotenv import load_dotenv
from github.GithubException import GithubException

from state import AgentState

load_dotenv()

SOURCE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx"}
IGNORE_DIRS = {
    ".git", ".github", "node_modules", "dist", "build", "coverage",
    "__pycache__", ".venv", "venv", ".next", ".turbo", ".idea", ".vscode",
}

CONTEXT_MAX_MATCHED_FILES = 80
MAX_BLOB_BYTES = 250_000
MAX_FILE_CHARS = 80_000


def _is_ignored(path: str) -> bool:
    return any(part in IGNORE_DIRS for part in PurePosixPath(path).parts)


def _is_candidate_context_file(path: str) -> bool:
    if _is_ignored(path) or PurePosixPath(path).suffix.lower() not in SOURCE_EXTENSIONS:
        return False

    low = f"/{path.lower()}"
    useful_dirs = [
        "/src/", "/app/", "/lib/", "/components/", "/pages/",
        "/routes/", "/services/", "/api/", "/tests/", "/__tests__/",
    ]
    return any(part in low for part in useful_dirs)


def _matches_terms(content: str, terms: list[str]) -> list[str]:
    content_low = (content or "").lower()
    matches = []

    for term in terms:
        value = str(term or "").strip()
        if len(value) >= 3 and value.lower() in content_low:
            matches.append(value)

    return matches


def loadRelevantContextRepoContents(state: AgentState) -> AgentState:
    print("Node6: loading relevant context repo contents...")

    g = state.github_client
    terms = list(getattr(state, "context_search_terms", []) or [])

    state.context_repository_contents = {}
    state.context_usage_matches = {}

    if not terms:
        state.repository_contents = dict(state.primary_repository_contents or {})
        print("Node6: no context search terms found.")
        return state

    for repo_cfg in state.repos:
        role = (repo_cfg.get("role") or "primary").lower()
        if role == "primary":
            continue

        owner = repo_cfg.get("owner") or repo_cfg.get("name")
        repo_name = repo_cfg["repo"]
        repo_key = f"{owner}/{repo_name}"

        repo = g.get_repo(repo_key)
        ref = repo.default_branch
        tree = repo.get_git_tree(ref, recursive=True).tree

        matched_count = 0

        for item in tree:
            if item.type != "blob":
                continue

            rel_path = item.path
            blob_size = getattr(item, "size", None) or 0

            if blob_size > MAX_BLOB_BYTES or not _is_candidate_context_file(rel_path):
                continue

            try:
                file_obj = repo.get_contents(rel_path, ref=ref)
                content = file_obj.decoded_content.decode("utf-8", errors="ignore")
            except (GithubException, UnicodeDecodeError, AttributeError, TypeError, ValueError):
                continue

            matches = _matches_terms(content, terms)
            if not matches:
                continue

            prefixed_path = f"{repo_key}/{rel_path}"
            state.context_repository_contents[prefixed_path] = content[:MAX_FILE_CHARS]
            state.context_usage_matches[prefixed_path] = matches

            matched_count += 1
            if matched_count >= CONTEXT_MAX_MATCHED_FILES:
                break

    state.repository_contents = {}
    state.repository_contents.update(state.primary_repository_contents or {})
    state.repository_contents.update(state.context_repository_contents or {})

    print(f"Node6: loaded context files={len(state.context_repository_contents)}")
    return state
