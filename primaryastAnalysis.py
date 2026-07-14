
import json
from pathlib import Path

from state import AgentState
from ast_utils import analyze_files


def analyzePrimaryAST(state: AgentState) -> AgentState:
    print("Node5: analyzing primary AST...")

    contents = state.primary_repository_contents or state.repository_contents or {}
    changed = set(getattr(state, "changed_files", []) or [])

    state.primary_ast_analysis = analyze_files(contents)
    state.primary_symbol_index = {}

    terms = set()

    for file_path, ast in state.primary_ast_analysis.items():
        symbols = []
        symbols.extend(ast.get("Classes", []))
        symbols.extend(ast.get("Functions", []))
        symbols.extend(ast.get("Methods", []))
        symbols.extend(ast.get("Routes", []))

        if file_path in changed:
            terms.add(Path(file_path).stem)

        for symbol in symbols:
            if len(symbol) >= 3:
                state.primary_symbol_index.setdefault(symbol, []).append(file_path)
                if file_path in changed:
                    terms.add(symbol)

        if Path(file_path).name == "package.json":
            try:
                package = json.loads(contents.get(file_path, ""))
                if package.get("name"):
                    terms.add(package["name"])
            except json.JSONDecodeError:
                pass

    for changed_file in changed:
        terms.add(Path(changed_file).stem)

    state.context_search_terms = sorted(t for t in terms if t and len(t) >= 3)
    state.ast_analysis = dict(state.primary_ast_analysis)

    print(f"Node5: primary AST files={len(state.primary_ast_analysis)}")
    print(f"Node5: context search terms={len(state.context_search_terms)}")
    return state
