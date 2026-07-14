import os
import re
from collections import defaultdict

from state import AgentState

ROUTE_PATTERN = re.compile(r'<Route\s+[^>]*path=["\']([^"\']+)["\'][^>]*element=\{<(\w+)', re.DOTALL)
LINK_PATTERN = re.compile(r'(?:to|href)=["\']([^"\']+)["\']')
NAVIGATE_PATTERN = re.compile(r'navigate\(\s*["\']([^"\']+)["\']')


def _page_name(file_path: str) -> str:
    return os.path.splitext(os.path.basename(file_path))[0]


def _extract_routes(repository_contents: dict) -> dict:
    routes = {}
    for file_path, content in (repository_contents or {}).items():
        if not file_path.endswith((".tsx", ".jsx")):
            continue
        for match in ROUTE_PATTERN.finditer(content or ""):
            routes[match.group(1)] = match.group(2)
    return routes


def _edges_from_pattern(repository_contents: dict, routes: dict, pattern: re.Pattern, edge_type: str) -> list[dict]:
    edges = []
    for file_path, content in (repository_contents or {}).items():
        if not file_path.endswith((".tsx", ".jsx")):
            continue

        source = _page_name(file_path)
        for route in pattern.findall(content or ""):
            target = routes.get(route)
            if target:
                edges.append({"from": source, "to": target, "type": edge_type, "route": route, "source_file": file_path})
    return edges


def navigation_graph_node(state: AgentState) -> AgentState:
    print("Building navigation graph...")

    repo = state.repository_contents or {}
    routes = _extract_routes(repo)

    edges = []
    edges.extend(_edges_from_pattern(repo, routes, LINK_PATTERN, "link"))
    edges.extend(_edges_from_pattern(repo, routes, NAVIGATE_PATTERN, "navigate"))

    adjacency = defaultdict(set)
    pages = set(routes.values())

    for edge in edges:
        adjacency[edge["from"]].add(edge["to"])
        pages.add(edge["from"])
        pages.add(edge["to"])

    state.navigation_graph = {
        "routes": [{"path": route, "page": page} for route, page in sorted(routes.items())],
        "pages": sorted(pages),
        "edges": edges,
        "raw": {node: sorted(children) for node, children in adjacency.items()},
    }

    print(f"Navigation graph contains {len(pages)} pages and {len(edges)} edges.")
    return state
