from dotenv import load_dotenv

from state import AgentState

load_dotenv()


def getDiff(state: AgentState) -> AgentState:
    print("Node3: collecting PR diff...")

    state.changed_files = []
    state.primary_changed_files = []
    state.git_diff = {}

    g = state.github_client

    for repo_cfg in state.repos:
        role = (repo_cfg.get("role") or "primary").lower()
        if role != "primary":
            continue

        owner = repo_cfg.get("owner") or repo_cfg.get("name")
        repo_name = repo_cfg["repo"]
        repo_key = f"{owner}/{repo_name}"
        pr_number = repo_cfg.get("pr_number")

        if pr_number is None:
            state.error = f"Primary repo {repo_key} is missing pr_number"
            return state

        repo = g.get_repo(repo_key)
        pr = repo.get_pull(pr_number)

        for file in pr.get_files():
            prefixed_file = f"{repo_key}/{file.filename}"

            state.changed_files.append(prefixed_file)
            state.primary_changed_files.append(prefixed_file)

            state.git_diff[prefixed_file] = {
                "repo": repo_key,
                "repo_role": "primary",
                "filename": file.filename,
                "status": file.status,
                "diff": file.patch or "",
                "additions": file.additions,
                "deletions": file.deletions,
                "changes": file.changes,
                "previous_filename": getattr(file, "previous_filename", None),
            }

    print(f"Node3: changed primary files={len(state.primary_changed_files)}")
    return state
