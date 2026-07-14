import json
import os
import re
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from openai import AzureOpenAI

from state import AgentState

load_dotenv()


def _truncate(text: str, max_chars: int = 6000) -> str:
    value = "" if text is None else str(text)
    return value if len(value) <= max_chars else value[:max_chars] + f"\n... [truncated {len(value) - max_chars} chars]"


def _read_full_context_files(files: list[str], repository_contents: dict) -> dict:
    result = {}

    for file_path in files:
        content = repository_contents.get(file_path)
        if content is not None:
            result[file_path] = content

    return result


def _read_existing_tests_examples(repository_contents: dict, limit: int = 12) -> dict:
    examples = {}

    for path, content in (repository_contents or {}).items():
        low = path.lower()

        if ".test." in low or ".spec." in low or "/tests/" in low or "__tests__" in low:
            examples[path] = _truncate(content, 6000)

        if len(examples) >= limit:
            break

    return examples


def _safe_identifier(value: str, fallback: str) -> str:
    text = str(value or "").strip()

    if not text:
        return fallback

    text = re.sub(r"[^a-zA-Z0-9_-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")

    return text or fallback


def _parse_json_response(text: str) -> dict:
    value = (text or "").strip()

    if value.startswith("```json"):
        value = value.removeprefix("```json").removesuffix("```").strip()
    elif value.startswith("```"):
        value = value.removeprefix("```").removesuffix("```").strip()

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"test_files": []}

    return parsed if isinstance(parsed, dict) else {"test_files": []}


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
    test_plan_payload: dict,
    changed_files: dict,
    impacted_files: dict,
    dependency_graph: dict,
    navigation_graph: dict,
    existing_tests_examples: dict,
) -> str:
    dependency_summary = {
        "node_count": len(dependency_graph.get("nodes", [])) if isinstance(dependency_graph, dict) else 0,
        "edge_count": len(dependency_graph.get("edges", [])) if isinstance(dependency_graph, dict) else 0,
        "edges": dependency_graph.get("edges", []) if isinstance(dependency_graph, dict) else [],
    }

    return f"""
You are a Senior SDET specializing in React, TypeScript, Jest, and React Testing Library.

Generate executable TypeScript test code from the human-readable test plan.

Return ONLY valid JSON:
{{
  "test_files": [
    {{
      "id": "TC-REQ-001-E2E-TRUE-01",
      "requirement_id": "REQ-001",
      "test_type": "e2e-true-path",
      "file_name": "REQ-001.e2e-true-path.test.tsx",
      "code": "complete runnable TypeScript test code"
    }}
  ]
}}

Rules:
- Do NOT generate Playwright.
- Use Jest + React Testing Library.
- Use @testing-library/react.
- Use @testing-library/user-event.
- Use jest.fn(), jest.spyOn(), screen queries, and waitFor where useful.
- Generate tests only for behavior supported by evidence.
- Do not include TODOs, placeholders, markdown, or explanations.
- Do not invent components, routes, APIs, roles, messages, or permissions.
- Prefer existing repo test style.
- Text assertions are allowed only if text exists in supplied context.
- If evidence is limited, use structural assertions or mock/callback assertions.

Human-Readable Test Plan:
{json.dumps(test_plan_payload, indent=2)}

Changed File Context:
{json.dumps(changed_files, indent=2)}

Impacted File Context:
{json.dumps(impacted_files, indent=2)}

Navigation Graph:
{json.dumps(navigation_graph, indent=2)}

Dependency Graph Summary:
{json.dumps(dependency_summary, indent=2)}

Existing Repository Tests:
{json.dumps(existing_tests_examples, indent=2)}
""".strip()


def test_code_generator(state: AgentState) -> AgentState:
    print("Node10: generating Jest + React test code...")

    test_plan_payload = getattr(state, "test_plan_payload", None)

    if not isinstance(test_plan_payload, dict) or not test_plan_payload:
        state.generated_tests = {
            "test_files": [],
            "error": "No test_plan_payload found. Run test_plan_generator first.",
        }
        print("Node10: no test_plan_payload found.")
        return state

    output_dir = Path(__file__).resolve().parent.parent / "generated_tests"
    e2e_dir = output_dir / "e2e"
    unit_dir = output_dir / "unit"

    output_dir.mkdir(parents=True, exist_ok=True)
    e2e_dir.mkdir(parents=True, exist_ok=True)
    unit_dir.mkdir(parents=True, exist_ok=True)

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

    existing_tests_examples = _read_existing_tests_examples(repository_contents)

    prompt = _build_prompt(
        test_plan_payload=test_plan_payload,
        changed_files=changed_files,
        impacted_files=impacted_files,
        dependency_graph=getattr(state, "dependency_graph", {}) or {},
        navigation_graph=getattr(state, "navigation_graph", {}) or {},
        existing_tests_examples=existing_tests_examples,
    )

    response = _azure_client().chat.completions.create(
        model=_deployment_name(),
        messages=[
            {
                "role": "system",
                "content": "You generate executable Jest + React Testing Library tests and return only valid JSON.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
        max_tokens=16000,
    )

    code_payload = _parse_json_response(response.choices[0].message.content or "{}")

    e2e_by_requirement = defaultdict(list)
    unit_by_requirement = defaultdict(list)

    for item in code_payload.get("test_files", []):
        if not isinstance(item, dict):
            continue

        requirement_id = _safe_identifier(item.get("requirement_id"), "UNKNOWN")
        test_type = _safe_identifier(item.get("test_type"), "test").lower()
        code = str(item.get("code") or "").strip()

        if not code:
            continue

        entry = {
            "test_type": test_type,
            "code": code,
        }

        if test_type.startswith("e2e"):
            e2e_by_requirement[requirement_id].append(entry)
        else:
            unit_by_requirement[requirement_id].append(entry)

    written_files = []

    for requirement_id, tests in e2e_by_requirement.items():
        for item in tests:
            file_path = e2e_dir / f"{requirement_id}.{item['test_type']}.test.tsx"
            file_path.write_text(item["code"] + "\n", encoding="utf-8")

            written_files.append({
                "requirement_id": requirement_id,
                "test_type": item["test_type"],
                "file_path": str(file_path),
            })

    for requirement_id, tests in unit_by_requirement.items():
        for item in tests:
            file_path = unit_dir / f"{requirement_id}.{item['test_type']}.test.tsx"
            file_path.write_text(item["code"] + "\n", encoding="utf-8")

            written_files.append({
                "requirement_id": requirement_id,
                "test_type": item["test_type"],
                "file_path": str(file_path),
            })

    state.generated_tests = {
        "test_plan_path": getattr(state, "test_plan_path", ""),
        "e2e_dir": str(e2e_dir),
        "unit_dir": str(unit_dir),
        "test_files": written_files,
        "code_payload": code_payload,
        "summary": {
            "generated_test_files": len(written_files),
        },
    }

    print(f"Node10: generated {len(written_files)} Jest + React test files")
    return state


def testCodeGenerator(state: AgentState) -> AgentState:
    return test_code_generator(state)
