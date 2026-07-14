from state import AgentState
from ast_utils import analyze_files


def analyzeContextAST(state: AgentState) -> AgentState:
    print("Node7: analyzing context AST...")

    state.context_ast_analysis = analyze_files(state.context_repository_contents or {})

    state.ast_analysis = {}
    state.ast_analysis.update(state.primary_ast_analysis or {})
    state.ast_analysis.update(state.context_ast_analysis or {})

    print(f"Node7: context AST files={len(state.context_ast_analysis)}")
    return state
