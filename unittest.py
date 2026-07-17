import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AzureOpenAI

from statenew import AgentState

load_dotenv()


MAX_CHANGED_FILES = 25
MAX_IMPACTED_FILES = 35
MAX_CHANGED_FILE_CHARS = 20000
MAX_IMPACTED_FILE_CHARS = 10000
MAX_CHANGED_TOTAL_CHARS = 240000
MAX_IMPACTED_TOTAL_CHARS = 150000
MAX_GRAPH_CHARS = 12000
MAX_IMPACT_CHARS = 10000


def _truncate(text: str, max_chars: int) -> str:
    value = "" if text is None else str(text)

    if len(value) <= max_chars:
        return value

    return value[:max_chars] + f"\n... [truncated {len(value) - max_chars} chars]"


def _extract_requirements(value) -> list:
    if isinstance(value, list):
        return value

    if isinstance(value, dict):
        requirements = value.get("requirements")
        return requirements if isinstance(requirements, list) else []

    if isinstance(value, str):
        try:
            return _extract_requirements(json.loads(value))
        except json.JSONDecodeError:
            return []

    return []


def _budgeted_file_context(
    files: list[str],
    repository_contents: dict,
    max_files: int,
    max_chars_per_file: int,
    total_budget: int,
) -> dict:
    result = {}
    used = 0

    for file_path in files[:max_files]:
        content = repository_contents.get(file_path)

        if content is None:
            continue

        content = str(content)
        clipped = content[:max_chars_per_file]

        remaining = total_budget - used
        if remaining <= 0:
            break

        if len(clipped) > remaining:
            clipped = clipped[:remaining] + "\n... [truncated due to total prompt budget]"

        result[file_path] = clipped
        used += len(clipped)

    return result


def _parse_json_response(text: str) -> dict:
    value = (text or "").strip()

    if value.startswith("```json"):
        value = value.removeprefix("```json").removesuffix("```").strip()
    elif value.startswith("```"):
        value = value.removeprefix("```").removesuffix("```").strip()

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"test_cases": []}

    return parsed if isinstance(parsed, dict) else {"test_cases": []}


def _azure_client() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=os.getenv("API_KEY") or os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-05-01-preview"),
        azure_endpoint=os.getenv("AZURE_ENDPOINT") or os.getenv("AZURE_OPENAI_ENDPOINT"),
        timeout=120.0,
    )


def _deployment_name() -> str:
    return (
        os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
        or os.getenv("AZURE_OPENAI_DEPLOYMENT")
        or "gpt-4o-mini"
    )


def _build_prompt(
    requirements: list,
    changed_files: dict,
    impacted_files: dict,
    state: AgentState,
) -> str:
    return f"""
You are a Senior QA Engineer.

Generate human-readable test cases for the supplied pull request.

Focus ONLY on tests that validate the code change made by the PR.

Do NOT generate E2E/browser tests.
Do NOT generate automation code.

Return ONLY valid JSON:
{{
  "test_cases": [
    {{
      "id": "TC-REQ-001-UNIT-POS-01",
      "requirement_id": "REQ-001",
      "test_type": "unit-positive",
      "priority": "P0",
      "risk": "medium",
      "title": "Test case title",
      "preconditions": [],
      "test_data": [],
      "steps": [],
      "expected_results": [],
      "cleanup_steps": []
    }}
  ]
}}

====================================================================
MANDATORY COVERAGE
====================================================================

For EVERY requirement generate:

1. unit-positive
2. unit-negative
3. integration
4. regression

Make sure all 4 test types are generated for each requirement.

Generate security ONLY when evidence supports it.

Security evidence includes:
- authentication
- authorization
- permissions
- roles
- access control
- tokens
- protected resources
- user input sanitization
- XSS
- SQL injection
- secrets
- unsafe external calls

Do not generate security tests without evidence.

====================================================================
TEST TYPE RULES
====================================================================

unit-positive:
- Validate the changed function/component/hook/service/module with valid input.
- Focus on the smallest isolated behavior changed by the PR.

unit-negative:
- Validate invalid input, missing input, failure state, rejected dependency, or edge condition.
- Must be grounded in changed code or requirement evidence.

integration:
- Validate interaction between changed code and directly connected dependency.
- Examples:
  - component + hook
  - service + API client
  - API handler + business logic
  - function + repository layer
  - context repo consumer + primary repo contract

regression:
- Validate existing behavior that could break because of the PR.
- Use impacted files, dependency graph, and existing behavior evidence.

security:
- Generate only when supported by evidence.
- Validate security-sensitive behavior affected by the PR.

====================================================================
STEP DETAIL RULES
====================================================================

Keep steps practical and automation-ready.

Recommended:
- Unit tests: 4 to 7 steps.
- Integration tests: 5 to 8 steps.
- Regression tests: 4 to 7 steps.
- Security tests: 5 to 8 steps.

Each step should be atomic:
- setup
- input preparation
- mock/stub setup if needed
- execution
- assertion
- cleanup if needed

Do not over-lengthen simple cases.
Do not use vague steps like "verify functionality".

Good unit example:
1. Initialize the changed utility with valid input.
2. Call the changed function with the prepared value.
3. Capture the returned result.
4. Verify the result contains the expected transformed value.
5. Verify no error is raised.

Good integration example:
1. Mock the dependent API client to return a valid response.
2. Render or initialize the changed service with the mocked dependency.
3. Trigger the behavior that calls the dependency.
4. Verify the dependency is called with the expected request.
5. Verify the returned state uses the dependency response.

====================================================================
GROUNDING RULES
====================================================================

- Every test must be based on supplied evidence.
- Prefer changed files over impacted files.
- Use impacted files to identify integration and regression coverage.
- Do not invent UI labels, routes, messages, APIs, roles, or permissions.
- Do not generate browser navigation steps.
- Do not assume a frontend exists unless evidence supports frontend code, but still do not generate E2E here.

====================================================================
INPUT DATA
====================================================================

Requirements:
{json.dumps(requirements, indent=2)}

Changed File Context:
{json.dumps(changed_files, indent=2)}

Impacted File Context:
{json.dumps(impacted_files, indent=2)}

Diff Intelligence:
{json.dumps(
    getattr(state, "diff_intelligence_new", None)
    or getattr(state, "diff_intelligence", ""),
    indent=2
)}

Repository Technology Information:
{json.dumps(getattr(state, "repo_tech_stack", {}) or {}, indent=2)}

Context Repo Usage Matches:
{json.dumps(getattr(state, "context_usage_matches", {}) or {}, indent=2)}

Dependency Graph:
{_truncate(json.dumps(getattr(state, "dependency_graph", {}) or {}, indent=2), MAX_GRAPH_CHARS)}

Impact Analysis:
{_truncate(json.dumps(getattr(state, "impact_analysis", {}) or {}, indent=2), MAX_IMPACT_CHARS)}
""".strip()


def _format_test_txt(payload: dict) -> str:
    blocks = []

    for test_case in payload.get("test_cases", []):
        if not isinstance(test_case, dict):
            continue

        lines = [
            f"Test Case: {test_case.get('id', '')}",
            f"Requirement: {test_case.get('requirement_id', '')}",
            f"Type: {test_case.get('test_type', '')}",
            f"Priority: {test_case.get('priority', 'P1')}",
            f"Risk: {test_case.get('risk', 'medium')}",
            f"Title: {test_case.get('title', '')}",
            f"Target: {test_case.get('target', '')}",
            "Preconditions:",
        ]

        for item in test_case.get("preconditions", []):
            lines.append(f"- {item}")

        if test_case.get("test_data"):
            lines.append("Test Data:")
            for item in test_case.get("test_data", []):
                lines.append(f"- {item}")

        lines.append("Steps:")
        for index, step in enumerate(test_case.get("steps", []), start=1):
            lines.append(f"{index}. {step}")

        lines.append("Expected Results:")
        for item in test_case.get("expected_results", []):
            lines.append(f"- {item}")

        if test_case.get("cleanup_steps"):
            lines.append("Cleanup Steps:")
            for index, step in enumerate(test_case.get("cleanup_steps", []), start=1):
                lines.append(f"{index}. {step}")

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks).strip() + "\n"


def unit_test_plan_generator(state: AgentState) -> AgentState:
    print("Generating unit/integration/regression/security test plan...")

    requirements = _extract_requirements(getattr(state, "requirements", []))

    if not requirements:
        state.test_plan_payload = {"test_cases": []}
        state.test_plan_path = ""
        print("No requirements found.")
        return state

    repository_contents = state.repository_contents or {}

    source_changed_files = [
        file_path
        for file_path in list(getattr(state, "changed_files", []) or [])
        if ".test." not in file_path.lower()
        and ".spec." not in file_path.lower()
        and "/tests/" not in file_path.lower()
    ]

    changed_files = _budgeted_file_context(
        source_changed_files,
        repository_contents,
        max_files=MAX_CHANGED_FILES,
        max_chars_per_file=MAX_CHANGED_FILE_CHARS,
        total_budget=MAX_CHANGED_TOTAL_CHARS,
    )

    impacted_files = _budgeted_file_context(
        list(getattr(state, "impacted_files", []) or []),
        repository_contents,
        max_files=MAX_IMPACTED_FILES,
        max_chars_per_file=MAX_IMPACTED_FILE_CHARS,
        total_budget=MAX_IMPACTED_TOTAL_CHARS,
    )

    response = _azure_client().chat.completions.create(
        model=_deployment_name(),
        messages=[
            {
                "role": "system",
                "content": "You create human-readable unit, integration, regression, and security QA test cases. Return only valid JSON.",
            },
            {
                "role": "user",
                "content": _build_prompt(
                    requirements=requirements,
                    changed_files=changed_files,
                    impacted_files=impacted_files,
                    state=state,
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=10000,
    )

    payload = _parse_json_response(response.choices[0].message.content or "{}")
    payload.setdefault("test_cases", [])

    output_dir = Path(__file__).resolve().parent.parent / "generated_tests"
    output_dir.mkdir(parents=True, exist_ok=True)

    test_txt_path = output_dir / "unit_test.txt"
    test_txt_path.write_text(_format_test_txt(payload), encoding="utf-8")

    state.test_plan_payload = payload
    state.test_plan_path = str(test_txt_path)

    print(f"Saved test plan to {test_txt_path}")
    return state


def unitTestPlanGenerator(state: AgentState) -> AgentState:
    return unit_test_plan_generator(state)
