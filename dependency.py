from pathlib import PurePosixPath
import re

import networkx as nx

from state import AgentState

SOURCE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx"}


def _repo_key_for(path: str) -> str:
    parts = str(path).replace("\\", "/").split("/")
    return f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else ""


def _repo_role(state: AgentState, repo_key: str) -> str:
    for repo_cfg in state.repos or []:
        owner = repo_cfg.get("owner") or repo_cfg.get("name")
        repo = repo_cfg.get("repo")
        if f"{owner}/{repo}" == repo_key:
            return (repo_cfg.get("role") or "primary").lower()
    return "context"


def _is_source_file(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in SOURCE_EXTENSIONS


def _is_test_file(path: str) -> bool:
    low = path.lower()
    name = PurePosixPath(path).name.lower()
    return "/tests/" in f"/{low}" or "__tests__" in low or ".test." in name or ".spec." in name


def _split_repo_path(path: str) -> tuple[str, str]:
    parts = str(path).replace("\\", "/").split("/")
    if len(parts) >= 3:
        return f"{parts[0]}/{parts[1]}", "/".join(parts[2:])
    return "", path


def _normalize(path: str) -> str:
    parts = []
    for part in str(path).replace("\\", "/").split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        else:
            parts.append(part)
    return "/".join(parts)


def _resolve_with_extensions(candidate: str, files: set[str]) -> str | None:
    if candidate in files:
        return candidate

    for ext in SOURCE_EXTENSIONS:
        if candidate + ext in files:
            return candidate + ext

    for ext in SOURCE_EXTENSIONS:
        index_file = candidate.rstrip("/") + "/index" + ext
        if index_file in files:
            return index_file

    init_file = candidate.rstrip("/") + "/__init__.py"
    if init_file in files:
        return init_file

    return None


def _resolve_import(current_file: str, import_path: str, files: set[str]) -> str | None:
    repo_key, rel_current = _split_repo_path(current_file)
    base = PurePosixPath(rel_current).parent

    if import_path.startswith("."):
        rel = _normalize((base / import_path).as_posix())
        return _resolve_with_extensions(f"{repo_key}/{rel}", files)

    if import_path.startswith("@/"):
        rel = _normalize("src/" + import_path[2:])
        return _resolve_with_extensions(f"{repo_key}/{rel}", files)

    module = import_path.replace(".", "/")
    for candidate in [f"{repo_key}/{module}", f"{repo_key}/src/{module}", f"{repo_key}/app/{module}", f"{repo_key}/lib/{module}"]:
        resolved = _resolve_with_extensions(candidate, files)
        if resolved:
            return resolved

    return None


def dependencyAnalyzer(state: AgentState) -> AgentState:
    print("Node8: building dependency graph...")

    repository_contents = state.repository_contents or {}
    ast_files = state.ast_analysis or {}
    useful_files = [path for path in repository_contents if _is_source_file(path)]
    useful_set = set(useful_files)

    graph = nx.MultiDiGraph()

    for path in useful_files:
        repo_key = _repo_key_for(path)
        graph.add_node(path, repo=repo_key, repo_role=_repo_role(state, repo_key), is_test=_is_test_file(path))

    seen = set()

    def add_edge(src: str, dst: str, edge_type: str, connection: str):
        if not src or not dst or src == dst or src not in useful_set or dst not in useful_set:
            return
        key = (src, dst, edge_type, connection)
        if key in seen:
            return
        seen.add(key)
        graph.add_edge(src, dst, edge_type=edge_type, connection=connection)

    function_index = {}
    for file_path, ast in ast_files.items():
        for func in ast.get("Functions", []) + ast.get("Methods", []):
            if len(func) >= 3:
                function_index.setdefault(func, set()).add(file_path)

    js_regexes = [
        re.compile(r"""import\s+(?:type\s+)?[\s\S]*?\s+from\s+['"](.+?)['"]"""),
        re.compile(r"""export\s+(?:\*|\{[\s\S]*?\})\s+from\s+['"](.+?)['"]"""),
        re.compile(r"""require\(\s*['"](.+?)['"]\s*\)"""),
        re.compile(r"""import\(\s*['"](.+?)['"]\s*\)"""),
    ]

    for file_path in useful_files:
        ast = ast_files.get(file_path, {})
        content = repository_contents.get(file_path, "") or ""

        imports = list(ast.get("Imports", []))
        for regex in js_regexes:
            imports.extend(regex.findall(content))

        for import_path in dict.fromkeys(imports):
            target = import_path if import_path in useful_set else _resolve_import(file_path, import_path, useful_set)
            if target:
                add_edge(
                    file_path,
                    target,
                    "test_import" if _is_test_file(file_path) or _is_test_file(target) else "import",
                    f"imports {import_path}",
                )

        for call in ast.get("Function Calls", []):
            call_name = str(call).split(".")[-1]
            for target in function_index.get(call_name, set()):
                add_edge(
                    file_path,
                    target,
                    "test_call" if _is_test_file(file_path) or _is_test_file(target) else "call",
                    f"calls {call_name}",
                )

    for context_file in (state.context_repository_contents or {}):
        for matched_symbol in (state.context_usage_matches or {}).get(context_file, []):
            for primary_file in (state.primary_symbol_index or {}).get(matched_symbol, []):
                add_edge(context_file, primary_file, "cross_repo_symbol_usage", f"uses primary symbol {matched_symbol}")

    state.dependency_graph = {
        "nodes": [
            {"id": node, "repo": data.get("repo", ""), "repo_role": data.get("repo_role", ""), "is_test": data.get("is_test", False)}
            for node, data in graph.nodes(data=True)
        ],
        "edges": [
            {"from": src, "to": dst, "type": data.get("edge_type", "import"), "connection": data.get("connection", "")}
            for src, dst, _, data in graph.edges(keys=True, data=True)
        ],
    }

    print(f"Node8: dependency nodes={len(state.dependency_graph['nodes'])}, edges={len(state.dependency_graph['edges'])}")
    return state
