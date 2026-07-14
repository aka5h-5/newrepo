import os
from github import Github

from state import AgentState


def fetch_pr_data(state: AgentState) -> AgentState:
    print("Node1: fetching PR data...")

    g = Github(os.getenv("GITHUB_TOKEN"))

    if not state.repos:
        state.error = "No repositories provided. Add entries to state.repos."
        return state

    state.github_client = g
    state.pr_data = {}

    first_repo = None
    first_pr = None

    for repo_cfg in state.repos:
        owner = repo_cfg.get("owner") or repo_cfg.get("name")
        repo_name = repo_cfg["repo"]
        role = (repo_cfg.get("role") or "primary").lower()
        pr_number = repo_cfg.get("pr_number")
        repo_key = f"{owner}/{repo_name}"

        repo = g.get_repo(repo_key)
        pr = None

        if role == "primary":
            if pr_number is None:
                state.error = f"Primary repo {repo_key} is missing pr_number"
                return state
            pr = repo.get_pull(pr_number)

        if first_repo is None:
            first_repo = repo

        if first_pr is None and pr is not None:
            first_pr = pr

        if pr is not None:
            state.pr_data[repo_key] = {
                "title": pr.title,
                "description": pr.body,
                "author": pr.user.login,
                "source_branch": pr.head.ref,
                "target_branch": pr.base.ref,
                "state": pr.state,
                "created_at": str(pr.created_at),
                "repo": repo_key,
                "pr_number": pr_number,
                "role": role,
            }
        else:
            state.pr_data[repo_key] = {
                "repo": repo_key,
                "role": role,
            }

    state.repo = first_repo
    state.pr = first_pr

    print("Node1: PR data fetched.")
    return state
