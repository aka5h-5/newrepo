import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AzureOpenAI

from state import AgentState

load_dotenv()


def _extract_requirements(value) -> list:
    if isinstance(value, list):
        return value

    if isinstance(value, dict):
        return value.get("requirements", []) if isinstance(value.get("requirements"), list) else []

    if isinstance(value, str):
        try:
            return _extract_requirements(json.loads(value))
        except json.JSONDecodeError:
            return []

    return []


def _read_full_context_files(files: list[str], repository_contents: dict) -> dict:
    result = {}

    for file_path in files:
        content = repository_contents.get(file_path)
        if content is not None:
            result[file_path] = content

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

Generate human-readable QA test cases for the supplied pull request.
Do NOT generate code.

Return ONLY valid JSON:
{{
  "test_cases": [
    {{
      "id": "TC-REQ-001-E2E-TRUE-01",
      "requirement_id": "REQ-001",
      "test_type": "e2e-true-path",
      "priority": "P0",
      "risk": "medium",
      "title": "Test case title",
      "preconditions": ["Required prerequisite"],
      "steps": ["Step 1", "Step 2"],
      "expected_results": ["Expected result"]
    }}
  ]
}}

Mandatory coverage for EVERY requirement:
- e2e-true-path
- e2e-error-path
- unit-true-path
- unit-error-path

Optional only if supported by evidence:
- integration
- regression
- security

Rules:
- Every test must be grounded in supplied evidence.
- Do not invent UI text, routes, permissions, APIs, or errors.
- Steps must be specific, observable, and executable.
- Expected results must describe exact observable outcomes.
- Use priority values: P0, P1, P2.
- Use risk values: high, medium, low.

Requirements:
{json.dumps(requirements, indent=2)}

Changed File Context:
{json.dumps(changed_files, indent=2)}

Impacted File Context:
{json.dumps(impacted_files, indent=2)}

Diff Intelligence:
{json.dumps(getattr(state, "diff_intelligence", ""), indent=2)}

Dependency Graph:
{json.dumps(getattr(state, "dependency_graph", {}), indent=2)}

Impact Analysis:
{json.dumps(getattr(state, "impact_analysis", {}), indent=2)}

Navigation Graph:
{json.dumps(getattr(state, "navigation_graph", {}), indent=2)}
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
            "Preconditions:",
        ]

        for item in test_case.get("preconditions", []):
            lines.append(f"- {item}")

        lines.append("Steps:")
        for index, step in enumerate(test_case.get("steps", []), start=1):
            lines.append(f"{index}. {step}")

        lines.append("Expected Results:")
        for item in test_case.get("expected_results", []):
            lines.append(f"- {item}")

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks).strip() + "\n"


def test_plan_generator(state: AgentState) -> AgentState:
    print("Node9: generating human-readable test plan...")

    requirements = _extract_requirements(getattr(state, "requirements", []))

    if not requirements:
        state.test_plan_payload = {"test_cases": []}
        state.test_plan_path = ""
        print("Node9: no requirements found.")
        return state

    repository_contents = state.repository_contents or {}

    source_changed_files = [
        file_path
        for file_path in list(getattr(state, "changed_files", []) or [])
        if ".test." not in file_path.lower()
        and ".spec." not in file_path.lower()
        and "/tests/" not in file_path.lower()
    ]

    changed_files = _read_full_context_files(
        source_changed_files,
        repository_contents,
    )

    impacted_files = _read_full_context_files(
        list(getattr(state, "impacted_files", []) or []),
        repository_contents,
    )

    response = _azure_client().chat.completions.create(
        model=_deployment_name(),
        messages=[
            {
                "role": "system",
                "content": "You create human-readable QA test plans and return only valid JSON.",
            },
            {
                "role": "user",
                "content": _build_prompt(
                    requirements,
                    changed_files,
                    impacted_files,
                    state,
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=12000,
    )

    payload = _parse_json_response(response.choices[0].message.content or "{}")

    output_dir = Path(__file__).resolve().parent.parent / "generated_tests"
    output_dir.mkdir(parents=True, exist_ok=True)

    test_txt_path = output_dir / "test.txt"
    test_txt_path.write_text(_format_test_txt(payload), encoding="utf-8")

    state.test_plan_payload = payload
    state.test_plan_path = str(test_txt_path)

    print(f"Node9: saved readable test plan to {test_txt_path}")
    return state


def testPlanGenerator(state: AgentState) -> AgentState:
    return test_plan_generator(state)
