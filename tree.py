from langgraph.graph import StateGraph, START, END

from state import AgentState
from node1 import fetch_pr_data
from node2 import loadRepoContents
from node3 import getDiff

builder = StateGraph(AgentState)

builder.add_node("get_pr_details", fetch_pr_data)
builder.add_node("load_repo_contents", loadRepoContents)
builder.add_node("get_diff", getDiff)

builder.add_edge(START, "get_pr_details")
builder.add_edge("get_pr_details", "load_repo_contents")
builder.add_edge("load_repo_contents", "get_diff")
builder.add_edge("get_diff", END)

graph = builder.compile()


if __name__ == "__main__":

    state = AgentState(
        repo_owner = "TS-KAAG",
        repo_name = "visual-test-frontend",
        pr_number =  31
    )

    result = graph.invoke(state)
    print(type(result))

    print("\n=== PR DETAILS ===")
    print(result["pr_data"])

    print("\n=== REPOSITORY FILES ===")
    print(result["repository_contents"])

    print("\n=== CHANGED FILES ===")
    print(result["changed_files"])

    print("\n=== GIT DIFF ===")
    print(result["git_diff"])
