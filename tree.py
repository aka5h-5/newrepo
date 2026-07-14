from langgraph.graph import StateGraph, START, END

from state import AgentState
from input import load_run_input

from node1 import fetch_pr_data
from node3 import getDiff
from node2 import loadPrimaryRepoContents
from node4 import diff_intelligence
from node5 import analyzePrimaryAST
from node6 import loadRelevantContextRepoContents
from node7 import analyzeContextAST
from node8_dependency import dependencyAnalyzer
from node9_impact import impact_analyzer
from navgraph import navigation_graph_node
from node8 import requirement_discovery
from node9 import test_plan_generator
from node10 import test_code_generator


builder = StateGraph(AgentState)

builder.add_node("load_run_input", load_run_input)
builder.add_node("get_pr_details", fetch_pr_data)
builder.add_node("get_diff", getDiff)
builder.add_node("load_primary_repo_contents", loadPrimaryRepoContents)
builder.add_node("diff_intelligence", diff_intelligence)
builder.add_node("analyze_primary_ast", analyzePrimaryAST)
builder.add_node("load_relevant_context_repo_contents", loadRelevantContextRepoContents)
builder.add_node("analyze_context_ast", analyzeContextAST)
builder.add_node("dependency_analyzer", dependencyAnalyzer)
builder.add_node("impact_analyzer", impact_analyzer)
builder.add_node("build_nav_graph", navigation_graph_node)
builder.add_node("requirement_discovery", requirement_discovery)
builder.add_node("test_plan_generator", test_plan_generator)
builder.add_node("test_code_generator", test_code_generator)

builder.add_edge(START, "load_run_input")
builder.add_edge("load_run_input", "get_pr_details")
builder.add_edge("get_pr_details", "get_diff")
builder.add_edge("get_diff", "load_primary_repo_contents")
builder.add_edge("load_primary_repo_contents", "diff_intelligence")
builder.add_edge("diff_intelligence", "analyze_primary_ast")
builder.add_edge("analyze_primary_ast", "load_relevant_context_repo_contents")
builder.add_edge("load_relevant_context_repo_contents", "analyze_context_ast")
builder.add_edge("analyze_context_ast", "dependency_analyzer")
builder.add_edge("dependency_analyzer", "impact_analyzer")
builder.add_edge("impact_analyzer", "build_nav_graph")
builder.add_edge("build_nav_graph", "requirement_discovery")
builder.add_edge("requirement_discovery", "test_plan_generator")
builder.add_edge("test_plan_generator", "test_code_generator")
builder.add_edge("test_code_generator", END)

graph = builder.compile()


if __name__ == "__main__":
    result = graph.invoke(AgentState())

    print("\n=== Changed Files ===")
    for file_path in result.get("changed_files", []):
        print("-", file_path)

    print("\n=== Diff Intelligence ===")
    if result.get("analysis"):
        print(result["analysis"].change_summary)

    print("\n=== Requirements ===")
    print(result.get("requirements"))

    print("\n=== Test Plan ===")
    print(result.get("test_plan_path"))

    print("\n=== Generated Tests ===")
    print(result.get("generated_tests"))
