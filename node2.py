from github import Github
from dotenv import load_dotenv
import os
from state import AgentState
 
load_dotenv()
 
def loadRepoContents(state: AgentState)-> AgentState:
    # authentication and repository access
    g = Github(os.getenv("GITHUB_TOKEN"))
    print(g.get_user().login)
    repo = g.get_repo(f"{state.repo_owner}/{state.repo_name}")
    # file tree - repository structure
    tree = repo.get_git_tree(repo.default_branch,recursive=True).tree
 
    # loading file contents
    repo_contents = {}
    for item in tree:
        if item.type != "blob":
            continue
        else:
           # print(item.path)
            try:
                file = repo.get_contents(item.path)
                content = file.decoded_content.decode("utf-8",errors="ignore")
                repo_contents[item.path] = content
            except Exception as e:
                print(f"Failed: {item.path}")
                print(e)
 
    state.repository_contents = repo_contents
    return state
