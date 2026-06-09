from github import Github
from state import AgentState
import os

def fetch_pr_data(state:AgentState)-> AgentState:
#first node
    g = Github(os.getenv("GITHUB_TOKEN"))
    print(g.get_user().login)
    repo = g.get_repo(f"{state.repo_owner}/{state.repo_name}")
    pr = repo.get_pull(state.pr_number)

    state.pr_data = {
        "title" : pr.title,
        "description" : pr.body,
        "author" : pr.user.login,
        "source_branch" : pr.head.ref,
        "target_branch" : pr.base.ref,
        "state" : pr.state,
        "created_at" : str(pr.created_at)
        }
    return state
