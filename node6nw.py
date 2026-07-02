from pathlib import PurePosixPath
import fnmatch
import re

import networkx as nx

from state import AgentState


def dependencyAnalyzer(state: AgentState) -> AgentState:
    print("Analyzing dependencies...")

    ALLOWED_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx"}
    IGNORE_DIRS = {
        ".git", ".github", "node_modules", "dist", "build", "coverage",
        "__pycache__", ".venv", "venv", ".next", ".turbo", ".idea", ".vscode",
    }
    IGNORE_FILE_PATTERNS = {
        "*.min.js", "*.map", "*.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        "*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "*.ico",
        "*.md", "*.txt", "*.json", "*.yaml", "*.yml", "*.toml", "*.ini",
    }

    js_import_regex = re.compile(
        r"""import\s+(?:type\s+)?(?:[\s\S]*?)from\s+['\"](.+?)['\"]""",
        re.MULTILINE,
    )
    js_export_from_regex = re.compile(
        r"""export\s+(?:\*|\{[\s\S]*?\})\s+from\s+['\"](.+?)['\"]""",
        re.MULTILINE,
    )
    js_dynamic_import_regex = re.compile(r"""import\(\s*['\"](.+?)['\"]\s*\)""")
    js_require_regex = re.compile(r"""require\(\s*['\"](.+?)['\"]\s*\)""")

    def is_useful_source_file(path: str) -> bool:
        p = PurePosixPath(path)
        if any(part in IGNORE_DIRS for part in p.parts):
            return False
        if any(fnmatch.fnmatch(p.name, pat) for pat in IGNORE_FILE_PATTERNS):
            return False
        return p.suffix.lower() in ALLOWED_EXTENSIONS

    def is_test_file(path: str) -> bool:
        lower_path = path.lower()
        name = PurePosixPath(path).name.lower()
        return "/tests/" in f"/{lower_path}" or "test" in name or "spec" in name

    def split_repo_root(path: str) -> tuple[str, str]:
        p = PurePosixPath(path)
        parts = list(p.parts)
        if len(parts) >= 3:
            return f"{parts[0]}/{parts[1]}", "/".join(parts[2:])
        return "", path

    def normalize_posix(path: str) -> str:
        parts = []
        for part in path.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(part)
        return "/".join(parts)

    def resolve_with_extensions(candidate: str, files: set[str]) -> str | None:
        if candidate in files:
            return candidate
        for ext in ALLOWED_EXTENSIONS:
            cand = candidate + ext
            if cand in files:
                return cand
        for ext in ALLOWED_EXTENSIONS:
            cand = candidate.rstrip("/") + "/index" + ext
            if cand in files:
                return cand
        return None

    def resolve_import(current_file: str, import_path: str, files: set[str]) -> str | None:
        repo_root, rel_current = split_repo_root(current_file)
        base = PurePosixPath(rel_current).parent

        if import_path.startswith("."):
            rel = normalize_posix((base / import_path).as_posix())
            if repo_root:
                scoped = f"{repo_root}/{rel}"
                resolved = resolve_with_extensions(scoped, files)
                if resolved:
                    return resolved
            return resolve_with_extensions(rel, files)

        if import_path.startswith("@/"):
            rel = normalize_posix("src/" + import_path[2:])
            if repo_root:
                scoped = f"{repo_root}/{rel}"
                resolved = resolve_with_extensions(scoped, files)
                if resolved:
                    return resolved
            return resolve_with_extensions(rel, files)

        rel = normalize_posix(import_path)
        if repo_root:
            scoped = f"{repo_root}/{rel}"
            resolved = resolve_with_extensions(scoped, files)
            if resolved:
                return resolved
        return resolve_with_extensions(rel, files)

    raw_ast = state.ast_analysis or {}
    ast_files = raw_ast.get("files", raw_ast)

    function_index: dict[str, set[str]] = {}
    for ast_path, ast in ast_files.items():
        for func in ast.get("Functions", []) + ast.get("functions", []):
            function_index.setdefault(func, set()).add(ast_path)

    graph = nx.MultiDiGraph()

    useful_files = [f for f in state.repository_contents.keys() if is_useful_source_file(f)]
    useful_files_set = set(useful_files)

    for item in useful_files:
        graph.add_node(item, type="file", name=item.split("/")[-1])

    seen_edges = set()

    def add_edge(src: str, dst: str, edge_type: str, connection: str) -> None:
        key = (src, dst, edge_type, connection)
        if key in seen_edges:
            return
        seen_edges.add(key)
        graph.add_edge(src, dst, connection=connection, edge_type=edge_type)

    for item in useful_files:
        ast = ast_files.get(item) or {}

        for func in ast.get("Function Calls", []) + ast.get("function_calls", []):
            targets = function_index.get(func, set())
            if len(targets) == 1:
                target = next(iter(targets))
                if target != item and target in useful_files_set:
                    edge_type = "test" if is_test_file(item) or is_test_file(target) else "call"
                    add_edge(item, target, edge_type, f"calls - {func}")

        for imp in ast.get("Imports", []) + ast.get("imports", []):
            target = imp if imp in useful_files_set else resolve_import(item, imp, useful_files_set)
            if target and target in useful_files_set:
                edge_type = "test" if is_test_file(item) or is_test_file(target) else "import"
                add_edge(item, target, edge_type, f"import - {target.split('/')[-1]}")

        if item.endswith((".ts", ".tsx", ".js", ".jsx")):
            content = state.repository_contents.get(item, "")
            for regex in (js_import_regex, js_export_from_regex, js_dynamic_import_regex, js_require_regex):
                for raw_path in regex.findall(content):
                    target = resolve_import(item, raw_path, useful_files_set)
                    if target and target in useful_files_set and target != item:
                        edge_type = "test" if is_test_file(item) or is_test_file(target) else "import"
                        add_edge(item, target, edge_type, f"import - {target.split('/')[-1]}")

    state.dependency_graph = {
        "nodes": list(graph.nodes()),
        "edges": [
            {
                "from": u,
                "to": v,
                "type": data.get("edge_type", "import"),
                "connection": data.get("connection", ""),
            }
            for u, v, _, data in graph.edges(keys=True, data=True)
        ],
    }

    impacted = set(state.changed_files)
    for changed in state.changed_files:
        for _, dst, _, _ in graph.out_edges(changed, keys=True, data=True):
            impacted.add(dst)
        for src, _, _, _ in graph.in_edges(changed, keys=True, data=True):
            impacted.add(src)
    state.impacted_files = sorted(impacted)

    return state
