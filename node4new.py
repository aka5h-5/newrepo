import os
from dotenv import load_dotenv
from openai import AzureOpenAI

from state import AgentState, DiffAnalysis

load_dotenv()

MAX_CHANGED_FILES = 40
MAX_FILE_CONTENT_CHARS = 2500
MAX_DIFF_FILES = 60
MAX_DIFF_LINES_PER_FILE = 120
MAX_TOTAL_PROMPT_CHARS = 120000


def _truncate_text(text: str, limit: int) -> str:
    value = "" if text is None else str(text)
    return value if len(value) <= limit else value[:limit] + f"\n... [truncated {len(value) - limit} chars]"


def _azure_client() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=os.getenv("API_KEY") or os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-05-01-preview"),
        azure_endpoint=os.getenv("AZURE_ENDPOINT") or os.getenv("AZURE_OPENAI_ENDPOINT"),
        timeout=120.0,
    )


def _deployment_name() -> str:
    return os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME") or os.getenv("AZURE_OPENAI_DEPLOYMENT") or "gpt-4o-mini"


def _changed_content(state: AgentState) -> str:
    blocks = []
    repo = state.repository_contents or {}

    for file_path in list(state.changed_files or [])[:MAX_CHANGED_FILES]:
        content = repo.get(file_path)
        if content:
            blocks.append(f"\n--- FILE: {file_path} ---\n{_truncate_text(content, MAX_FILE_CONTENT_CHARS)}")

    return "\n".join(blocks)


def _compact_git_diff(state: AgentState) -> str:
    blocks = []

    for file_path, meta in list((state.git_diff or {}).items())[:MAX_DIFF_FILES]:
        status = meta.get("status", "")
        diff = meta.get("diff", "") or ""
        diff_lines = "\n".join(diff.splitlines()[:MAX_DIFF_LINES_PER_FILE])
        blocks.append(f"\n--- DIFF: {file_path} [{status}] ---\n{diff_lines}")

    return "\n".join(blocks)


def diff_intelligence(state: AgentState) -> AgentState:
    print("Node4: generating diff intelligence...")

    prompt = f"""
Analyze the PR diff and changed files. Translate the code changes into business language.

Rules:
1. Give a quick summary.
2. Explain what changed, why it matters, risk level, and business impact.
3. Return bullet points.
4. Do not explain code syntax.
5. Focus on user/product impact.
6. Keep it concise, around 5-6 lines.

Changed Files:
{list(state.changed_files or [])[:MAX_CHANGED_FILES]}

PR Diff:
{_compact_git_diff(state)}

Changed File Content:
{_changed_content(state)}
""".strip()

    prompt = _truncate_text(prompt, MAX_TOTAL_PROMPT_CHARS)

    response = _azure_client().chat.completions.create(
        model=_deployment_name(),
        messages=[
            {"role": "system", "content": "You are a software change analyst."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )

    summary = response.choices[0].message.content.strip()
    state.analysis = DiffAnalysis(change_summary=summary)
    state.diff_intelligence = summary

    print("Node4: diff intelligence complete.")
    return state
