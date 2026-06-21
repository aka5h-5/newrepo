import re
import networkx as nx
from pathlib import PurePosixPath
from state import AgentState


# -----------------------------
# File groups
# -----------------------------
CODE_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")
STYLE_EXTENSIONS = (".css", ".scss")
TEST_PREFIXES = ("tests/",)
CONFIG_FILES = ("tsconfig.json", "package.json", "vite.config.ts", "rsbuild.config.ts", "Dockerfile")
ASSET_EXTENSIONS = (".png", ".jpg", ".jpeg", ".svg", ".gif")


# -----------------------------
# Regex patterns (expanded)
# -----------------------------
JS_IMPORT_REGEX = re.compile(
    r"""import\s+(?:type\s+)?(?:[\s\S]*?)from\s+['"](.+?)['"]""",
    re.MULTILINE
)

DYNAMIC_IMPORT_REGEX = re.compile(
    r"""import\(\s*['"](.+?)['"]\s*\)"""
)

EXPORT_REGEX = re.compile(
    r"""export\s+(?:\*|\{[\s\S]*?\})\s+from\s+['"](.+?)['"]"""
)

CSS_IMPORT_REGEX = re.compile(
    r"""@import\s+['"](.+?)['"]"""
)


# -----------------------------
# Path resolution helpers
# -----------------------------
def resolve_candidate(base, path):
    return (base / path).as_posix()


def resolve_with_extensions(candidate, all_files):
    for f in all_files:
        if f.startswith(candidate):
            return f
    return None


def resolve_import(current_file, import_path, all_files):
    base = PurePosixPath(current_file).parent

    # relative imports
    if import_path.startswith("."):
        return resolve_with_extensions(resolve_candidate(base, import_path), all_files)

    # alias imports (e.g. @/components/X)
    if import_path.startswith("@/"):
        aliased = "src/" + import_path[2:]
        return resolve_with_extensions(aliased, all_files)

    return None


# -----------------------------
# Node 6 implementation
# -----------------------------
def dependency_analyzer(state: AgentState) -> AgentState:
    """
    Node 6 — Repository Dependency Analyzer (enhanced)

    Detects:
    - runtime imports
    - type-only imports
    - re-exports
    - dynamic imports
    - styles, tests, configs, assets
    """

    graph = nx.DiGraph()
    all_files = set(state.repository_files)

    # add all files as nodes
    for f in all_files:
        graph.add_node(f)

    for file, content in state.repository_contents.items():

        # -------------------------
        # JS / TS imports
        # -------------------------
        if file.endswith(CODE_EXTENSIONS):

            for regex in (JS_IMPORT_REGEX, EXPORT_REGEX, DYNAMIC_IMPORT_REGEX):
                for path in regex.findall(content):
                    resolved = resolve_import(file, path, all_files)
                    if resolved:
                        graph.add_edge(file, resolved, type="import")

            # config references
            for cfg in CONFIG_FILES:
                if cfg in content:
                    graph.add_edge(file, cfg, type="config")

            # asset usage
            for asset in all_files:
                if asset.endswith(ASSET_EXTENSIONS) and asset in content:
                    graph.add_edge(file, asset, type="asset")

        # -------------------------
        # CSS imports
        # -------------------------
        if file.endswith(STYLE_EXTENSIONS):
            for path in CSS_IMPORT_REGEX.findall(content):
                resolved = resolve_import(file, path, all_files)
                if resolved:
                    graph.add_edge(file, resolved, type="style")

        # -------------------------
        # Test dependencies
        # -------------------------
        if file.startswith(TEST_PREFIXES):
            for target in all_files:
                short = target.replace("src/", "").split(".")[0]
                if short and short in content:
                    graph.add_edge(file, target, type="test")

    # -------------------------
    # Serialize
    # -------------------------
    state.dependency_graph = {
        "nodes": list(graph.nodes),
        "edges": [
            {
                "from": u,
                "to": v,
                "type": graph.edges[u, v].get("type", "import")
            }
            for u, v in graph.edges
        ]
    }

    return state
