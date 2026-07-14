from pydantic import BaseModel, Field
from typing import Dict, List, Optional

class DiffAnalysis(BaseModel):
    change_summary: str = ""
    risk_level: str = ""
    business_impact: str = ""
    testing_areas: List[str]= Field(default_factory = list)

class AgentState(BaseModel):

primary_changed_files: list = []
primary_repository_contents: dict = {}
context_repository_contents: dict = {}
repository_contents: dict = {}
primary_ast_analysis: dict = {}
context_ast_analysis: dict = {}
ast_analysis: dict = {}
primary_symbol_index: dict = {}
context_search_terms: list = []
context_usage_matches: dict = {}
dependency_graph: dict = {}
impact_analysis: dict = {}
impact_subgraph: dict = {}
impacted_files: list = []
navigation_graph: dict = {}
test_plan_payload: dict = {}
test_plan_path: str = ""
generated_tests: dict = {}
diff_intelligence: str = ""
