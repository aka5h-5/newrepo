import os
import json
from openai import AzureOpenAI
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


def _truncate(text: str, max_chars: int = 2000) -> str:
    if text is None:
        return ""
    text = str(text)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... [truncated - {len(text) - max_chars} chars omitted]"


def _truncate_dict(d: dict, max_chars_per_item: int = 1200, max_items: int = 8) -> dict:
    truncated = {}
    for i, (k, v) in enumerate(d.items()):
        if i >= max_items:
            truncated["..."] = f"[{len(d) - max_items} more items omitted]"
            break
        truncated[k] = _truncate(v, max_chars_per_item)
    return truncated


def _truncate_list(values: list, max_items: int = 60, max_chars_per_item: int = 240) -> list:
    truncated = []
    for i, value in enumerate(values):
        if i >= max_items:
            truncated.append(f"... [{len(values) - max_items} more items omitted]")
            break
        truncated.append(_truncate(value, max_chars_per_item))
    return truncated


def requirement_discovery(state: AgentState) -> AgentState:
    """
    Node 8 — Requirement Discovery (AI)

    Discovers BUSINESS REQUIREMENTS from:
    - Source code (changed files)
    - Naming conventions & comments
    - Existing tests
    - FULL dependency graph (Node 6)
    - FULL impact subgraph (Node 7)
    - README / docs (if present)
    - Swagger / OpenAPI (if present)

    Output:
    - Human-readable business requirements
    - Same prose style as Node 4
    """

    print("Discovering requirements...")

    # ✅ SAME Azure client as Node 4
    client = AzureOpenAI(
        api_key=os.getenv("API_KEY"),
        api_version="2024-05-01-preview",
        azure_endpoint=os.getenv("AZURE_ENDPOINT")
    )

    # -------------------------------------------------
    # 1. SOURCE CODE (changed files only)
    # -------------------------------------------------
    changed_file_contents = {}
    for f in state.changed_files:
        content = state.repository_contents.get(f)
        if content:
            changed_file_contents[f] = "\n".join(content.splitlines()[:150])

    # -------------------------------------------------
    # 2. FULL DEPENDENCY GRAPH (Node 6)
    # -------------------------------------------------
    dg_edges = _dependency_edges(state.dependency_graph)
    dependency_edges = [
        f"{e['from']} -> {e['to']} ({e['type']})"
        for e in dg_edges
    ]

    # -------------------------------------------------
    # 3. FULL IMPACT SUBGRAPH (Node 7)
    # -------------------------------------------------
    impact_edges = [
        f"{e['from']} -> {e['to']} ({e['type']})"
        for e in state.impact_subgraph.get("edges", [])
    ]
    impacted_file_contents = {
        f: "\n".join(state.repository_contents.get(f, "").splitlines()[:150])
        for f in state.impact_analysis.get("all", [])
        if state.repository_contents.get(f)
    }

    # -------------------------------------------------
    # 4. TESTS (high-confidence requirement signals)
    # -------------------------------------------------
    test_edges = [
        f"{e['from']} -> {e['to']}"
        for e in dg_edges
        if e.get("type") == "test"
    ]

    # -------------------------------------------------
    # 5. README / DOCUMENTATION (if present)
    # -------------------------------------------------
    docs = []
    for f, content in state.repository_contents.items():
        lower_name = f.lower()
        if "/readme" in lower_name or "/docs/" in lower_name or lower_name.startswith(("readme", "docs/")):
            docs.append(f"\n--- DOC: {f} ---\n{content[:1500]}")
    readme_files = docs

    # -------------------------------------------------
    # 6. Swagger / OpenAPI (if present)
    # -------------------------------------------------
    apis = []
    for f, content in state.repository_contents.items():
        name = f.lower()
        if "openapi" in name or "swagger" in name:
            apis.append(f"\n--- API SPEC: {f} ---\n{content[:1500]}")
    swagger_files = apis

    pr_info = state.pr_data
    git_diff = state.git_diff
    repo_tech_stack_map = state.repo_tech_stack if isinstance(state.repo_tech_stack, dict) else {}

    changed_file_contents = _truncate_dict(changed_file_contents, max_chars_per_item=1400, max_items=10)
    impacted_file_contents = _truncate_dict(impacted_file_contents, max_chars_per_item=1000, max_items=20)
    git_diff = _truncate_dict(git_diff, max_chars_per_item=1400, max_items=12)
    readme_files = _truncate_list(readme_files, max_items=6, max_chars_per_item=1500)
    swagger_files = _truncate_list(swagger_files, max_items=4, max_chars_per_item=1500)
    dependency_edges = _truncate_list(dependency_edges, max_items=120, max_chars_per_item=220)
    impact_edges = _truncate_list(impact_edges, max_items=120, max_chars_per_item=220)
    test_edges = _truncate_list(test_edges, max_items=80, max_chars_per_item=220)
    pr_info = _truncate(pr_info, 4000)
    repo_tech_stack_text = _truncate(repo_tech_stack_map, 2000)

    if repo_tech_stack_map:
        language = ", ".join(
            f"{repo}: {tech.get('language', 'Unknown')}"
            for repo, tech in repo_tech_stack_map.items()
        )
        framework = ", ".join(
            f"{repo}: {tech.get('framework', 'Unknown')}"
            for repo, tech in repo_tech_stack_map.items()
        )
    else:
        language = state.language
        framework = state.framework

    # -------------------------------------------------
    # LLM PROMPT
    # -------------------------------------------------
    prompt = f"""You are a requirements analyst.

    Your task is to identify test-ready business requirements from the pull request and repository context.

    Consider the following sources of information to identify requirements:
 
    1. Dependency graph edges (all file relationships):
    {dependency_edges}

    Impact subgraph edges (changed plus related context):
    {impact_edges}
 
    Test edges (files that test other files):
    {test_edges}
 
    2. The pull request description and any associated metadata (e.g., labels, linked issues).
   
    {pr_info}
 
    3.The content of README files and any documentation present in the repository.
 
    {readme_files}
 
    {swagger_files}
 
    4. The content of changed files and the git diff of the pull request.
    Changed Files:
 
    {changed_file_contents}
 
    Git Diff:
 
    {git_diff}
 
    5. The content of impacted components that are affected by the changes.
    Impacted Components:
 
    {impacted_file_contents}
 
    6. The programming language and framework used in the repository, as they may provide context on the types of requirements (e.g., security, performance, user experience) that are relevant.
    Per-repo tech stack:
    {repo_tech_stack_text}

    Language: {language}
    Framework: {framework}
 
    Return structured, test-ready requirements in the following JSON format:
    {{
        "requirements": [
            {{
                "id": "<requirement identifier>",
                "title": "<clear and concise title of the requirement>",
                "description": "<business language>",
                "type": "<functional, validation, error handling, security, regression>",
                "priority": "<priority level of the requirement, e.g., high, medium, low>",
                "sources": "<list of sources that informed this requirement>",
                "risk_level": "<risk level: high, medium, low>",
                "traceability": {{
                    "changed_files": "<list of changed files/functions>",
                    "impacted_components": "<list of impacted components>",
                    "evidence_sources": "<list of evidence sources>"
                }},
                "test_recommendations": "<list of recommended test cases>"
            }}
        ],
        "coverage_gaps": ["<missing existing test coverage>"]
    }}
 
    Rules to follow:
    1. Use the PR description, README files, changed files, git diff, impacted components, and any other relevant information to identify requirements.
    2. Produce machine-readable JSON output that can be used for automated testing and validation.
    3. No free text or explanations outside of the JSON structure. Only return the JSON output.
    4. Ensure that the requirements are clear, concise, and written in business language.
    5. Find as many requirements as possible, including functional, validation, error handling, security, and regression requirements.
    6. Identify any gaps in existing test coverage and include them in the "coverage_gaps" section of the output.
    7. Prefer requirements that are specific enough to drive executable tests with setup, action, and assertion steps.
    8. When multiple repositories are provided, favor the primary repository for the core requirement set and include context-repo dependencies only where they change behavior.
    9. Do not invent requirements that cannot be justified by the provided inputs.
 
    Example Output:
    {{
        "requirements": [
        {{
            "id": "REQ-001",
            "title": "PhonePe payment must succeed for valid UPI transaction",
            "description": "System should authorize and record successful PhonePe payments.",
            "type": "functional",
            "priority": "P0",
            "risk": "high",
            "sources": ["diff", "openapi", "existing_tests"],
            "traceability": {{
                "files": ["payments/service.py"],
                "functions": ["process_phonepe_payment"],
                "impacted_components": ["checkout", "invoice"]
            }},
            "test_recommendations": ["positive", "integration", "regression"]
        }}],
        "coverage_gaps": [
            "No negative test for invalid PhonePe VPA",
            "No refund-flow regression for partial failures"
        ]
    }}
    """

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are an expert Business Analyst."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.2,
        max_tokens=8000,
        response_format={"type": "json_object"},
    )

    content = (response.choices[0].message.content or "").strip()
    try:
        payload = json.loads(content)
        state.requirements = json.dumps(payload, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        state.requirements = content
    print("Requirement discovery complete.")

    return state
