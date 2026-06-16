from pydantic import BaseModel
from typing import Dict, List

class AgentState(BaseModel):
    repo_path: str
    changed_files: List[str] = []

    dependency_graph: Dict[str, List[str]] = {}
    impacted_files: List[str] = []


import os
import ast
import networkx as nx

from state import AgentState


def dependency_analyzer(state: AgentState) -> AgentState:

    repo_path = state.repo_path

    graph = nx.DiGraph()

    python_files = []

    # Collect Python files
    for root, _, files in os.walk(repo_path):
        for file in files:
            if file.endswith(".py"):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, repo_path)

                python_files.append(rel_path)
                graph.add_node(rel_path)

    # Build dependency graph
    for file in python_files:

        full_path = os.path.join(repo_path, file)

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read())

            for node in ast.walk(tree):

                if isinstance(node, ast.Import):
                    for alias in node.names:

                        imported_module = alias.name.replace(".", "/") + ".py"

                        if imported_module in python_files:
                            graph.add_edge(file, imported_module)

                elif isinstance(node, ast.ImportFrom):

                    if node.module:

                        imported_module = (
                            node.module.replace(".", "/") + ".py"
                        )

                        if imported_module in python_files:
                            graph.add_edge(file, imported_module)

        except Exception as e:
            print(f"Error parsing {file}: {e}")

    impacted_files = set()

    # Find impacted files
    for changed_file in state.changed_files:

        if changed_file in graph:

            impacted_files.add(changed_file)

            # Files that depend on changed file
            predecessors = nx.ancestors(graph, changed_file)

            impacted_files.update(predecessors)

    state.dependency_graph = {
        node: list(graph.successors(node))
        for node in graph.nodes()
    }

    state.impacted_files = list(impacted_files)

    return state
