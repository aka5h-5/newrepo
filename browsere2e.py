import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AzureOpenAI

from statenew import AgentState

load_dotenv()

MAX_CHANGED_FILES = 20
MAX_IMPACTED_FILES = 40
MAX_CHANGED_FILE_CHARS = 16000
MAX_IMPACTED_FILE_CHARS = 10000
MAX_CHANGED_TOTAL_CHARS = 180000
MAX_IMPACTED_TOTAL_CHARS = 160000


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
        return {"browser_e2e_test_cases": []}

    return parsed if isinstance(parsed, dict) else {"browser_e2e_test_cases": []}


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


def _browser_strategy_items(state: AgentState) -> list:
    strategy = (getattr(state, "e2e_strategy_payload", {}) or {}).get("e2e_strategy", [])
    return [
        item
        for item in strategy
        if isinstance(item, dict) and item.get("requires_browser_e2e")
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
        f"Start URL: {test_case.get('start_url', '')}",
        "Steps:",
    ]

    for idx, step in enumerate(test_case.get("steps", []), start=1):
        lines.append(f"{idx}. {step}")

    lines.append("Expected Results:")
    for item in test_case.get("expected_results", []):
        lines.append(f"- {item}")

    return "\n".join(lines)


def _build_prompt(strategy_items: list, changed_files: dict, impacted_files: dict, state: AgentState) -> str:
    app_base_url = getattr(state, "app_base_url", "") or "<configured application base URL>"

    return f"""
You are a Senior QA Engineer specializing in browser E2E test planning.

Generate plain-English browser E2E test cases only.
Do NOT generate automation code.

These test cases will be passed to another tool that generates executable browser automation.
Therefore the steps must describe the FULL user flow clearly enough for code generation.

Return ONLY valid JSON:
{{
  "browser_e2e_test_cases": [
    {{
      "id": "TC-REQ-001-BROWSER-E2E-TRUE-01",
      "requirement_id": "REQ-001",
      "test_type": "browser-e2e-true-path",
      "priority": "P0",
      "risk": "high",
      "title": "Browser E2E true path title",
      "automation_target": "Browser E2E",
      "start_url": "{app_base_url}",
      "steps": ["Load URL: {app_base_url}"],
      "expected_results": []
    }}
  ]
}}

Mandatory:
- Generate browser-e2e-true-path and browser-e2e-error-path for each strategy item.
- Put setup, test data, navigation, actions, assertions, and cleanup inside steps.
- Do not use separate preconditions, test_data, or cleanup_steps fields.

Browser flow requirements:
- First step must be exactly: "Load URL: {app_base_url}"
- Steps must cover the complete user journey from page load to final validation.
- Do not assume the browser is already on the correct page.
- Include how the user reaches the impacted feature after loading the base URL.
- Include any required setup as explicit steps, for example creating/selecting a record.
- Include user interactions such as clicking, typing, selecting, submitting, opening modals, filtering, or navigating.
- Include wait steps for asynchronous UI/backend updates where relevant.
- Include validation steps after each important action.
- Include error/recovery steps for error-path tests.
- Include cleanup/reset steps at the end if the test creates or modifies data.
- If backend or SDK changes affect frontend behavior, explicitly include the frontend action that triggers that backend/SDK behavior.
- If cross-repo impact exists, mention that the frontend context repo is consuming behavior from the primary repo.

Step quality:
- Steps should be detailed and automation-ready.
- Browser E2E should usually have 8 to 16 steps.
- Each step should contain one clear action or validation.
- Do not combine unrelated actions in one step.
- Do not overdo tiny visual checks.
- Do not assume specific notifications, snackbars, toasts, banners, dialogs, or error messages unless explicitly supported by the provided evidence.
- When UI feedback mechanisms are unknown, validate the resulting user-visible behavior or application state instead of a specific notification.
- Prefer assertions about outcomes, navigation, data state, rendering, or functionality over assertions about toast/snackbar text.
-Instead of: Verify the application handles the failed image load gracefully, generate:Verify the share action completes successfully and the link is available to the user.
-Instead of: Verify an error message is displayed, generate: Verify the application remains usable and provides appropriate handling for the failed request.

E2E Strategy Items:
{json.dumps(strategy_items, indent=2)}

Changed File Context:
{json.dumps(changed_files, indent=2)}

Impacted File Context:
{json.dumps(impacted_files, indent=2)}

Navigation Graph:
{_truncate(json.dumps(getattr(state, "navigation_graph", {}) or {}, indent=2), 12000)}

Repo Tech Stack:
{json.dumps(getattr(state, "repo_tech_stack", {}) or {}, indent=2)}

Context Repo Usage Matches:
{json.dumps(getattr(state, "context_usage_matches", {}) or {}, indent=2)}

Impact Analysis:
{_truncate(json.dumps(getattr(state, "impact_analysis", {}) or {}, indent=2), 12000)}
""".strip()


def browser_e2e_plan_generator(state: AgentState) -> AgentState:
    print("Generating browser E2E plain-English test cases...")

    strategy_items = _browser_strategy_items(state)

    if not strategy_items:
        state.browser_e2e_payload = {"browser_e2e_test_cases": []}

        output_dir = Path(__file__).resolve().parent.parent / "generated_tests"
        output_dir.mkdir(parents=True, exist_ok=True)

        output_path = output_dir / "browser_e2e_tests.txt"
        output_path.write_text("", encoding="utf-8")
        state.browser_e2e_path = str(output_path)

        print("No browser E2E strategy items found.")
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
                "content": "You generate browser E2E plain-English test cases. Return only valid JSON.",
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
    payload.setdefault("browser_e2e_test_cases", [])

    state.browser_e2e_payload = payload

    output_dir = Path(__file__).resolve().parent.parent / "generated_tests"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "browser_e2e_tests.txt"
    output_text = "\n\n".join(
        _format_case(case)
        for case in payload["browser_e2e_test_cases"]
    ).strip()

    output_path.write_text(output_text + ("\n" if output_text else ""), encoding="utf-8")

    state.browser_e2e_path = str(output_path)

    print(f"Saved browser E2E tests to {output_path}")
    return state


def browserE2EPlanGenerator(state: AgentState) -> AgentState:
    return browser_e2e_plan_generator(state)
