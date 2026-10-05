"""
StateGraph do agente CRM HITL.
Estrutura: call_claude → execute_tool → loop até sem tool calls.
"""

from typing import Annotated, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AnyMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from config import ANTHROPIC_API_KEY
from prompt import SYSTEM_PROMPT
from tools import ALL_TOOLS

# ---------------------------------------------------------------------------
# Estado do grafo
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


# ---------------------------------------------------------------------------
# Modelo com prompt caching
# ---------------------------------------------------------------------------

llm = ChatAnthropic(
    model="claude-sonnet-5-5",
    api_key=ANTHROPIC_API_KEY,
    max_tokens=8096,
).bind_tools(ALL_TOOLS)

# System prompt com cache_control para economizar tokens entre execuções diárias
_SYSTEM = SystemMessage(
    content=[
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
)


# ---------------------------------------------------------------------------
# Nós do grafo
# ---------------------------------------------------------------------------

def call_claude(state: AgentState) -> AgentState:
    messages = [_SYSTEM] + state["messages"]
    response = llm.invoke(messages)
    return {"messages": [response]}


_TOOL_MAP = {t.name: t for t in ALL_TOOLS}


def execute_tool(state: AgentState) -> AgentState:
    last = state["messages"][-1]
    results: list[ToolMessage] = []

    for call in last.tool_calls:
        tool_fn = _TOOL_MAP[call["name"]]
        try:
            output = tool_fn.invoke(call["args"])
        except Exception as exc:
            output = {"error": str(exc)}

        results.append(
            ToolMessage(
                content=str(output),
                tool_call_id=call["id"],
                name=call["name"],
            )
        )

    return {"messages": results}


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "execute_tool"
    return END


# ---------------------------------------------------------------------------
# Construção do grafo
# ---------------------------------------------------------------------------

def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("call_claude", call_claude)
    graph.add_node("execute_tool", execute_tool)

    graph.set_entry_point("call_claude")
    graph.add_conditional_edges("call_claude", should_continue)
    graph.add_edge("execute_tool", "call_claude")

    return graph.compile()


agent = build_graph()
