from github import Github
from dotenv import load_dotenv
import os
from state import AgentState 
load_dotenv()
 
def getDiff(state: AgentState)-> AgentState:
    # authentication and repository access
    g = Github(os.getenv("GITHUB_TOKEN"))
    print(g.get_user().login)
    repo = g.get_repo(f"{state.repo_owner}/{state.repo_name}")
    pr = repo.get_pull(state.pr_number)
    files = pr.get_files()
       
    for file in files:
        state.changed_files.append(file.filename)
        state.git_diff[file.filename] = {
            "status": file.status,
            "diff": file.patch
        }
    return state
