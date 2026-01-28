import os
import re
import subprocess
from typing import Annotated, TypedDict, Literal, Sequence, Optional

from langgraph.graph import StateGraph, START, END # type: ignore
from langgraph.graph.message import add_messages # type: ignore
from langgraph.checkpoint.memory import MemorySaver # type: ignore
from langchain_ollama import ChatOllama # type: ignore
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage # type: ignore


llm = ChatOllama(
    base_url= os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/"), # "https://api.ollama.com",
    model="deepseek-v3.2:cloud", # "gpt-oss:120b-cloud",
    api_key=os.getenv("OLLAMA_API_KEY"),
    temperature=0
)


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    repo_context: Optional[str]
    next_step: Optional[Literal["SEARCH", "PATCH", "GENERATE", "ANSWER"]]


def planner(state: AgentState):
    """Decides next action based on the user's request."""
    # print("=== Planner called ===")
    user_query = state["messages"][-1].content.lower()
    
    prompt = f"""You are an elite code assistant.
        User query: {user_query}
        Available repo context: {state.get('repo_context') or 'none'}

        Decide:
        - Needs to search/read existing code → SEARCH
        - Needs to fix/change/patch code → PATCH
        - Needs to generate new code / write function → GENERATE
        - Simple answer / chat → ANSWER

        Reply with **ONLY** one word: SEARCH, PATCH, GENERATE or ANSWER
    """

    try:
        decision = llm.invoke([HumanMessage(content=prompt)]).content.strip().upper()
        choice = "ANSWER"
        if "SEARCH" in decision:    choice = "SEARCH"
        elif "PATCH" in decision:   choice = "PATCH"
        elif "GENERATE" in decision: choice = "GENERATE"
        return {"next_step": choice}
    except Exception as e:
        # print(f"Planner error: {e}")
        return {"next_step": "ANSWER"}


def search_code(state: AgentState):
    """Search the codebase using ripgrep."""
    # print("=== Search code called ===")
    query = state["messages"][-1].content
    try:
        result = subprocess.run(
            ["rg", "--json", "-i", "--max-columns=200", query, "."],
            capture_output=True, text=True,
            cwd="/app" if os.path.exists("/app") else ".",
            timeout=15
        )
        context = result.stdout[:50000]
        if not context.strip():
            context = "No matches found."
    except Exception as e:
        context = f"Search failed: {str(e)}"
    
    return {"repo_context": context}


def generate_patch(state: AgentState):
    """Generate and apply a git patch."""
    # print("=== Generate patch called ===")
    prompt = f"""Fix this issue:
        User: {state["messages"][-1].content}
        Context: {state.get("repo_context", "No context")[:20000]} 

        Output ONLY a valid git patch (diff format). No explanations.
        """

    try:
        patch = llm.invoke([HumanMessage(content=prompt)]).content.strip()
        patch_path = "suggested.patch"
        with open(patch_path, "w") as f:
            f.write(patch)
        
        apply = subprocess.run(["git", "apply", patch_path], capture_output=True, text=True)
        msg = "Patch applied!" if apply.returncode == 0 else f"Patch failed: {apply.stderr}"
        return {"messages": [AIMessage(content=msg)]}
    except Exception as e:
        return {"messages": [AIMessage(content=f"Patch error: {str(e)}")]}


def generate_code(state: AgentState):
    # print("=== Generate code called ===")
    request = state["messages"][-1].content
    
    prompt = f"""Generate Python code for: {request}
    Context: {state.get("repo_context", "No context")}
    Output format:
    FILENAME: <filename>
    CODE:
    <pure code>
    """
    
    try:
        response = llm.invoke([HumanMessage(content=prompt)]).content.strip()
        
        # Simple parser for the LLM output
        filename_match = re.search(r"FILENAME:\s*(.*)", response)
        code_match = re.search(r"CODE:\s*([\s\S]*)", response)
        
        if filename_match and code_match:
            filename = filename_match.group(1).strip()
            code_content = code_match.group(1).strip()
            
            with open(filename, "w") as f:
                f.write(code_content)
            return {"messages": [AIMessage(content=f"Successfully created/updated {filename}")]}
        
        return {"messages": [AIMessage(content=f"Generated code (not written to file):\n{response}")]}
    except Exception as e:
        return {"messages": [AIMessage(content=f"Code generation failed: {str(e)}")]}


# ────────────────────────────────────────────────
# EDGES & ROUTING
# ────────────────────────────────────────────────

def route_tools(state: AgentState) -> Literal["search_code", "generate_patch", "generate_code", END]: # type: ignore
    step = state.get("next_step")
    if step == "SEARCH":
        return "search_code"
    if step == "PATCH":
        return "generate_patch"
    if step == "GENERATE":
        return "generate_code"
    return END



builder = StateGraph(AgentState)

# Add all nodes
builder.add_node("planner",        planner)
builder.add_node("search_code",    search_code)
builder.add_node("generate_patch", generate_patch)
builder.add_node("generate_code",  generate_code)

# Define edges
builder.add_edge(START, "planner")

builder.add_conditional_edges(
    "planner",
    route_tools,
    {
        "search_code":    "search_code",
        "generate_patch": "generate_patch",
        "generate_code":  "generate_code",
        END:              END
    }
)

# Loops / terminal edges
builder.add_edge("search_code",    "planner")
builder.add_edge("generate_patch", END)
builder.add_edge("generate_code",  END)

# Compile with memory
memory = MemorySaver()
app = builder.compile(checkpointer=memory)