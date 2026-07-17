import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from openai import AzureOpenAI

from statenew import AgentState

load_dotenv()


MAX_CHANGED_FILES = 25
MAX_IMPACTED_FILES = 40
MAX_CHANGED_FILE_CHARS = 20000
MAX_IMPACTED_FILE_CHARS = 10000
MAX_CHANGED_TOTAL_CHARS = 240000
MAX_IMPACTED_TOTAL_CHARS = 160000
MAX_EXISTING_TEST_FILES = 12
MAX_EXISTING_TEST_CHARS = 7000
MAX_AST_CHARS = 16000
MAX_GRAPH_CHARS = 12000


def _truncate(text: str, max_chars: int) -> str:
    value = "" if text is None else str(text)
    return value if len(value) <= max_chars else value[:max_chars] + f"\n... [truncated {len(value) - max_chars} chars]"


def _safe_identifier(value: str, fallback: str) -> str:
    text = str(value or "").strip()

    if not text:
        return fallback

    text = re.sub(r"[^a-zA-Z0-9_-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")

    return text or fallback


def _primary_repo_tech(state: AgentState) -> dict:
    for repo_key, tech in (getattr(state, "repo_tech_stack", {}) or {}).items():
        if (tech.get("role") or "").lower() == "primary":
            return {
                "repo": repo_key,
                "language": tech.get("language", "Unknown"),
                "framework": tech.get("framework", "Unknown"),
                "role": tech.get("role", "primary"),
            }

    return {
        "repo": "",
        "language": getattr(state, "language", "Unknown"),
        "framework": getattr(state, "framework", "Unknown"),
        "role": "primary",
    }


def _test_framework_from_existing_tech(state: AgentState) -> str:
    tech = _primary_repo_tech(state)

    language = str(tech.get("language", "")).lower()
    framework = str(tech.get("framework", "")).lower()

    if "python" in language:
        return "pytest"

    if "typescript" in language or "javascript" in language:
        return "jest"

    if framework in {"fastapi", "flask", "django"}:
        return "pytest"

    if framework in {"react", "next.js", "angular", "vue", "express", "nestjs"}:
        return "jest"

    return "unknown"


def _primary_repo_key(state: AgentState) -> str:
    return str(_primary_repo_tech(state).get("repo") or "")


def _primary_repo_contents(state: AgentState) -> dict:
    repo_key = _primary_repo_key(state)
    repository_contents = getattr(state, "repository_contents", {}) or {}

    if not repo_key:
        return repository_contents

    return {
        path: content
        for path, content in repository_contents.items()
        if path.startswith(repo_key + "/")
    }


def _read_existing_tests(state: AgentState, framework: str) -> dict:
    examples = {}
    primary_contents = _primary_repo_contents(state)

    for file_path, content in primary_contents.items():
        low = file_path.lower()
        name = low.split("/")[-1]

        if framework == "pytest":
            is_test = (
                low.endswith(".py")
                and (
                    "/tests/" in low
                    or name.startswith("test_")
                    or name.endswith("_test.py")
                )
            )
        elif framework == "jest":
            is_test = (
                ".test." in low
                or ".spec." in low
                or "/tests/" in low
                or "__tests__" in low
            )
        else:
            is_test = False

        if is_test:
            examples[file_path] = _truncate(content, MAX_EXISTING_TEST_CHARS)

        if len(examples) >= MAX_EXISTING_TEST_FILES:
            break

    return examples


def _budgeted_file_context(
    files,
    repository_contents,
    max_files,
    max_chars_per_file,
    total_budget,
):
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
        return {
            "can_generate": False,
            "reason": "Model returned invalid JSON.",
            "code": "",
        }

    if not isinstance(parsed, dict):
        return {
            "can_generate": False,
            "reason": "Model response was not a JSON object.",
            "code": "",
        }

    return parsed


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


def _folder_for_test_type(test_type: str) -> str:
    value = str(test_type or "").lower()

    if "integration" in value:
        return "integration"

    if "regression" in value:
        return "regression"

    if "security" in value:
        return "security"

    return "unit"


def _extension_for_framework(framework: str, target_file: str) -> str:
    if framework == "pytest":
        return ".py"

    lower = str(target_file or "").lower()
    if lower.endswith((".tsx", ".jsx")):
        return ".test.tsx"

    return ".test.ts"


def _skip_test_code(test_case: dict, reason: str, framework: str) -> str:
    test_id = str(test_case.get("id") or "unknown-test")
    title = str(test_case.get("title") or "Ungenerated grounded test")
    reason = str(reason or "Insufficient grounded evidence.")

    if framework == "pytest":
        safe_name = _safe_identifier(test_id, "skipped_test").lower()
        safe_reason = reason.replace('"', "'")
        safe_title = title.replace('"""', "'''")

        return f'''import pytest


@pytest.mark.skip(reason="{safe_reason}")
def test_{safe_name}():
    """{safe_title}"""
    assert False
'''

    escaped_title = title.replace("'", "\\'")
    escaped_reason = reason.replace("'", "\\'")

    return f"""describe.skip('{test_id}: {escaped_title}', () => {{
  it('was not generated because evidence was insufficient', () => {{
    throw new Error('{escaped_reason}');
  }});
}});
"""


def _default_file_name(requirement_id, test_type, test_id, framework, target_file):
    req = _safe_identifier(requirement_id, "REQ_UNKNOWN")
    typ = _safe_identifier(test_type, "unit").lower()
    tid = _safe_identifier(test_id, "TC").lower()

    if framework == "pytest":
        return f"test_{req.lower()}_{typ}_{tid}.py"

    return f"{req}.{typ}.{tid}{_extension_for_framework(framework, target_file)}"


def _normalize_file_name(file_name, fallback, framework, target_file):
    name = Path(str(file_name or "").strip()).name

    if not name:
        return fallback

    if framework == "pytest":
        if not name.endswith(".py"):
            name = f"{_safe_identifier(name, fallback).lower()}.py"
        if not name.startswith("test_"):
            name = f"test_{name}"
        return name

    if not name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")):
        stem = _safe_identifier(name, fallback)
        name = f"{stem}{_extension_for_framework(framework, target_file)}"

    return name


def _build_prompt(
    test_case,
    framework,
    primary_tech,
    changed_files,
    impacted_files,
    existing_tests,
    state,
):
    return f"""
You are a Senior SDET.

Generate ONE executable test file for the supplied plain-English test case.

Framework to use: {framework}

The framework has already been selected from the primary repository technology.
Do not re-detect or change the framework.

Allowed frameworks:
- pytest
- jest

Return ONLY valid JSON:
{{
  "can_generate": true,
  "reason": "",
  "id": "{test_case.get("id", "")}",
  "requirement_id": "{test_case.get("requirement_id", "")}",
  "test_type": "{test_case.get("test_type", "")}",
  "file_name": "test file name",
  "target_file": "owner/repo/src/path/to/file",
  "framework": "{framework}",
  "evidence_used": ["owner/repo/src/path/to/file"],
  "code": "complete executable test code"
}}

If you cannot generate a grounded test without guessing, return:
{{
  "can_generate": false,
  "reason": "clear reason",
  "id": "{test_case.get("id", "")}",
  "requirement_id": "{test_case.get("requirement_id", "")}",
  "test_type": "{test_case.get("test_type", "")}",
  "file_name": "",
  "target_file": "",
  "framework": "{framework}",
  "evidence_used": [],
  "code": ""
}}

Rules:
- Generate code using ONLY {framework}.
- If framework is pytest, generate Python pytest code.
- If framework is jest, generate Jest code.
- Do NOT generate Playwright, Cypress, Vitest, Mocha, unittest, or pseudocode.
- Do NOT invent imports, functions, classes, components, hooks, services, APIs, UI text, or error messages.
- Use only names and paths visible in supplied evidence.
- If exact target/import cannot be identified, return can_generate=false.
- Mock dependencies only when dependency paths are visible in imports or existing tests.
- Prefer repository style from existing tests.
- Code must not include markdown fences.

Pytest rules:
- Use pytest.
- Use monkeypatch or unittest.mock only when needed.
- File name must be test_*.py.
- Test functions must start with test_.
- Do not use Django/FastAPI/Flask clients unless evidence shows those patterns.

Jest rules:
- Use describe(), it() or test(), expect().
- Use jest.fn(), jest.spyOn(), jest.mock() only when grounded by evidence.
- If target is React component, React Testing Library is allowed only when component evidence exists.
- Do not invent UI text. Only assert text that appears in changed/impacted files or existing tests.
- File name must be *.test.ts or *.test.tsx.

Plain-English Test Case:
{json.dumps(test_case, indent=2)}

Primary Repo Technology:
{json.dumps(primary_tech, indent=2)}

Selected Test Framework:
{framework}

Changed File Context:
{json.dumps(changed_files, indent=2)}

Impacted File Context:
{json.dumps(impacted_files, indent=2)}

Existing Primary Repo Tests:
{json.dumps(existing_tests, indent=2)}

Full Repo Technology Information:
{json.dumps(getattr(state, "repo_tech_stack", {}) or {}, indent=2)}

AST Analysis:
{_truncate(json.dumps(getattr(state, "ast_analysis", {}) or {}, indent=2), MAX_AST_CHARS)}

Dependency Graph:
{_truncate(json.dumps(getattr(state, "dependency_graph", {}) or {}, indent=2), MAX_GRAPH_CHARS)}

Impact Analysis:
{_truncate(json.dumps(getattr(state, "impact_analysis", {}) or {}, indent=2), MAX_GRAPH_CHARS)}
""".strip()


def test_code_generator(state: AgentState) -> AgentState:
    print("Generating pytest/Jest tests from unit test plan...")

    test_plan_payload = getattr(state, "test_plan_payload", {}) or {}
    test_cases = test_plan_payload.get("test_cases", [])

    if not isinstance(test_cases, list) or not test_cases:
        state.generated_tests = {
            "test_files": [],
            "skipped_tests": [],
            "error": "No test cases found in state.test_plan_payload.",
        }
        print("No test cases found.")
        return state

    primary_tech = _primary_repo_tech(state)
    framework = _test_framework_from_existing_tech(state)

    if framework not in {"pytest", "jest"}:
        state.generated_tests = {
            "framework": framework,
            "primary_tech": primary_tech,
            "test_files": [],
            "skipped_tests": [
                {
                    "id": case.get("id", ""),
                    "requirement_id": case.get("requirement_id", ""),
                    "test_type": case.get("test_type", ""),
                    "reason": (
                        "Primary repo language/framework from repo_tech_stack "
                        "could not be mapped to pytest or Jest."
                    ),
                }
                for case in test_cases
                if isinstance(case, dict)
            ],
            "error": "Unsupported or unknown primary test framework.",
            "summary": {
                "input_test_cases": len(test_cases),
                "generated_test_files": 0,
                "skipped_tests": len(test_cases),
                "count_matches_unittests_txt": False,
            },
        }
        print("Unknown framework. Skipping code generation.")
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

    existing_tests = _read_existing_tests(state, framework)

    client = _azure_client()
    deployment = _deployment_name()

    output_root = Path(__file__).resolve().parent.parent / "generated_tests" / framework
    output_root.mkdir(parents=True, exist_ok=True)

    written_files = []
    skipped_tests = []

    for index, test_case in enumerate(test_cases, start=1):
        if not isinstance(test_case, dict):
            continue

        test_id = _safe_identifier(test_case.get("id"), f"TC_{index:03d}")
        requirement_id = _safe_identifier(test_case.get("requirement_id"), "REQ_UNKNOWN")
        test_type = _safe_identifier(test_case.get("test_type"), "unit").lower()

        response = client.chat.completions.create(
            model=deployment,
            messages=[
                {
                    "role": "system",
                    "content": "You generate grounded executable pytest or Jest tests. Return only valid JSON.",
                },
                {
                    "role": "user",
                    "content": _build_prompt(
                        test_case=test_case,
                        framework=framework,
                        primary_tech=primary_tech,
                        changed_files=changed_files,
                        impacted_files=impacted_files,
                        existing_tests=existing_tests,
                        state=state,
                    ),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=6500,
        )

        payload = _parse_json_response(response.choices[0].message.content or "{}")

        can_generate = bool(payload.get("can_generate"))
        reason = str(payload.get("reason") or "").strip()
        code = str(payload.get("code") or "").strip()
        target_file = str(payload.get("target_file") or "").strip()

        generated = can_generate and bool(code)

        if not generated:
            reason = reason or "Insufficient grounded evidence to generate this test without guessing."
            code = _skip_test_code(test_case, reason, framework)
            skipped_tests.append({
                "id": test_case.get("id", test_id),
                "requirement_id": test_case.get("requirement_id", requirement_id),
                "test_type": test_case.get("test_type", test_type),
                "reason": reason,
            })

        folder = _folder_for_test_type(test_type)
        output_dir = output_root / folder
        output_dir.mkdir(parents=True, exist_ok=True)

        fallback_name = _default_file_name(
            requirement_id=requirement_id,
            test_type=test_type,
            test_id=test_id,
            framework=framework,
            target_file=target_file,
        )

        file_name = _normalize_file_name(
            file_name=payload.get("file_name", ""),
            fallback=fallback_name,
            framework=framework,
            target_file=target_file,
        )

        file_path = output_dir / file_name
        file_path.write_text(code + "\n", encoding="utf-8")

        written_files.append({
            "id": test_case.get("id", test_id),
            "requirement_id": test_case.get("requirement_id", requirement_id),
            "test_type": test_case.get("test_type", test_type),
            "framework": framework,
            "target_file": target_file,
            "file_path": str(file_path),
            "generated": generated,
            "evidence_used": payload.get("evidence_used", []),
        })

    state.generated_tests = {
        "framework": framework,
        "primary_tech": primary_tech,
        "test_dir": str(output_root),
        "test_files": written_files,
        "skipped_tests": skipped_tests,
        "summary": {
            "input_test_cases": len(test_cases),
            "generated_test_files": len(written_files),
            "skipped_tests": len(skipped_tests),
            "count_matches_unittests_txt": len(written_files) == len(test_cases),
        },
    }

    print(
        f"Generated {len(written_files)} {framework} test files for "
        f"{len(test_cases)} test cases. Skipped with skip markers: {len(skipped_tests)}."
    )

    return state


def unitTestsGenerator(state: AgentState) -> AgentState:
    return test_code_generator(state)
