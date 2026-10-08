"""
Subagente de Análise Profunda de Workflow.
Espelho do workflow n8n "Análise Profunda de Workflow" (zEXGK9a3BN1CuD5mPEJGu).

Ferramentas:
  - listar_workflows_hubspot  → GET /automation/v4/flows
  - detalhar_workflow_hubspot → GET /automation/v4/flows/{id}

Posta relatório no canal de análise profunda (SLACK_CHANNEL_ANALISE_ID).
Limite: 8 iterações (mesmo do n8n).
"""

import requests
from typing import Annotated, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from config import (
    ANTHROPIC_API_KEY,
    HUBSPOT_API_TOKEN_ANALISE,
    SLACK_BOT_TOKEN,
    SLACK_CHANNEL_ANALISE_ID,
    HUBSPOT_BASE_URL,
    SLACK_BASE_URL,
)

_HS_HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_TOKEN_ANALISE}",
    "Content-Type": "application/json",
}

_SLACK_HEADERS = {
    "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
    "Content-Type": "application/json",
}

# ---------------------------------------------------------------------------
# Ferramentas do subagente
# ---------------------------------------------------------------------------

@tool
def listar_workflows_hubspot(offset: int = 0) -> dict:
    """
    Lista workflows do HubSpot relacionados a Deals, ordenados pelos mais recentes.
    Retorna 100 por página. Use offset para paginar quando a lista vier truncada.

    Args:
        offset: Posição inicial para paginação (0, 100, 200...).
    """
    params = {
        "objectTypeId": "0-3",
        "sort": "updatedAt",
        "limit": 100,
        "offset": offset,
    }
    url = f"{HUBSPOT_BASE_URL}/automation/v4/flows"
    resp = requests.get(url, params=params, headers=_HS_HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # Trunca p/ não estourar contexto (mesmo critério do n8n: 20000 chars)
    import json
    raw = json.dumps(data)
    if len(raw) > 20000:
        raw = raw[:20000] + "...(truncado)"
    return {"raw": raw}


@tool
def detalhar_workflow_hubspot(flow_id: str) -> dict:
    """
    Retorna a estrutura completa de um workflow do HubSpot pelo seu ID.

    Args:
        flow_id: ID numérico do workflow.
    """
    url = f"{HUBSPOT_BASE_URL}/automation/v4/flows/{flow_id}"
    resp = requests.get(url, headers=_HS_HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    import json
    raw = json.dumps(data)
    if len(raw) > 20000:
        raw = raw[:20000] + "...(truncado)"
    return {"raw": raw}


_SUBAGENT_TOOLS = [listar_workflows_hubspot, detalhar_workflow_hubspot]
_TOOL_MAP = {t.name: t for t in _SUBAGENT_TOOLS}

# ---------------------------------------------------------------------------
# Grafo do subagente
# ---------------------------------------------------------------------------

class SubState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    iteration_count: int


_llm = ChatAnthropic(
    model="claude-sonnet-5-5",
    api_key=ANTHROPIC_API_KEY,
    max_tokens=3000,
).bind_tools(_SUBAGENT_TOOLS)

_SYSTEM = SystemMessage(
    content=[
        {
            "type": "text",
            "text": (
                "Você é um investigador de workflows HubSpot. "
                "Identifique o workflow pelo nome aproximado, leia sua estrutura completa "
                "e produza um relatório com as possíveis causas do problema e sugestões de correção. "
                "NUNCA tente editar workflows — você só tem ferramentas de leitura. "
                "Se não encontrar o workflow após varrer todas as páginas, informe claramente."
            ),
            "cache_control": {"type": "ephemeral"},
        }
    ]
)

MAX_ITERATIONS = 8  # mesmo limite do n8n


def _call_llm(state: SubState) -> SubState:
    messages = [_SYSTEM] + state["messages"]
    response = _llm.invoke(messages)
    return {"messages": [response], "iteration_count": state["iteration_count"]}


def _execute_tool(state: SubState) -> SubState:
    last = state["messages"][-1]
    results: list[ToolMessage] = []

    for call in last.tool_calls:
        fn = _TOOL_MAP[call["name"]]
        try:
            output = fn.invoke(call["args"])
        except Exception as exc:
            output = {"error": str(exc)}
        results.append(
            ToolMessage(
                content=str(output),
                tool_call_id=call["id"],
                name=call["name"],
            )
        )

    return {
        "messages": results,
        "iteration_count": state["iteration_count"] + 1,
    }


def _should_continue(state: SubState) -> str:
    if state["iteration_count"] >= MAX_ITERATIONS:
        return "limit_exceeded"
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "execute_tool"
    return END


def _limit_exceeded(state: SubState) -> SubState:
    msg = (
        f":warning: *Análise Profunda — limite de iterações atingido* "
        f"({state['iteration_count']}/{MAX_ITERATIONS})\n"
        "A investigação foi interrompida. Verifique os logs para continuar manualmente."
    )
    _post_slack(msg)
    return state


def _post_slack(text: str) -> None:
    payload = {"channel": SLACK_CHANNEL_ANALISE_ID, "text": text}
    url = f"{SLACK_BASE_URL}/chat.postMessage"
    requests.post(url, json=payload, headers=_SLACK_HEADERS, timeout=30)


def _build_subgraph():
    g = StateGraph(SubState)
    g.add_node("call_llm", _call_llm)
    g.add_node("execute_tool", _execute_tool)
    g.add_node("limit_exceeded", _limit_exceeded)

    g.set_entry_point("call_llm")
    g.add_conditional_edges(
        "call_llm",
        _should_continue,
        {"execute_tool": "execute_tool", "limit_exceeded": "limit_exceeded", END: END},
    )
    g.add_edge("execute_tool", "call_llm")
    g.add_edge("limit_exceeded", END)

    return g.compile()


_subgraph = _build_subgraph()

# ---------------------------------------------------------------------------
# Entrypoint público chamado por tools.solicitar_analise_workflow
# ---------------------------------------------------------------------------

def run_analise_profunda(workflow_nome_origem: str, motivo: str) -> str:
    prompt = (
        f'Investigue o workflow do HubSpot cujo nome interno é aproximadamente: "{workflow_nome_origem}".\n'
        f"Motivo da investigação: {motivo}\n\n"
        "Passos:\n"
        "1. Use listar_workflows_hubspot para ver todos os workflows e encontrar o correspondente.\n"
        "2. Use detalhar_workflow_hubspot com o ID correto para ver a estrutura completa.\n"
        "3. Identifique possíveis causas do problema (campo obrigatório vazio, pipeline errado, etc).\n"
        "4. Monte um relatório com sugestão de correção. NUNCA tente editar o workflow.\n"
        "5. Se a lista vier truncada, chame listar_workflows_hubspot com offset: 100, 200, etc.\n"
        "6. Se após varrer tudo não encontrar, reporte que pode ser app externo (ex: integração n8n)."
    )

    final_state = _subgraph.invoke(
        {
            "messages": [HumanMessage(content=prompt)],
            "iteration_count": 0,
        },
        config={"recursion_limit": 20},
    )

    last = final_state["messages"][-1]
    report_text = getattr(last, "content", str(last))

    # Posta o relatório no canal de análise profunda (espelho do "Slack: Relatório Final" do n8n)
    full_text = f":mag: *Análise Profunda — {workflow_nome_origem}*\n\n{report_text}"
    _post_slack(full_text)

    return report_text
