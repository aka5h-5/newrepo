import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AzureOpenAI

from statenew import AgentState

load_dotenv()

MAX_CHANGED_FILES = 20
MAX_IMPACTED_FILES = 35
MAX_CHANGED_FILE_CHARS = 18000
MAX_IMPACTED_FILE_CHARS = 10000
MAX_CHANGED_TOTAL_CHARS = 200000
MAX_IMPACTED_TOTAL_CHARS = 140000


def _truncate(text: str, max_chars: int) -> str:
    value = "" if text is None else str(text)
    return value if len(value) <= max_chars else value[:max_chars] + f"\n... [truncated {len(value) - max_chars} chars]"


def _budgeted_file_context(files, repository_contents, max_files, max_chars_per_file, total_budget):
    result = {}
    used = 0

    for file_path in list(files or [])[:max_files]:
        content = repository_contents.get(file_path)
        if content is None:
            continue

        clipped = str(content)[:max_chars_per_file]
        remaining = total_budget - used
        if remaining <= 0:
            break

        if len(clipped) > remaining:
            clipped = clipped[:remaining] + "\n... [truncated due to prompt budget]"

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
        return {"api_e2e_test_cases": []}

    return parsed if isinstance(parsed, dict) else {"api_e2e_test_cases": []}


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


def _api_strategy_items(state: AgentState) -> list:
    strategy = (getattr(state, "e2e_strategy_payload", {}) or {}).get("e2e_strategy", [])
    return [
        item
        for item in strategy
        if isinstance(item, dict) and item.get("requires_api_e2e")
    ]


def _format_case(test_case: dict) -> str:
    lines = [
        f"Test Case: {test_case.get('id', '')}",
        f"Requirement: {test_case.get('requirement_id', '')}",
        f"Type: {test_case.get('test_type', '')}",
        f"Priority: {test_case.get('priority', 'P1')}",
        f"Risk: {test_case.get('risk', 'medium')}",
        f"Title: {test_case.get('title', '')}",
        f"Automation Target: {test_case.get('automation_target', '')}",
        f"Base URL: {test_case.get('base_url', '')}",
        "Steps:",
    ]

    for idx, step in enumerate(test_case.get("steps", []), start=1):
        lines.append(f"{idx}. {step}")

    lines.append("Expected Results:")
    for item in test_case.get("expected_results", []):
        lines.append(f"- {item}")

    return "\n".join(lines)


def _build_prompt(strategy_items: list, changed_files: dict, impacted_files: dict, state: AgentState) -> str:
    api_base_url = getattr(state, "api_base_url", "") or "<configured API base URL>"

    return f"""
You are a Senior QA Engineer specializing in API/backend E2E test planning.

Generate plain-English API/backend E2E test cases only.
Do NOT generate automation code.

These test cases will be passed to another tool that generates executable API automation.
Therefore the steps must describe the FULL backend/API workflow clearly enough for code generation.

Return ONLY valid JSON:
{{
  "api_e2e_test_cases": [
    {{
      "id": "TC-REQ-001-API-E2E-TRUE-01",
      "requirement_id": "REQ-001",
      "test_type": "api-e2e-true-path",
      "priority": "P0",
      "risk": "high",
      "title": "API E2E true path title",
      "automation_target": "API E2E",
      "base_url": "{api_base_url}",
      "steps": ["Use API base URL: {api_base_url}"],
      "expected_results": []
    }}
  ]
}}

Mandatory:
- Generate api-e2e-true-path and api-e2e-error-path for each strategy item.
- Put setup, test data, request preparation, execution, validation, and cleanup inside steps.
- Do not use separate preconditions, test_data, or cleanup_steps fields.

API flow requirements:
- First step must be exactly: "Use API base URL: {api_base_url}"
- Steps must cover the complete backend/API workflow from setup to final validation.
- Include how test data is prepared.
- Include the request payload or input shape when evidence supports it.
- Include endpoint path only when evidence exists.
- If endpoint path is known, steps should refer to "{api_base_url}<endpoint_path>".
- Include headers/auth setup only when evidence supports auth or headers.
- Include sending/executing the request or backend workflow.
- Include response capture.
- Include status/result validation.
- Include response body validation only for fields supported by evidence.
- Include persistence/side-effect validation only when evidence supports it.
- Include cleanup/reset steps at the end if test data is created or modified.
- For error-path tests, include invalid/missing input, failed dependency, or unsupported state only when evidence supports it.
- If cross-repo impact exists, mention that the backend/API consumer uses behavior from the primary repo.

Step quality:
- Steps should be detailed and automation-ready.
- API E2E should usually have 7 to 14 steps.
- Each step should contain one clear action or validation.
- Do not combine request execution and assertion in one step.
- Do not assume specific success messages, error messages, validation messages, status text, exception text, or notification content unless explicitly supported by the provided evidence.
- When exact API responses or error details are unknown, validate the API behavior, response category, returned contract, or system outcome instead of asserting specific message text.
- If exact field names are unknown, describe the validated contract semantically without inventing names.

E2E Strategy Items:
{json.dumps(strategy_items, indent=2)}

Changed File Context:
{json.dumps(changed_files, indent=2)}

Impacted File Context:
{json.dumps(impacted_files, indent=2)}

Dependency Graph:
{_truncate(json.dumps(getattr(state, "dependency_graph", {}) or {}, indent=2), 12000)}

Impact Analysis:
{_truncate(json.dumps(getattr(state, "impact_analysis", {}) or {}, indent=2), 12000)}

Diff Intelligence:
{json.dumps(
    getattr(state, "diff_intelligence_new", None)
    or getattr(state, "diff_intelligence", ""),
    indent=2
)}
""".strip()


def api_e2e_plan_generator(state: AgentState) -> AgentState:
    print("Generating API E2E plain-English test cases...")

    strategy_items = _api_strategy_items(state)

    if not strategy_items:
        state.api_e2e_payload = {"api_e2e_test_cases": []}

        output_dir = Path(__file__).resolve().parent.parent / "generated_tests"
        output_dir.mkdir(parents=True, exist_ok=True)

        output_path = output_dir / "api_e2e_tests.txt"
        output_path.write_text("", encoding="utf-8")
        state.api_e2e_path = str(output_path)

        print("No API E2E strategy items found.")
        return state

    repo = state.repository_contents or {}

    changed_files = _budgeted_file_context(
        getattr(state, "changed_files", []) or [],
        repo,
        MAX_CHANGED_FILES,
        MAX_CHANGED_FILE_CHARS,
        MAX_CHANGED_TOTAL_CHARS,
    )

    impacted_files = _budgeted_file_context(
        getattr(state, "impacted_files", []) or [],
        repo,
        MAX_IMPACTED_FILES,
        MAX_IMPACTED_FILE_CHARS,
        MAX_IMPACTED_TOTAL_CHARS,
    )

    response = _azure_client().chat.completions.create(
        model=_deployment_name(),
        messages=[
            {
                "role": "system",
                "content": "You generate API E2E plain-English test cases. Return only valid JSON.",
            },
            {
                "role": "user",
                "content": _build_prompt(strategy_items, changed_files, impacted_files, state),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=10000,
    )

    payload = _parse_json_response(response.choices[0].message.content or "{}")
    payload.setdefault("api_e2e_test_cases", [])

    state.api_e2e_payload = payload

    output_dir = Path(__file__).resolve().parent.parent / "generated_tests"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "api_e2e_tests.txt"
    output_text = "\n\n".join(
        _format_case(case)
        for case in payload["api_e2e_test_cases"]
    ).strip()

    output_path.write_text(output_text + ("\n" if output_text else ""), encoding="utf-8")

    state.api_e2e_path = str(output_path)

    print(f"Saved API E2E tests to {output_path}")
    return state


def apiE2EPlanGenerator(state: AgentState) -> AgentState:
    return api_e2e_plan_generator(state)
