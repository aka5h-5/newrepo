from pathlib import PurePosixPath

from dotenv import load_dotenv
from github.GithubException import GithubException

from state import AgentState

load_dotenv()

SOURCE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx"}
CONTEXT_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".toml"}

IGNORE_DIRS = {
    ".git", ".github", "node_modules", "dist", "build", "coverage",
    "__pycache__", ".venv", "venv", ".next", ".turbo", ".idea", ".vscode",
}

PRIMARY_MAX_FILES = 180
MAX_BLOB_BYTES = 250_000
MAX_FILE_CHARS = 80_000


def _split_prefixed_path(path: str) -> tuple[str, str]:
    parts = str(path).replace("\\", "/").split("/")
    if len(parts) >= 3:
        return f"{parts[0]}/{parts[1]}", "/".join(parts[2:])
    return "", path


def _is_ignored(path: str) -> bool:
    return any(part in IGNORE_DIRS for part in PurePosixPath(path).parts)


def _is_primary_useful(path: str) -> bool:
    if _is_ignored(path):
        return False

    p = PurePosixPath(path)
    name = p.name.lower()
    ext = p.suffix.lower()
    low = path.lower()

    important = {
        "readme.md", "package.json", "pyproject.toml", "requirements.txt",
        "tsconfig.json", "vite.config.ts", "next.config.js", "next.config.ts",
    }

    return (
        ext in SOURCE_EXTENSIONS
        or ext in CONTEXT_EXTENSIONS
        or name in important
        or "openapi" in low
        or "swagger" in low
    )


def _priority(path: str, changed_rel_paths: set[str]) -> int:
    low = f"/{path.lower()}"

    if path in changed_rel_paths:
        return 0
    if "package.json" in low or "pyproject" in low or "requirements.txt" in low:
        return 1
    if "/src/" in low or "/app/" in low:
        return 2
    if "/tests/" in low or ".test." in low or ".spec." in low:
        return 3
    return 4


def _detect_language(files: list[str]) -> str:
    counts = {"TypeScript": 0, "JavaScript": 0, "Python": 0}

    for file_path in files:
        low = file_path.lower()
        if low.endswith((".ts", ".tsx")):
            counts["TypeScript"] += 1
        elif low.endswith((".js", ".jsx")):
            counts["JavaScript"] += 1
        elif low.endswith(".py"):
            counts["Python"] += 1

    language = max(counts, key=counts.get)
    return language if counts[language] else "Unknown"


def _detect_framework(files: list[str]) -> str:
    low_files = [f.lower() for f in files]

    if any("next.config" in f for f in low_files):
        return "Next.js"
    if any(f.endswith("angular.json") for f in low_files):
        return "Angular"
    if any(f.endswith(".tsx") for f in low_files):
        return "React"
    return "Unknown"


def loadPrimaryRepoContents(state: AgentState) -> AgentState:
    print("Node2: loading primary repo contents...")

    g = state.github_client
    state.primary_repository_contents = {}
    state.repository_files = []
    state.repo_files_by_repo = {}

    changed_by_repo = {}
    for changed_file in getattr(state, "changed_files", []) or []:
        repo_key, rel_path = _split_prefixed_path(changed_file)
        if repo_key and rel_path:
            changed_by_repo.setdefault(repo_key, set()).add(rel_path)

    repo_tech_stack = {}

    for repo_cfg in state.repos:
        role = (repo_cfg.get("role") or "primary").lower()
        if role != "primary":
            continue

        owner = repo_cfg.get("owner") or repo_cfg.get("name")
        repo_name = repo_cfg["repo"]
        repo_key = f"{owner}/{repo_name}"
        pr_number = repo_cfg.get("pr_number")

        if pr_number is None:
            state.error = f"Primary repo {repo_key} is missing pr_number"
            return state

        repo = g.get_repo(repo_key)
        pr = repo.get_pull(pr_number)
        ref = pr.head.sha

        tree = repo.get_git_tree(ref, recursive=True).tree
        changed_rel_paths = changed_by_repo.get(repo_key, set())

        candidates = []
        state.repo_files_by_repo[repo_key] = []

        for item in tree:
            if item.type != "blob":
                continue

            rel_path = item.path
            prefixed_path = f"{repo_key}/{rel_path}"

            state.repository_files.append(prefixed_path)
            state.repo_files_by_repo[repo_key].append(prefixed_path)

            blob_size = getattr(item, "size", None) or 0
            if blob_size > MAX_BLOB_BYTES:
                continue

            if rel_path not in changed_rel_paths and not _is_primary_useful(rel_path):
                continue

            candidates.append((rel_path, prefixed_path, blob_size))

        candidates.sort(key=lambda item: (_priority(item[0], changed_rel_paths), item[2], item[0]))

        for rel_path, prefixed_path, _ in candidates[:PRIMARY_MAX_FILES]:
            try:
                file_obj = repo.get_contents(rel_path, ref=ref)
                content = file_obj.decoded_content.decode("utf-8", errors="ignore")
                state.primary_repository_contents[prefixed_path] = content[:MAX_FILE_CHARS]
            except (GithubException, UnicodeDecodeError, AttributeError, TypeError, ValueError):
                state.primary_repository_contents[prefixed_path] = ""

        repo_tech_stack[repo_key] = {
            "language": _detect_language(state.repo_files_by_repo[repo_key]),
            "framework": _detect_framework(state.repo_files_by_repo[repo_key]),
            "role": role,
            "loaded_files": len(state.primary_repository_contents),
            "total_files_seen": len(state.repo_files_by_repo[repo_key]),
        }

    state.repository_contents = dict(state.primary_repository_contents)
    state.repo_tech_stack = repo_tech_stack
    state.language = _detect_language(list(state.repository_files or []))
    state.framework = _detect_framework(list(state.repository_files or []))

    print(f"Node2: loaded primary files={len(state.primary_repository_contents)}")
    return state
