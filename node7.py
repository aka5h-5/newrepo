from collections import defaultdict, deque
from state import AgentState


def impact_analyzer(state: AgentState) -> AgentState:
    """
    Node 7 — Impact + Context Analyzer

    Produces a PR-scoped subgraph that includes:
    - Changed files
    - Upstream dependents (impact)
    - Downstream dependencies (context)
    - Related tests
    """

    changed = set(state.changed_files)
    edges = state.dependency_graph.get("edges", [])

    forward = defaultdict(list)
    reverse = defaultdict(list)

    for e in edges:
        src = e["from"]
        dst = e["to"]
        etype = e.get("type", "import")

        forward[src].append((dst, etype))
        reverse[dst].append((src, etype))

    visited = set(changed)
    subgraph_edges = []

    queue = deque([(f, "changed") for f in changed])

    while queue:
        current, origin = queue.popleft()

        # ✅ UPSTREAM (impact)
        for parent, etype in reverse.get(current, []):
            if parent not in visited:
                visited.add(parent)
                queue.append((parent, "impact"))

            subgraph_edges.append({
                "from": parent,
                "to": current,
                "type": etype
            })

        # ✅ DOWNSTREAM (context)
        for child, etype in forward.get(current, []):
            if child not in visited:
                visited.add(child)
                queue.append((child, "context"))

            subgraph_edges.append({
                "from": current,
                "to": child,
                "type": etype
            })

    state.impact_analysis = {
        "changed": sorted(changed),
        "related": sorted(visited - changed),
        "all": sorted(visited)
    }

    state.impact_subgraph = {
        "nodes": sorted(visited),
        "edges": subgraph_edges
    }

    return state
