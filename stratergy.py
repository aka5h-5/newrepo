import json
import os

from dotenv import load_dotenv
from openai import AzureOpenAI

from statenew import AgentState

load_dotenv()

MAX_GRAPH_CHARS = 12000
MAX_IMPACT_CHARS = 12000
MAX_NAV_CHARS = 8000


def _truncate(value, max_chars: int) -> str:
    text = json.dumps(value, indent=2) if isinstance(value, (dict, list)) else str(value or "")
    return text if len(text) <= max_chars else text[:max_chars] + f"\n... [truncated {len(text) - max_chars} chars]"


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


def _parse_json_response(text: str) -> dict:
    value = (text or "").strip()

    if value.startswith("```json"):
        value = value.removeprefix("```json").removesuffix("```").strip()
    elif value.startswith("```"):
        value = value.removeprefix("```").removesuffix("```").strip()

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"e2e_strategy": []}

    return parsed if isinstance(parsed, dict) else {"e2e_strategy": []}


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


def _repo_key_for(path: str) -> str:
    parts = str(path).replace("\\", "/").split("/")
    return f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else ""


def _repo_type(language: str, framework: str) -> str:
    language = str(language or "").lower()
    framework = str(framework or "").lower()

    if framework in {"react", "next.js", "angular", "vue"}:
        return "frontend"

    if framework in {"fastapi", "flask", "django", "express", "nestjs", "spring boot"}:
        return "backend"

    if "python" in language:
        return "backend_or_library"

    if "typescript" in language or "javascript" in language:
        return "frontend_or_node"

    return "unknown"


def _repo_summary(state: AgentState) -> dict:
    summary = {}

    for repo_key, tech in (getattr(state, "repo_tech_stack", {}) or {}).items():
        language = tech.get("language", "Unknown")
        framework = tech.get("framework", "Unknown")

        summary[repo_key] = {
            "role": tech.get("role", "unknown"),
            "language": language,
            "framework": framework,
            "repo_type": _repo_type(language, framework),
            "loaded_files": tech.get("loaded_files"),
            "total_files_seen": tech.get("total_files_seen"),
        }

    return summary


def _consumer_repo_summary(state: AgentState) -> list[dict]:
    consumers = (
        (getattr(state, "impact_analysis", {}) or {})
        .get("cross_repo_consumers", [])
    )

    tech_stack = getattr(state, "repo_tech_stack", {}) or {}
    result = []

    for file_path in consumers:
        repo_key = _repo_key_for(file_path)
        tech = tech_stack.get(repo_key, {})
        language = tech.get("language", "Unknown")
        framework = tech.get("framework", "Unknown")

        result.append({
            "repo": repo_key,
            "file": file_path,
            "language": language,
            "framework": framework,
            "repo_type": _repo_type(language, framework),
            "role": tech.get("role", "context"),
            "usage_matches": (getattr(state, "context_usage_matches", {}) or {}).get(file_path, []),
        })

    return result


def _build_prompt(requirements: list, state: AgentState) -> str:
    return f"""
You are a senior QA E2E test strategist.

Decide which plain-English E2E test plans are needed for each requirement.

Do NOT generate test cases.
Do NOT generate code.

Return ONLY valid JSON:
{{
  "e2e_strategy": [
    {{
      "requirement_id": "REQ-001",
      "requirement_title": "Requirement title",
      "primary_repo_type": "backend | frontend | sdk-library | unknown",
      "impacted_repo_types": ["frontend", "backend"],
      "requires_api_e2e": true,
      "requires_browser_e2e": true,
      "requires_cross_repo_e2e": true,
      "api_e2e_reason": "why API/backend E2E is needed",
      "browser_e2e_reason": "why browser E2E is needed",
      "cross_repo_reason": "why cross-repo behavior matters",
      "frontend_entry_candidates": ["page or route candidate"],
      "backend_entry_candidates": ["endpoint/service candidate"],
      "evidence": ["short evidence item"]
    }}
  ]
}}

Decision rules:
- Primary backend with no frontend impact: API E2E only.
- Primary backend with impacted frontend context repo: API E2E + browser E2E + cross-repo E2E.
- Primary frontend: browser E2E.
- Primary SDK/library with frontend consumer impact: browser E2E + cross-repo E2E.
- Primary SDK/library with backend consumer impact: API E2E + cross-repo E2E.
- Do not require browser E2E unless frontend evidence exists.
- Do not require API E2E unless backend/API/service evidence exists.
- Use Cross-Repo Consumer Summary as strong evidence.
- Do not invent missing systems.

Requirements:
{json.dumps(requirements, indent=2)}

Repo Tech Stack Summary:
{json.dumps(_repo_summary(state), indent=2)}

Cross-Repo Consumer Summary:
{json.dumps(_consumer_repo_summary(state), indent=2)}

Changed Files:
{json.dumps(getattr(state, "changed_files", []) or [], indent=2)}

Impacted Files:
{json.dumps(getattr(state, "impacted_files", []) or [], indent=2)}

Context Repo Usage Matches:
{json.dumps(getattr(state, "context_usage_matches", {}) or {}, indent=2)}

Dependency Graph:
{_truncate(getattr(state, "dependency_graph", {}) or {}, MAX_GRAPH_CHARS)}

Impact Analysis:
{_truncate(getattr(state, "impact_analysis", {}) or {}, MAX_IMPACT_CHARS)}

Navigation Graph:
{_truncate(getattr(state, "navigation_graph", {}) or {}, MAX_NAV_CHARS)}

Diff Intelligence:
{json.dumps(
    getattr(state, "diff_intelligence_new", None)
    or getattr(state, "diff_intelligence", ""),
    indent=2
)}
""".strip()


def e2e_strategy_planner(state: AgentState) -> AgentState:
    print("Planning E2E strategy...")

    requirements = _extract_requirements(getattr(state, "requirements", []))

    if not requirements:
        state.e2e_strategy_payload = {"e2e_strategy": []}
        print("No requirements found.")
        return state

    response = _azure_client().chat.completions.create(
        model=_deployment_name(),
        messages=[
            {
                "role": "system",
                "content": "You are a senior QA E2E strategist. Return only valid JSON.",
            },
            {
                "role": "user",
                "content": _build_prompt(requirements, state),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=7000,
    )

    payload = _parse_json_response(response.choices[0].message.content or "{}")
    payload.setdefault("e2e_strategy", [])

    state.e2e_strategy_payload = payload

    print(f"Planned E2E strategy for {len(payload['e2e_strategy'])} requirements.")
    return state


def e2eStrategyPlanner(state: AgentState) -> AgentState:
    return e2e_strategy_planner(state)
