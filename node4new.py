from pydantic import BaseModel
import os
from dotenv import load_dotenv
from langgraph.graph import StateGraph,START,END
from langchain_openai import ChatOpenAI
import state
from state import AgentState, DiffAnalysis
from openai import AzureOpenAI
 
load_dotenv()
 
def diff_intelligence(state: AgentState)-> AgentState:
    question = """Analyze Diff details and changed files of a pull request and translate the code changes into business language.
    Follow the below rules:
    1. Give a quick summary of the changes.
    1. Give precise details on what changed, why it changed, risk level and business impact.
    2. For the above four details, return them as bullet points.
    3. Do not explain code syntax.
    4. Focus on user impact.
    5. Use language understandable by product managers.
    6. Keep the answer concise and about 5-6 lines.
    """
    prompt = f'{question} \n --- \n Changed Files: {state.changed_files} \n PR Diff: \n {state.git_diff}'
 
    print(prompt)
 
    client = AzureOpenAI(
        api_key= f'{os.getenv("API_KEY")}',
        api_version="2024-05-01-preview",
        azure_endpoint=f'{os.getenv("AZURE_ENDPOINT")}'
    )
   
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role":"system","content":"You are a software change analyst"},
            {"role":"user","content":f'{prompt}'}
        ]
    )
    print(response)
    content = response.choices[0].message.content.strip()
    state.analysis = DiffAnalysis(
    change_summary=content
)
    return state
