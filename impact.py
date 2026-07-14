from collections import defaultdict, deque

from state import AgentState


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


def _is_test_file(path: str) -> bool:
    low = path.lower()
    return "/tests/" in f"/{low}" or "__tests__" in low or ".test." in low or ".spec." in low


def impact_analyzer(state: AgentState) -> AgentState:
    print("Node9: building impact graph...")

    changed = set(getattr(state, "primary_changed_files", None) or state.changed_files or [])
    edges = (state.dependency_graph or {}).get("edges", [])

    forward = defaultdict(list)
    reverse = defaultdict(list)

    for edge in edges:
        src = edge.get("from")
        dst = edge.get("to")
        if not src or not dst:
            continue
        forward[src].append(edge)
        reverse[dst].append(edge)

    upstream = set()
    downstream = set()
    context_consumers = set()
    related_tests = set()

    q = deque(changed)
    while q:
        current = q.popleft()
        for edge in reverse.get(current, []):
            parent = edge["from"]
            if parent in changed or parent in upstream:
                continue
            upstream.add(parent)
            if _repo_role(state, _repo_key_for(parent)) != "primary":
                context_consumers.add(parent)
            if _is_test_file(parent):
                related_tests.add(parent)
            q.append(parent)

    q = deque(changed)
    while q:
        current = q.popleft()
        for edge in forward.get(current, []):
            child = edge["to"]
            if child in changed or child in downstream:
                continue
            downstream.add(child)
            if _is_test_file(child):
                related_tests.add(child)
            q.append(child)

    visited = changed | upstream | downstream | context_consumers | related_tests

    subgraph_edges = []
    seen = set()

    for edge in edges:
        src = edge.get("from")
        dst = edge.get("to")
        edge_type = edge.get("type", "import")
        connection = edge.get("connection", "")

        if src not in visited or dst not in visited:
            continue

        key = (src, dst, edge_type, connection)
        if key in seen:
            continue
        seen.add(key)

        subgraph_edges.append({"from": src, "to": dst, "type": edge_type, "connection": connection})

    node_roles = {}
    for node in sorted(visited):
        if node in changed:
            role = "changed_primary"
        elif node in context_consumers:
            role = "context_repo_consumer"
        elif node in related_tests:
            role = "related_test"
        elif node in upstream:
            role = "upstream_dependent"
        elif node in downstream:
            role = "downstream_dependency"
        else:
            role = "related"
        node_roles[node] = role

    state.impacted_files = sorted(visited - changed)
    state.impact_analysis = {
        "changed": sorted(changed),
        "upstream_dependents": sorted(upstream),
        "downstream_dependencies": sorted(downstream),
        "cross_repo_consumers": sorted(context_consumers),
        "related_tests": sorted(related_tests),
        "impact": sorted(upstream | context_consumers | related_tests),
        "context": sorted(downstream),
        "all": sorted(visited),
        "node_roles": node_roles,
    }
    state.impact_subgraph = {
        "nodes": [
            {
                "id": node,
                "repo": _repo_key_for(node),
                "repo_role": _repo_role(state, _repo_key_for(node)),
                "role": node_roles.get(node, "related"),
                "is_test": _is_test_file(node),
            }
            for node in sorted(visited)
        ],
        "edges": subgraph_edges,
    }

    print(f"Node9: impacted files={len(state.impacted_files)}")
    return state
