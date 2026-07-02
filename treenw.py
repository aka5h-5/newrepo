from langgraph.graph import StateGraph, START, END
import matplotlib.pyplot as plt
import networkx as nx
from dotenv import load_dotenv

load_dotenv()

from state import AgentState
from node1 import fetch_pr_data
from node2 import loadRepoContents
from node3 import getDiff
from node4 import diff_intelligence
from node5 import analyzeAST
from node6 import dependencyAnalyzer as dependency_analyzer
from node7 import impact_analyzer
from node8 import requirement_discovery
from node9 import test_generator


# ------------------------------------------------------
# Build LangGraph (EXECUTION TREE)
# ------------------------------------------------------

builder = StateGraph(AgentState)

builder.add_node("get_pr_details", fetch_pr_data)
builder.add_node("load_repo_contents", loadRepoContents)
builder.add_node("get_diff", getDiff)
builder.add_node("diff_intelligence", diff_intelligence)
builder.add_node("analyzeAST", analyzeAST)
builder.add_node("dependency_analyzer", dependency_analyzer)
builder.add_node("impact_analyzer", impact_analyzer)
builder.add_node("requirement_discovery", requirement_discovery)
builder.add_node("test_generator", test_generator)

builder.add_edge(START, "get_pr_details")
builder.add_edge("get_pr_details", "load_repo_contents")
builder.add_edge("load_repo_contents", "get_diff")
builder.add_edge("get_diff", "diff_intelligence")
builder.add_edge("diff_intelligence", "analyzeAST")
builder.add_edge("analyzeAST", "dependency_analyzer")
builder.add_edge("dependency_analyzer", "impact_analyzer")
builder.add_edge("impact_analyzer", "requirement_discovery")
builder.add_edge("requirement_discovery", "test_generator")
builder.add_edge("test_generator", END)

graph = builder.compile()


# ------------------------------------------------------
# Visualization Helpers
# ------------------------------------------------------

EDGE_COLORS = {
    "import": "black",
    "py-import": "gray",
    "call": "red",
    "style": "blue",
    "test": "green",
    "config": "orange",
    "asset": "purple",
}

NODE_ROLE_COLORS = {
    "changed": "#ff4444",
    "upstream": "#4444ff",
    "downstream": "#ff9900",
    "test": "#44aa44",
    "context": "#17a2b8",
    "related": "#aaaaaa",
}

TEST_HINTS = ("/tests/", "__tests__", ".test.", ".spec.", "test_", "_test.")


def _is_test_file(path: str) -> bool:
    p = "/" + path.lower()
    return any(h in p for h in TEST_HINTS)


def _result_dependency_edges(result):
    dg = result.get("dependency_graph")

    if isinstance(dg, dict):
        return dg.get("edges", [])

    if hasattr(dg, "edges"):
        edges = []
        for u, v, data in dg.edges(data=True):
            edges.append(
                {
                    "from": u,
                    "to": v,
                    "type": data.get("type") or data.get("edge_type", "import"),
                }
            )
        return edges

    return []


def _normalize_or_build_roles(result, graph_nodes):
    """
    Returns node -> normalized role map.
    Handles:
    - new Node 7: node_roles with changed/upstream/downstream/test/related
    - old Node 7 labels: impact/context
    - missing node_roles entirely (builds roles from changed/upstream/downstream lists)
    """
    impact = result.get("impact_analysis", {})

    role_alias = {
        "impact": "upstream",
        "context": "downstream",
    }

    raw_roles = impact.get("node_roles", {}) or {}
    roles = {n: role_alias.get(r, r) for n, r in raw_roles.items()}

    if roles:
        for n in list(roles.keys()):
            if roles[n] not in NODE_ROLE_COLORS:
                roles[n] = "related"
        return roles

    changed = set(impact.get("changed", []))
    upstream = set(impact.get("upstream", [])) | set(impact.get("impact", []))
    downstream = set(impact.get("downstream", [])) | set(impact.get("context", []))

    built = {}
    for n in graph_nodes:
        if n in changed:
            built[n] = "changed"
        elif _is_test_file(n):
            built[n] = "test"
        elif n in upstream:
            built[n] = "upstream"
        elif n in downstream:
            built[n] = "downstream"
        else:
            built[n] = "related"

    return built


def visualize_dependency_graph(result):
    G = nx.DiGraph()

    for e in _result_dependency_edges(result):
        G.add_edge(e["from"], e["to"], type=e.get("type", "import"))

    if not G.edges:
        print("No dependency edges to visualize.")
        return

    plt.figure(figsize=(18, 14))
    pos = nx.spring_layout(G, seed=42, k=3.5, iterations=300)

    nx.draw_networkx_nodes(
        G,
        pos,
        node_size=2600,
        node_color="#b3d9e6",
        edgecolors="#333333",
    )

    labels = {n: n.replace("src/", "") for n in G.nodes}
    nx.draw_networkx_labels(G, pos, labels=labels, font_size=9)

    for edge_type, color in EDGE_COLORS.items():
        typed_edges = [
            (u, v) for u, v, d in G.edges(data=True)
            if d.get("type") == edge_type
        ]
        if typed_edges:
            nx.draw_networkx_edges(
                G,
                pos,
                edgelist=typed_edges,
                edge_color=color,
                width=2,
                arrows=True,
                arrowsize=18,
            )

    legend_handles = [
        plt.Line2D([0], [0], color=color, linewidth=2, label=etype)
        for etype, color in EDGE_COLORS.items()
    ]
    plt.legend(handles=legend_handles, loc="upper left", fontsize=8)

    plt.title("Dependency Graph (Node 6)", fontsize=16, fontweight="bold")
    plt.axis("off")
    plt.show()


def visualize_impact_subgraph(result):
    G = nx.DiGraph()

    for e in result["impact_subgraph"]["edges"]:
        G.add_edge(e["from"], e["to"], type=e.get("type", "import"))

    if not G.edges:
        print("No impact edges to visualize.")
        return

    plt.figure(figsize=(14, 10))
    pos = nx.spring_layout(G, seed=42, k=2.5)

    roles = _normalize_or_build_roles(result, G.nodes)
    node_colors = [
        NODE_ROLE_COLORS.get(roles.get(n, "related"), "#aaaaaa")
        for n in G.nodes
    ]

    nx.draw_networkx_nodes(
        G,
        pos,
        node_size=2600,
        node_color=node_colors,
        edgecolors="#333333",
    )
    nx.draw_networkx_labels(G, pos, font_size=9)
    nx.draw_networkx_edges(G, pos, arrows=True, arrowsize=18, width=2)

    legend_handles = [
        plt.Line2D(
            [0], [0],
            marker="o",
            color="w",
            markerfacecolor=color,
            markersize=12,
            label=role,
        )
        for role, color in NODE_ROLE_COLORS.items()
    ]
    plt.legend(handles=legend_handles, loc="upper left", fontsize=8)

    plt.title("Impact + Context Subgraph (Node 7)", fontsize=16, fontweight="bold")
    plt.axis("off")
    plt.show()


# ------------------------------------------------------
# Run Graph
# ------------------------------------------------------

if __name__ == "__main__":
    state = AgentState(
        repos=[
            {
                "owner": "TS-KAAG",
                "repo": "visual-test-frontend",
                "role": "primary",
                "pr_number": 49,
            },
            {
                "owner": "TS-KAAG",
                "repo": "ts-titanium-platform-backend",
                "role": "context",
            },
        ],
    )

    result = graph.invoke(state)

    print(type(result))

    print("\n=== CHANGED FILES ===")
    for f in result["changed_files"]:
        print("-", f)

    print("\n=== NODE 6 DEPENDENCY GRAPH ===")
    for e in _result_dependency_edges(result):
        print(f'{e["from"]}  --->  {e["to"]}  ({e.get("type", "import")})')

    visualize_dependency_graph(result)

    print("\n=== NODE 7 IMPACT + CONTEXT ANALYSIS ===")

    print("\nChanged:")
    for f in result["impact_analysis"].get("changed", []):
        print("-", f)

    upstream_list = result["impact_analysis"].get("upstream")
    if upstream_list is None:
        upstream_list = result["impact_analysis"].get("impact", [])

    print("\nUpstream (impact - files that depend on changed):")
    for f in upstream_list:
        print("-", f)

    downstream_list = result["impact_analysis"].get("downstream")
    if downstream_list is None:
        downstream_list = result["impact_analysis"].get("context", [])

    print("\nDownstream (context - files that changed files depend on):")
    for f in downstream_list:
        print("-", f)

    print("\nRelated Tests:")
    for f in result["impact_analysis"].get("related_tests", []):
        print("-", f)

    print("\nAll related:")
    for f in result["impact_analysis"].get("all", []):
        print("-", f)

    print("\n=== NODE 7 IMPACT SUBGRAPH ===")
    for e in result["impact_subgraph"]["edges"]:
        print(f'{e["from"]}  --->  {e["to"]}  ({e["type"]})')

    visualize_impact_subgraph(result)

    if result.get("analysis"):
        print("\n=== ANALYSIS ===")
        print(result["analysis"].change_summary)

    if result.get("requirements"):
        print("\n=== REQUIREMENTS ===")
        print(result["requirements"])

    print("\n=== GENERATED TESTS ===")
    print(result["generated_tests"])

    
    print("\n=== PER-REPO TECH STACK ===")
    for repo_key, tech in (result.get("repo_tech_stack") or {}).items():
        print(f"{repo_key}:")
        print("  Language:", tech.get("language", "Unknown"))
        print("  Framework:", tech.get("framework", "Unknown"))
