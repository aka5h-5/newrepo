from collections import defaultdict, deque
from state import AgentState


def _dependency_edges(dependency_graph):
    if isinstance(dependency_graph, dict):
        return dependency_graph.get("edges", [])

    if hasattr(dependency_graph, "edges"):
        edges = []
        for u, v, data in dependency_graph.edges(data=True):
            edges.append(
                {
                    "from": u,
                    "to": v,
                    "type": data.get("type") or data.get("edge_type", "import"),
                }
            )
        return edges

    return []


def impact_analyzer(state: AgentState) -> AgentState:
    """
    Node 7 — Impact + Context Analyzer

    Produces a PR-scoped subgraph that includes:
    - Changed files
    - Upstream dependents (impact)
    - Downstream dependencies (context)
    - Related tests
    """

    print("Analyzing impact and context...")

    changed = set(state.changed_files)
    edges = _dependency_edges(state.dependency_graph)

    primary_repo_keys = set()
    context_repo_keys = set()
    for repo_cfg in state.repos:
        owner = repo_cfg.get("owner") or repo_cfg.get("name")
        repo_name = repo_cfg.get("repo")
        if not owner or not repo_name:
            continue
        repo_key = f"{owner}/{repo_name}"
        role = (repo_cfg.get("role") or "primary").lower()
        if role == "primary":
            primary_repo_keys.add(repo_key)
        else:
            context_repo_keys.add(repo_key)

    forward = defaultdict(list)
    reverse = defaultdict(list)

    for e in edges:
        src = e["from"]
        dst = e["to"]
        etype = e.get("type", "import")
        forward[src].append((dst, etype))
        reverse[dst].append((src, etype))

    def is_test_file(path: str) -> bool:
        p = path.lower()
        return (
            "/tests/" in f"/{p}" or "__tests__" in p or ".test." in p
            or ".spec." in p or "test" in p
        )

    # Upstream: files that depend on changed files (reverse traversal).
    upstream = set()
    uq = deque(changed)
    while uq:
        current = uq.popleft()
        for parent, _ in reverse.get(current, []):
            if parent in changed or parent in upstream:
                continue
            upstream.add(parent)
            uq.append(parent)

    # Downstream: files that changed files depend on (forward traversal).
    downstream = set()
    dq = deque(changed)
    while dq:
        current = dq.popleft()
        for child, _ in forward.get(current, []):
            if child in changed or child in downstream:
                continue
            downstream.add(child)
            dq.append(child)

    visited = changed | upstream | downstream

    # Include full context-repo footprint so the impact subgraph reflects
    # dependencies from non-primary repositories in multi-repo analysis.
    graph_nodes = set()
    for e in edges:
        graph_nodes.add(e["from"])
        graph_nodes.add(e["to"])

    def repo_key_for(path: str) -> str:
        parts = path.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return ""

    context_nodes = {
        n for n in graph_nodes
        if repo_key_for(n) in context_repo_keys
    }
    visited |= context_nodes

    edge_seen = set()
    subgraph_edges = []
    for e in edges:
        src = e["from"]
        dst = e["to"]
        if src in visited and dst in visited:
            key = (src, dst, e.get("type", "import"))
            if key in edge_seen:
                continue
            edge_seen.add(key)
            subgraph_edges.append({"from": src, "to": dst, "type": key[2]})

    related_tests = sorted(n for n in visited if is_test_file(n))

    node_roles = {}
    for n in sorted(visited):
        if n in changed:
            node_roles[n] = "changed"
        elif n in related_tests:
            node_roles[n] = "test"
        elif n in upstream:
            node_roles[n] = "upstream"
        elif n in downstream:
            node_roles[n] = "downstream"
        elif n in context_nodes:
            node_roles[n] = "context"
        else:
            node_roles[n] = "related"

    state.impact_analysis = {
        "changed": sorted(changed),
        "upstream": sorted(upstream),
        "downstream": sorted(downstream),
        "impact": sorted(upstream),
        "context": sorted(downstream),
        "related_tests": related_tests,
        "related": sorted((visited - changed) - set(related_tests)),
        "all": sorted(visited),
        "node_roles": node_roles,
    }

    state.impact_subgraph = {
        "nodes": sorted(visited),
        "edges": subgraph_edges,
    }

    return state
