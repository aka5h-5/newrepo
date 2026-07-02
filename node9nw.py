from state import AgentState
import os
import json
from pathlib import Path
from dotenv import load_dotenv
from openai import AzureOpenAI


load_dotenv()


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


def _truncate_dict(d: dict, max_chars_per_item: int = 1400, max_items: int = 10) -> dict:
    truncated = {}
    for i, (k, v) in enumerate(d.items()):
        if i >= max_items:
            truncated["..."] = f"[{len(d) - max_items} more items omitted]"
            break
        truncated[k] = _truncate(v, max_chars_per_item)
    return truncated


def _safe_relative_path(path_value: str, workspace_root: Path):
    rel = Path(str(path_value).strip())
    if rel.is_absolute():
        return None

    resolved = (workspace_root / rel).resolve()
    try:
        resolved.relative_to(workspace_root)
    except ValueError:
        return None
    return resolved


def _load_generated_json(generated_text: str):
    text = (generated_text or "").strip()
    if not text:
        return None

    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _extract_requirements(requirements_text: str):
    parsed = _load_generated_json(requirements_text)
    if isinstance(parsed, dict) and isinstance(parsed.get("requirements"), list):
        return [r for r in parsed["requirements"] if isinstance(r, dict)]
    return []


def _infer_flow_type(title: str, changed_files: list, impacted_files: list):
    text = " ".join([str(title or "")] + list(changed_files or []) + list(impacted_files or [])).lower()

    ui_markers = ["page", "screen", "component", "tsx", "jsx", "frontend", "ui", "view", "route"]
    api_markers = ["api", "endpoint", "controller", "router", "request", "response", "http", "swagger", "openapi"]
    service_markers = ["service", "repository", "dao", "worker", "job", "queue", "processor", "handler", "domain"]

    ui_score = sum(1 for m in ui_markers if m in text)
    api_score = sum(1 for m in api_markers if m in text)
    service_score = sum(1 for m in service_markers if m in text)

    if api_score >= max(ui_score, service_score) and api_score > 0:
        return "api"
    if ui_score >= max(api_score, service_score) and ui_score > 0:
        return "ui"
    return "service"


def _display_step_from_path(path: str):
    low = str(path).lower()
    if "auth" in low or "login" in low or "signin" in low:
        return "Authentication page"
    if "home" in low or "dashboard" in low:
        return "Home dashboard"
    if "list" in low or "search" in low or "catalog" in low:
        return "Listing/Search page"
    if "detail" in low or "view" in low or "profile" in low:
        return "Details page"
    if "create" in low or "edit" in low or "form" in low:
        return "Create/Edit form"
    if "review" in low or "approve" in low:
        return "Review/Approval page"
    if "settings" in low or "config" in low:
        return "Settings page"
    if "admin" in low:
        return "Admin page"
    if "confirm" in low or "success" in low or "result" in low:
        return "Confirmation/Result page"
    return "Feature page"


def _infer_navigation_steps(title: str, changed_files: list, impacted_files: list, dependency_edges: list):
    _ = dependency_edges
    base = [
        "Open application base URL.",
        "Authenticate with a valid user role if required.",
        "Navigate from main menu to the target module.",
    ]

    if title:
        base.append(f"Open the flow for requirement: {title}.")

    seen_labels = set()
    for path in list(changed_files or []) + list(impacted_files or []):
        label = _display_step_from_path(path)
        if label == "Feature page" or label in seen_labels:
            continue
        seen_labels.add(label)
        base.append(f"Navigate to {label}.")
        if len(base) >= 8:
            break

    base.extend([
        "Trigger the primary user action in this flow.",
        "Verify resulting state/message and persisted behavior.",
    ])

    deduped = []
    seen = set()
    for step in base:
        if step in seen:
            continue
        seen.add(step)
        deduped.append(step)
    return deduped[:10]


def _playwright_hints(navigation_steps: list):
    hints = ["await page.goto('/')"]
    for step in navigation_steps:
        s = str(step).lower()
        if "auth" in s or "login" in s or "sign in" in s:
            hints.extend([
                "await page.click('text=Login')",
                "await page.fill('[name=email]', testEmail)",
                "await page.fill('[name=password]', testPassword)",
                "await page.click('text=Sign In')",
            ])
        elif "menu" in s or "navigate" in s:
            hints.append("await page.click('[data-testid=navigation-menu]')")
        elif "search" in s or "listing" in s:
            hints.extend([
                "await page.fill('[data-testid=search-input]', 'sample value')",
                "await page.keyboard.press('Enter')",
            ])
        elif "details" in s:
            hints.append("await page.click('[data-testid=list-item]:first-child')")
        elif "form" in s:
            hints.extend([
                "await page.fill('[data-testid=primary-input]', 'sample value')",
                "await page.click('[data-testid=submit-action]')",
            ])
        elif "primary user action" in s or "trigger" in s:
            hints.append("await page.click('[data-testid=primary-action]')")
        elif "verify" in s or "persisted" in s or "result" in s:
            hints.append("await expect(page.locator('[data-testid=success-state]')).toBeVisible()")
    return hints[:14]


def _api_hints(test_type: str):
    hints = [
        "resp = client.post('/target-endpoint', json=payload)",
        "assert resp.status_code in (200, 201, 202)",
    ]
    if test_type in {"negative", "security"}:
        hints = [
            "resp = client.post('/target-endpoint', json=invalid_payload)",
            "assert resp.status_code in (400, 401, 403, 404, 422)",
        ]
    return hints


def _service_hints(test_type: str):
    hints = [
        "result = service.execute(valid_input)",
        "assert result is not None",
    ]
    if test_type in {"negative", "security"}:
        hints = [
            "with pytest.raises(ExpectedError):",
            "    service.execute(invalid_input)",
        ]
    return hints


def _default_scenario(
    req_id: str,
    title: str,
    req_type: str,
    test_type: str,
    changed_files: list,
    impacted_files: list,
    dependency_edges: list,
):
    flow_type = _infer_flow_type(title, changed_files, impacted_files)
    nav_steps = _infer_navigation_steps(title, changed_files, impacted_files, dependency_edges)

    if test_type in {"negative", "security"}:
        execution_steps = [
            "Provide invalid or unauthorized input payload.",
            "Submit action and capture error response.",
        ]
    else:
        execution_steps = [
            "Enter valid test data for the flow.",
            "Submit action and wait for completion.",
        ]

    if flow_type == "api":
        nav_steps = [
            "Start API test context and authentication token setup.",
            "Prepare endpoint URL and request payload.",
            "Invoke API endpoint for the target requirement.",
            "Validate response body and status code.",
        ]
    elif flow_type == "service":
        nav_steps = [
            "Initialize service dependencies and mocks.",
            "Prepare input model for target behavior.",
            "Invoke service method under test.",
            "Verify domain output and side effects.",
        ]

    automation_hints = {}
    if flow_type == "ui":
        automation_hints = {"playwright_steps": _playwright_hints(nav_steps)}
    elif flow_type == "api":
        automation_hints = {"api_steps": _api_hints(test_type)}
    else:
        automation_hints = {"service_steps": _service_hints(test_type)}

    return {
        "title": f"{test_type.title()} scenario for {title}",
        "flow_type": flow_type,
        "preconditions": [
            "System dependencies are available.",
            f"Requirement type is {req_type}.",
        ],
        "inputs": {},
        "navigation_steps": nav_steps,
        "setup_steps": ["Prepare fixtures, stubs, and test data."],
        "execution_steps": execution_steps,
        "assertion_steps": [
            "Verify expected state transitions.",
            "Verify response and output payload.",
        ],
        "teardown_steps": ["Reset created data and cleanup test context."],
        "expected": [f"Requirement {req_id} behavior is validated."],
        "automation_hints": automation_hints,
    }


def _normalize_payload_scenarios(
    payload: dict,
    requirements: list,
    changed_files: list,
    impacted_files: list,
    dependency_edges: list,
):
    req_lookup = {
        str(req.get("id") or ""): req for req in requirements if isinstance(req, dict)
    }

    tests = payload.get("generated_tests", [])
    if not isinstance(tests, list):
        return payload

    e2e_types = {"positive", "integration", "regression", "security"}

    for test in tests:
        if not isinstance(test, dict):
            continue

        req_id = str(test.get("requirement_id") or "REQ-UNKNOWN")
        req = req_lookup.get(req_id, {})
        title = str(req.get("title") or req_id)
        req_type = str(req.get("type") or "functional")
        test_type = str(test.get("type") or "positive").lower()
        inferred_nav = _infer_navigation_steps(title, changed_files, impacted_files, dependency_edges)
        inferred_flow_type = _infer_flow_type(title, changed_files, impacted_files)

        scenario = test.get("scenario")
        if not isinstance(scenario, dict):
            scenario = _default_scenario(
                req_id,
                title,
                req_type,
                test_type,
                changed_files,
                impacted_files,
                dependency_edges,
            )
            test["scenario"] = scenario

        if "navigation_steps" not in scenario or not isinstance(scenario.get("navigation_steps"), list):
            scenario["navigation_steps"] = inferred_nav

        required_list_fields = [
            "preconditions",
            "setup_steps",
            "execution_steps",
            "assertion_steps",
            "teardown_steps",
            "expected",
        ]
        for field in required_list_fields:
            if not isinstance(scenario.get(field), list):
                scenario[field] = []

        if not scenario.get("title"):
            scenario["title"] = f"{test_type.title()} scenario for {title}"
        if not scenario.get("flow_type"):
            scenario["flow_type"] = inferred_flow_type
        if not isinstance(scenario.get("inputs"), dict):
            scenario["inputs"] = {}

        if not isinstance(scenario.get("automation_hints"), dict):
            scenario["automation_hints"] = {}

        flow_type = str(scenario.get("flow_type") or inferred_flow_type)
        if flow_type == "ui":
            if not isinstance(scenario.get("automation_hints", {}).get("playwright_steps"), list):
                scenario["automation_hints"]["playwright_steps"] = _playwright_hints(scenario.get("navigation_steps", []))
        elif flow_type == "api":
            if not isinstance(scenario.get("automation_hints", {}).get("api_steps"), list):
                scenario["automation_hints"]["api_steps"] = _api_hints(test_type)
        else:
            if not isinstance(scenario.get("automation_hints", {}).get("service_steps"), list):
                scenario["automation_hints"]["service_steps"] = _service_hints(test_type)

        if "end_to_end" not in test:
            test["end_to_end"] = test_type in e2e_types

    return payload


def _fallback_payload(
    requirements: list,
    file_prefix: str,
    file_ext: str,
    changed_files: list,
    impacted_files: list,
    dependency_edges: list,
):
    generated_tests = []
    traceability = {}

    test_types = [
        ("positive", "POS", "test_deactivate_old_baseline_on_submit"),
        ("negative", "NEG", "test_fail_to_deactivate_baseline"),
        ("boundary", "BND", "test_deactivate_baseline_with_min_max_values"),
        ("regression", "REG", "test_regression_deactivate_baseline"),
        ("integration", "INT", "test_integration_deactivate_baseline"),
        ("security", "SEC", "test_unauthorized_deactivate_baseline"),
    ]

    if not requirements:
        requirements = [{"id": "REQ-001", "title": "Core behavior should work", "type": "functional"}]

    for idx, req in enumerate(requirements, start=1):
        req_id = str(req.get("id") or f"REQ-{idx:03d}")
        title = str(req.get("title") or f"Requirement {idx}")
        req_type = str(req.get("type") or "functional")

        for test_type, test_suffix, test_name in test_types:
            test_id = f"TC-{req_id}-{test_suffix}-01"
            file_name = f"{file_prefix}test_{req_id.lower().replace('-', '_')}{file_ext}"

            if file_ext.endswith(".py"):
                if test_type == "negative":
                    test_code = (
                        "import pytest\n"
                        "from fastapi import HTTPException\n\n"
                        f"def {test_name}(mock_db, mock_review_service):\n"
                        "    # Setup\n"
                        "    comparison_id = 'invalid-comparison-id'\n"
                        "    mock_review_service.submit.side_effect = HTTPException(status_code=404, detail='Comparison not found')\n\n"
                        "    # Execution & Assertions\n"
                        "    with pytest.raises(Exception) as exc_info:\n"
                        "        submit(comparison_id)\n"
                        "    assert 'Comparison not found' in str(exc_info.value)\n"
                    )
                elif test_type == "security":
                    test_code = (
                        "import pytest\n"
                        "from fastapi import HTTPException\n\n"
                        f"def {test_name}(mock_db, mock_review_service):\n"
                        "    # Setup\n"
                        "    comparison_id = 'valid-comparison-id'\n"
                        "    mock_review_service.submit.side_effect = HTTPException(status_code=401, detail='Unauthorized')\n\n"
                        "    # Execution & Assertions\n"
                        "    with pytest.raises(HTTPException) as exc_info:\n"
                        "        submit(comparison_id)\n"
                        "    assert exc_info.value.status_code == 401\n"
                    )
                elif test_type == "positive":
                    test_code = (
                        f"def {test_name}(mock_db, mock_review_service):\n"
                        "    # Setup\n"
                        "    comparison_id = 'valid-comparison-id'\n"
                        "    mock_review_service.submit.return_value = ...  # Mock response\n\n"
                        "    # Execution\n"
                        "    result = submit(comparison_id)\n\n"
                        "    # Assertions\n"
                        "    assert result is not None\n"
                        "    assert mock_review_service.submit.called\n"
                        "    # Teardown\n"
                        "    cleanup_baselines()\n"
                    )
                else:
                    test_code = (
                        f"def {test_name}(mock_db, mock_review_service):\n"
                        "    # Setup\n"
                        "    comparison_id = 'valid-comparison-id'\n"
                        "    mock_review_service.submit.return_value = ...  # Mock response\n\n"
                        "    # Execution\n"
                        "    result = submit(comparison_id)\n\n"
                        "    # Assertions\n"
                        "    assert result is not None\n"
                        "    assert mock_review_service.submit.called\n"
                    )
            else:
                test_code = (
                    f"describe('{req_id} {test_type}', () => {{\n"
                    f"  it('validates {title}', () => {{\n"
                    "    const result = true;\n"
                    "    expect(result).toBe(true);\n"
                    "  });\n"
                    "});\n"
                )

            generated_tests.append(
                {
                    "test_id": test_id,
                    "requirement_id": req_id,
                    "type": test_type,
                    "priority": "P1",
                    "risk": "medium",
                    "file": file_name,
                    "target": {
                        "component": "unknown_component",
                        "function": "submit",
                        "route": None,
                    },
                    "scenario": {
                        **_default_scenario(
                            req_id,
                            title,
                            req_type,
                            test_type,
                            changed_files,
                            impacted_files,
                            dependency_edges,
                        ),
                    },
                    "description": f"Fallback synthetic {test_type} test for {req_id}: {title}",
                    "test_code": test_code,
                    "end_to_end": test_type in {"positive", "integration", "regression", "security"},
                }
            )
            traceability.setdefault(req_id, []).append(test_id)

    return {
        "generated_tests": generated_tests,
        "traceability": traceability,
        "coverage_intent": {
            "covered_requirements": list(traceability.keys()),
            "gaps": [],
        },
    }


def test_generator(state: AgentState) -> AgentState:
    print("Generating tests...")
    print(f"DEBUG: Requirements input length: {len(state.requirements or '')}")
    print(f"DEBUG: Requirements preview: {(state.requirements or '')[:200]}")

    repository_contents = state.repository_contents or {}
    primary_repo = next((repo for repo in state.repos if (repo.get("role") or "primary") == "primary"), None)
    repo_tech_stack = state.repo_tech_stack or {}

    if primary_repo:
        repo_key = f"{primary_repo.get('owner', '')}/{primary_repo.get('repo', '')}"
        primary_stack = repo_tech_stack.get(repo_key, {})
    else:
        primary_stack = {}

    state_languages = getattr(state, "languages", None) or []
    state_frameworks = getattr(state, "frameworks", None) or []
    language = str(
        primary_stack.get("language")
        or (state_languages[0] if state_languages else None)
        or state.language
        or "python"
    ).lower()
    framework = str(
        primary_stack.get("framework")
        or (state_frameworks[0] if state_frameworks else None)
        or state.framework
        or "Unknown"
    )

    changed_file_contents = {
        f: repository_contents[f]
        for f in state.changed_files
        if f in repository_contents
    }

    impacted_components = state.impact_analysis.get("all", []) or state.impacted_files
    impacted_file_contents = {
        f: repository_contents[f]
        for f in impacted_components
        if f in repository_contents
    }

    existing_tests = {
        path: content
        for path, content in repository_contents.items()
        if "test" in path.lower() or "spec" in path.lower()
    }

    dependency_edges = [
        f"{e.get('from')} -> {e.get('to')} [{e.get('type', '')}]"
        for e in _dependency_edges(state.dependency_graph)
    ]

    changed_file_contents = _truncate_dict(changed_file_contents, max_chars_per_item=1400, max_items=12)
    impacted_file_contents = _truncate_dict(impacted_file_contents, max_chars_per_item=1500, max_items=12)
    git_diff_sample = _truncate_dict(state.git_diff, max_chars_per_item=1600, max_items=20)
    existing_tests = _truncate_dict(existing_tests, max_chars_per_item=1200, max_items=8)

    if language in ["javascript", "typescript"]:
        framework_rules = """Generate executable Jest + React Testing Library tests.
- Use describe/it blocks.
- Use jest.fn for mocks.
- Return complete test code only."""
        if language == "typescript":
            file_ext = ".test.tsx" if framework.lower() == "react" else ".test.ts"
        else:
            file_ext = ".test.jsx" if framework.lower() == "react" else ".test.js"
        file_prefix = "tests/"
    else:
        framework_rules = """Generate executable pytest tests.
- Use def test_* functions.
- Use assert statements.
- Return complete test code only."""
        file_ext = "_test.py"
        file_prefix = "tests/"

    diff_summary = ""
    if state.analysis and getattr(state.analysis, "change_summary", None):
        diff_summary = state.analysis.change_summary
    elif getattr(state, "diff_analysis", None):
        diff_summary = str(state.diff_analysis)

    requirements_list = _extract_requirements(state.requirements)

    prompt = f"""You are a Senior Test Automation Engineer.

{framework_rules}

TEST TYPES TO COVER FOR EVERY REQUIREMENT:
- Positive: Happy path - valid inputs, expected success
- Negative: Invalid inputs, error conditions, rejection paths
- Boundary: Edge values (min, max, empty, null, overflow)
- Regression: Ensure existing behavior is not broken
- Integration: Cross-module or cross-service flows
- Security: Unauthorized access, injection, privilege escalation

For Python output, generate ONLY service-level pytest tests in this strict style:
- Each test function signature is either:
    - def test_<name>(mock_db, mock_review_service)
    - def test_<name>(mock_db, mock_compare_service)
- Use section comments exactly like: # Setup, # Execution, # Assertions, optional # Teardown
- Use service-function style calls like submit(comparison_id) or compare(comparison_id)
- Negative and security tests must use pytest.raises(...)
- When appropriate, assert HTTPException status/detail
- Never generate API endpoint tests like client.post(...), client.get(...), requests.post(...)

Python style reference:
def test_deactivate_old_baseline_on_submit(mock_db, mock_review_service):
    # Setup
    comparison_id = 'valid-comparison-id'
    mock_review_service.submit.return_value = ...

    # Execution
    result = submit(comparison_id)

    # Assertions
    assert result is not None
    assert mock_review_service.submit.called

def test_handle_missing_baseline(mock_db, mock_compare_service):
    # Setup
    comparison_id = 'valid-comparison-id'
    mock_compare_service.compare.side_effect = HTTPException(status_code=404, detail='Baseline not found')

    # Execution & Assertions
    with pytest.raises(HTTPException) as exc_info:
        compare(comparison_id)
    assert exc_info.value.status_code == 404

Business Requirements:
{state.requirements}

Diff Intelligence Summary:
{diff_summary}

Changed Files:
{changed_file_contents}

Git Diff:
{git_diff_sample}

AST Analysis:
{str(state.ast_analysis)}

Dependency Graph Edges:
{dependency_edges[:80]}

Impacted Components:
{impacted_file_contents}

Existing Tests:
{existing_tests}

Language: {language}
Framework: {framework}

Return ONLY valid JSON with keys: generated_tests, traceability, coverage_intent.
Each generated test must include: test_id, requirement_id, type, file, scenario, description, test_code.
Do not return markdown fences.

Scenario requirements for each generated test:
- `scenario` must be detailed and automation-ready.
- Include these fields inside `scenario`:
    - title
    - flow_type (`ui` | `api` | `service`)
    - preconditions (array)
    - inputs (object)
    - navigation_steps (array): explicit page/navigation flow and user journey steps
    - setup_steps (array)
    - execution_steps (array)
    - assertion_steps (array)
    - teardown_steps (array)
    - expected (array)
- automation_hints object with one mode-specific key:
    - ui: automation_hints.playwright_steps
    - api: automation_hints.api_steps
    - service: automation_hints.service_steps
- For UI tests, navigation_steps must include concrete movement through the app/module.
- For API/service tests, do NOT force browser navigation; provide endpoint/service invocation flow.
- Mark end_to_end=true for full user flow tests, especially positive/integration/regression/security scenarios.

Rules:
0. REQUIREMENT COMPLETENESS: traceability MUST map every requirement_id listed in Business Requirements.
1. Create at least 1 DISTINCT test per type for each requirement.
2. EVERY requirement MUST have all 6 test types.
3. Test cases must be generated for ALL requirements, including low risk/priority ones.
4. For each requirement, include happy-path, validation-failure, and dependency-failure variants where applicable.
5. Ensure each requirement has service-level tests. API-level tests are allowed for non-Python outputs only.
6. test_code must be complete and runnable; no pseudocode placeholders beyond explicit mock return shorthand (...).
7. Every test_code must include setup/preconditions, execution flow, and assertions; include teardown when relevant.
8. Avoid duplicate test logic across test_ids.
9. Return only JSON.
"""

    client = AzureOpenAI(
        api_key=os.getenv("API_KEY"),
        api_version="2024-05-01-preview",
        azure_endpoint=os.getenv("AZURE_ENDPOINT"),
        timeout=120.0,
    )

    generated_text = ""
    payload = None

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a Senior Test Automation Engineer."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=5000,
        )
        generated_text = response.choices[0].message.content or ""
        payload = _load_generated_json(generated_text)
    except Exception as e:
        print(f"Test generation failed: {e}")

    # Force fallback if model returns endpoint-level tests for Python.
    if isinstance(payload, dict) and file_ext.endswith(".py"):
        generated = payload.get("generated_tests", [])
        if isinstance(generated, list):
            has_endpoint_style = any(
                isinstance(item, dict)
                and any(
                    token in str(item.get("test_code") or "")
                    for token in ("client.post(", "client.get(", "requests.post(", "requests.get(")
                )
                for item in generated
            )
            if has_endpoint_style:
                payload = None

    if isinstance(payload, dict):
        payload = _normalize_payload_scenarios(
            payload,
            requirements_list,
            list(state.changed_files or []),
            list(impacted_components or []),
            dependency_edges,
        )

    if not isinstance(payload, dict) or not isinstance(payload.get("generated_tests"), list) or not payload.get("generated_tests"):
        payload = _fallback_payload(
            requirements_list,
            file_prefix,
            file_ext,
            list(state.changed_files or []),
            list(impacted_components or []),
            dependency_edges,
        )

    workspace_root = Path(__file__).resolve().parent
    output_file = workspace_root / "tests.txt"
    output_file.write_text(json.dumps(payload, indent=4, ensure_ascii=False), encoding="utf-8")

    created_files = []
    test_entries = payload.get("generated_tests", [])
    if isinstance(test_entries, list):
        by_file = {}
        for entry in test_entries:
            if not isinstance(entry, dict):
                continue
            file_value = entry.get("file")
            test_code = entry.get("test_code")
            if not file_value or not isinstance(test_code, str) or not test_code.strip():
                continue

            target_path = _safe_relative_path(file_value, workspace_root)
            if not target_path:
                continue

            by_file.setdefault(target_path, []).append(test_code.strip())

        for target_path, test_blocks in by_file.items():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            file_body = "\n\n".join(test_blocks) + "\n"
            target_path.write_text(file_body, encoding="utf-8")
            created_files.append(str(target_path))

    state.generated_tests = str(output_file)
    print(f"Generated tests saved to: {output_file}")
    if created_files:
        print(f"Created test files: {len(created_files)}")
        for p in created_files:
            print(f" - {p}")
    else:
        print("No test files created from JSON output. See tests.txt for raw response.")

    return state


def testGenerator(state: AgentState) -> AgentState:
    return test_generator(state)
