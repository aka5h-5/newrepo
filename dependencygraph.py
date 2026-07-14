import json
import os
import time

from dotenv import load_dotenv
from openai import AzureOpenAI

from state import AgentState

load_dotenv()


def _truncate(value, max_chars: int = 3000) -> str:
    text = (
        json.dumps(value, ensure_ascii=False, indent=2)
        if isinstance(value, (dict, list))
        else str(value or "")
    )

    if len(text) <= max_chars:
        return text

    return text[:max_chars] + f"\n... [truncated {len(text) - max_chars} chars]"


def _truncate_dict(data: dict, max_items: int = 10, max_chars_per_item: int = 1500) -> dict:
    result = {}

    for index, (key, value) in enumerate((data or {}).items()):
        if index >= max_items:
            result["..."] = f"{len(data) - max_items} more items omitted"
            break

        result[key] = _truncate(value, max_chars_per_item)

    return result


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


def _parse_json(text: str) -> dict:
    value = (text or "").strip()

    if value.startswith("```json"):
        value = value.removeprefix("```json").removesuffix("```").strip()
    elif value.startswith("```"):
        value = value.removeprefix("```").removesuffix("```").strip()

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {
            "requirements": [],
            "coverage_gaps": ["Requirement JSON parsing failed"],
        }

    if not isinstance(parsed, dict):
        return {
            "requirements": [],
            "coverage_gaps": ["Requirement response was not a JSON object"],
        }

    parsed.setdefault("requirements", [])
    parsed.setdefault("coverage_gaps", [])
    return parsed


def _call_with_retry(prompt: str, attempts: int = 3) -> str:
    client = _azure_client()
    delay = 1.0

    for attempt in range(1, attempts + 1):
        try:
            response = client.chat.completions.create(
                model=_deployment_name(),
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert requirements analyst. Return only valid JSON.",
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                temperature=0.2,
                max_tokens=9000,
                response_format={"type": "json_object"},
            )

            return response.choices[0].message.content or "{}"

        except Exception as error:
            if attempt == attempts:
                raise

            print(f"Node8: retry {attempt}/{attempts} after error: {error}")
            time.sleep(delay)
            delay *= 2

    return "{}"


def _fallback_requirements(state: AgentState) -> dict:
    requirements = []

    for index, file_path in enumerate(list(getattr(state, "changed_files", []) or [])[:6], start=1):
        requirements.append({
            "id": f"REQ-{index:03d}",
            "title": f"Changed behavior in {file_path} should work correctly",
            "description": "The changed PR behavior should be validated and should not break impacted flows.",
            "type": "regression",
            "priority": "medium",
            "risk_level": "medium",
            "sources": ["changed_files", "git_diff"],
            "traceability": {
                "changed_files": [file_path],
                "impacted_components": list(getattr(state, "impacted_files", []) or [])[:8],
                "evidence_sources": ["fallback"],
            },
            "test_recommendations": ["e2e", "unit", "regression"],
        })

    return {
        "requirements": requirements,
        "coverage_gaps": [
            "LLM requirement discovery failed; fallback requirements generated from changed files."
        ],
    }


def _collect_docs(repository_contents: dict) -> dict:
    docs = {}

    for path, content in (repository_contents or {}).items():
        low = path.lower()

        if (
            "readme" in low
            or "/docs/" in low
            or "openapi" in low
            or "swagger" in low
        ):
            docs[path] = content

    return docs


def _collect_full_file_contents(repository_contents: dict, files: list[str]) -> dict:
    result = {}

    for file_path in files:
        content = repository_contents.get(file_path)
        if content is not None:
            result[file_path] = content

    return result


def requirement_discovery(state: AgentState) -> AgentState:
    print("Node8: discovering requirements...")

    repository_contents = getattr(state, "repository_contents", {}) or {}

    changed_files = list(getattr(state, "changed_files", []) or [])
    impacted_files = list(getattr(state, "impacted_files", []) or [])

    changed_file_contents = _collect_full_file_contents(
        repository_contents,
        changed_files,
    )

    impacted_file_contents = _collect_full_file_contents(
        repository_contents,
        impacted_files,
    )

    context_usage_matches = getattr(state, "context_usage_matches", {}) or {}

    dependency_graph = getattr(state, "dependency_graph", {}) or {}
    impact_analysis = getattr(state, "impact_analysis", {}) or {}
    impact_subgraph = getattr(state, "impact_subgraph", {}) or {}
    navigation_graph = getattr(state, "navigation_graph", {}) or {}

    docs = _collect_docs(repository_contents)

    prompt = f"""
You are a requirements analyst.

Your task is to identify test-ready business requirements from the pull request and repository context.

Return ONLY valid JSON in this exact format:
{{
  "requirements": [
    {{
      "id": "REQ-001",
      "title": "Clear requirement title",
      "description": "Business-language behavior to validate",
      "type": "functional | validation | error handling | security | regression",
      "priority": "high | medium | low",
      "risk_level": "high | medium | low",
      "sources": ["diff", "changed_file", "impact_graph"],
      "traceability": {{
        "changed_files": ["..."],
        "impacted_components": ["..."],
        "context_repo_consumers": ["..."],
        "evidence_sources": ["..."]
      }},
      "test_recommendations": ["e2e", "unit", "integration", "regression"]
    }}
  ],
  "coverage_gaps": ["..."]
}}

Rules:
1. Requirements must be grounded in the supplied evidence.
2. Prefer requirements from the primary repo PR changes.
3. Include context repo behavior only when context files are connected through usage matches, dependency graph, or impact graph.
4. Do not invent unrelated product workflows.
5. Write descriptions in business language, not code syntax.
6. Make each requirement specific enough to generate setup/action/assertion tests.
7. Include functional, validation, error handling, security, or regression requirements only when evidence supports them.
8. Security requirements are allowed only if evidence includes auth, permissions, roles, tokens, protected resources, or access control.
9. If a change impacts a context repo consumer, mention that in traceability.
10. Do not output markdown or explanations outside JSON.

PR Metadata:
{_truncate(getattr(state, "pr_data", {}), 4000)}

Diff Intelligence:
{getattr(state, "diff_intelligence", "")}

Git Diff:
{json.dumps(getattr(state, "git_diff", {}), indent=2)}

Changed File Contents:
{json.dumps(changed_file_contents, indent=2)}

Impacted File Contents:
{json.dumps(impacted_file_contents, indent=2)}

Context Repo Usage Matches:
{json.dumps(context_usage_matches, indent=2)}

Dependency Graph:
{_truncate(dependency_graph, 12000)}

Impact Analysis:
{_truncate(impact_analysis, 10000)}

Impact Subgraph:
{_truncate(impact_subgraph, 10000)}

Navigation Graph:
{_truncate(navigation_graph, 8000)}

Docs / API Context:
{_truncate_dict(docs, max_items=8, max_chars_per_item=2500)}

Repo Tech Stack:
{_truncate(getattr(state, "repo_tech_stack", {}), 3000)}
""".strip()

    try:
        payload = _parse_json(_call_with_retry(prompt))
    except Exception as error:
        print(f"Node8: requirement discovery failed: {error}")
        payload = _fallback_requirements(state)

    state.requirements = payload

    print(f"Node8: discovered {len(payload.get('requirements', []))} requirements.")
    return state
