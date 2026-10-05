"""
Ferramentas do agente CRM HITL.
Cada função é registrada como tool no LangGraph via @tool decorator.
"""

import requests
from datetime import datetime, timedelta, timezone  # noqa: F401
from langchain_core.tools import tool

from config import (
    HUBSPOT_API_TOKEN,
    SLACK_BOT_TOKEN,
    SLACK_CHANNEL_ID,
    HUBSPOT_BASE_URL,
    SLACK_BASE_URL,
)

_HUBSPOT_HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_TOKEN}",
    "Content-Type": "application/json",
}

_SLACK_HEADERS = {
    "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
    "Content-Type": "application/json",
}


def _hubspot_post(path: str, payload: dict) -> dict:
    url = f"{HUBSPOT_BASE_URL}{path}"
    resp = requests.post(url, json=payload, headers=_HUBSPOT_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _hubspot_get(path: str, params: dict | None = None) -> dict:
    url = f"{HUBSPOT_BASE_URL}{path}"
    resp = requests.get(url, params=params, headers=_HUBSPOT_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# 1. buscar_deals_abertos
# ---------------------------------------------------------------------------

@tool
def buscar_deals_abertos(pipeline_id: str = "") -> dict:
    """
    Busca todos os deals com closedWon=false (pipeline aberto) no HubSpot.
    Retorna lista de deals com campos: id, dealname, hubspot_owner_id,
    pipeline, dealstage, createdate, hs_lastmodifieddate, amount,
    hs_all_owner_ids.

    Args:
        pipeline_id: Filtra por pipeline específico (opcional).
                     Se vazio, retorna deals de todos os pipelines.
    """
    properties = [
        "dealname",
        "hubspot_owner_id",
        "pipeline",
        "dealstage",
        "createdate",
        "hs_lastmodifieddate",
        "closedate",
        "amount",
        "hs_all_owner_ids",
        "hs_deal_stage_probability",
    ]

    filters = [
        {
            "propertyName": "hs_is_closed_won",
            "operator": "EQ",
            "value": "false",
        },
        {
            "propertyName": "hs_is_closed",
            "operator": "EQ",
            "value": "false",
        },
    ]

    if pipeline_id:
        filters.append(
            {
                "propertyName": "pipeline",
                "operator": "EQ",
                "value": pipeline_id,
            }
        )

    deals: list[dict] = []
    after: str | None = None

    while True:
        payload: dict = {
            "filterGroups": [{"filters": filters}],
            "properties": properties,
            "limit": 100,
            "sorts": [
                {
                    "propertyName": "hs_lastmodifieddate",
                    "direction": "DESCENDING",
                }
            ],
        }
        if after:
            payload["after"] = after

        data = _hubspot_post("/crm/v3/objects/deals/search", payload)

        results = data.get("results", [])
        deals.extend(
            {
                "id": d["id"],
                **d.get("properties", {}),
            }
            for d in results
        )

        paging = data.get("paging", {})
        next_page = paging.get("next", {})
        after = next_page.get("after")
        if not after:
            break

    return {
        "total": len(deals),
        "deals": deals,
    }


# ---------------------------------------------------------------------------
# 2. buscar_contatos_batch
# ---------------------------------------------------------------------------

@tool
def buscar_contatos_batch(deal_ids: list[str]) -> dict:
    """
    Busca os contatos associados a uma lista de deal IDs via batch associations API.

    Args:
        deal_ids: Lista de IDs de deals (strings).
    """
    if not deal_ids:
        return {"associations": {}}

    # Processa em lotes de 100 (limite da API)
    associations: dict[str, list[str]] = {}

    for i in range(0, len(deal_ids), 100):
        batch = deal_ids[i:i + 100]
        payload = {"inputs": [{"id": did} for did in batch]}
        data = _hubspot_post(
            "/crm/v4/associations/deals/contacts/batch/read", payload
        )
        for result in data.get("results", []):
            deal_id = result.get("from", {}).get("id")
            contact_ids = [a["toObjectId"] for a in result.get("to", [])]
            if deal_id:
                associations[str(deal_id)] = [str(c) for c in contact_ids]

    return {"associations": associations}


# ---------------------------------------------------------------------------
# 3. buscar_detalhes_contatos
# ---------------------------------------------------------------------------

@tool
def buscar_detalhes_contatos(contact_ids: list[str]) -> dict:
    """
    Busca propriedades dos contatos em batch: owner, email, nome e telefone.

    Args:
        contact_ids: Lista de IDs de contatos (strings).
    """
    if not contact_ids:
        return {"contacts": {}}

    properties = ["hubspot_owner_id", "email", "firstname", "lastname", "phone"]
    contacts: dict[str, dict] = {}

    for i in range(0, len(contact_ids), 100):
        batch = contact_ids[i:i + 100]
        payload = {
            "properties": properties,
            "inputs": [{"id": cid} for cid in batch],
        }
        data = _hubspot_post("/crm/v3/objects/contacts/batch/read", payload)
        for c in data.get("results", []):
            contacts[str(c["id"])] = c.get("properties", {})

    return {"contacts": contacts}


# ---------------------------------------------------------------------------
# 4. postar_canal_slack
# ---------------------------------------------------------------------------

@tool
def postar_canal_slack(texto: str) -> dict:
    """
    Posta uma mensagem no canal #marketing-comercial do Slack.

    Args:
        texto: Texto da mensagem (suporta Slack mrkdwn).
    """
    payload = {"channel": SLACK_CHANNEL_ID, "text": texto}
    url = f"{SLACK_BASE_URL}/chat.postMessage"
    resp = requests.post(url, json=payload, headers=_SLACK_HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack error: {data.get('error')}")
    return {"ok": True, "ts": data.get("ts"), "channel": data.get("channel")}


# ---------------------------------------------------------------------------
# 5. buscar_historico_slack
# ---------------------------------------------------------------------------

@tool
def buscar_historico_slack(dias: int = 30) -> dict:
    """
    Busca o histórico de mensagens do canal Slack dos últimos N dias.

    Args:
        dias: Janela de busca em dias (padrão 30).
    """
    import time
    oldest = str(time.time() - dias * 86400)

    params = {
        "channel": SLACK_CHANNEL_ID,
        "oldest": oldest,
        "limit": 100,
    }
    url = f"{SLACK_BASE_URL}/conversations.history"
    resp = requests.get(url, params=params, headers=_SLACK_HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack error: {data.get('error')}")

    messages = [
        {"ts": m.get("ts"), "text": m.get("text", "")[:300]}
        for m in data.get("messages", [])
        if m.get("subtype") is None
    ]
    return {"messages": messages, "total": len(messages)}


# ---------------------------------------------------------------------------
# 6. solicitar_analise_workflow
# ---------------------------------------------------------------------------

@tool
def solicitar_analise_workflow(contexto: str) -> dict:
    """
    Aciona o subagente de análise profunda de inconsistências de workflow HubSpot.

    Args:
        contexto: Resumo dos deals problemáticos e nome do workflow suspeito.
    """
    from subagent import run_analise_profunda

    report = run_analise_profunda(
        workflow_nome_origem="workflow CRM",
        motivo=contexto,
    )
    return {"relatorio": report}


# ---------------------------------------------------------------------------
# 7. calcular_metricas_pipeline  (Analytics — Passo 5)
# ---------------------------------------------------------------------------

@tool
def calcular_metricas_pipeline(deals: list[dict]) -> dict:
    """
    Calcula métricas de saúde do pipeline a partir da lista de deals já buscados.
    Retorna health score (0-100), distribuição por estágio e contagens de problemas.

    Args:
        deals: Lista de deals retornada por buscar_deals_abertos.
    """
    total = len(deals)
    if total == 0:
        return {"health_score": 100, "total": 0}

    agora = datetime.now(timezone.utc)
    sem_owner = 0
    sem_atividade = 0
    close_date_vencida = 0
    sem_valor = 0
    por_estagio: dict[str, int] = {}

    for deal in deals:
        sem_owner += 1 if not deal.get("hubspot_owner_id") else 0

        ultima_mod = deal.get("hs_lastmodifieddate")
        if ultima_mod:
            try:
                dt = datetime.fromisoformat(ultima_mod.replace("Z", "+00:00"))
                if (agora - dt).days > 14:
                    sem_atividade += 1
            except Exception:
                pass

        close_date = deal.get("closedate")
        if close_date:
            try:
                dt = datetime.fromisoformat(close_date.replace("Z", "+00:00"))
                if dt < agora:
                    close_date_vencida += 1
            except Exception:
                pass

        try:
            if not deal.get("amount") or float(deal.get("amount") or 0) == 0:
                sem_valor += 1
        except (ValueError, TypeError):
            sem_valor += 1

        estagio = deal.get("dealstage", "desconhecido")
        por_estagio[estagio] = por_estagio.get(estagio, 0) + 1

    problemas = sem_owner + sem_atividade + close_date_vencida
    health_score = max(0, round(100 - (problemas / total) * 100))

    return {
        "total": total,
        "health_score": health_score,
        "sem_owner": sem_owner,
        "sem_atividade_14d": sem_atividade,
        "close_date_vencida": close_date_vencida,
        "sem_valor": sem_valor,
        "por_estagio": por_estagio,
    }


# ---------------------------------------------------------------------------
# 8. sugerir_acoes_corretivas  (HITL — Passo 4)
# ---------------------------------------------------------------------------

@tool
def sugerir_acoes_corretivas(acoes: list[dict]) -> dict:
    """
    Posta no Slack uma lista de ações corretivas sugeridas aguardando aprovação humana.
    O agente NUNCA executa essas ações sozinho — apenas sugere e aguarda resposta.

    Args:
        acoes: Lista de dicts com campos: tipo, deal_id, dealname, descricao.
               Exemplo: {"tipo": "atribuir_owner", "deal_id": "123",
                         "dealname": "Loja X", "descricao": "Atribuir owner: João"}
    """
    if not acoes:
        return {"ok": True, "mensagem": "Nenhuma ação corretiva necessária."}

    linhas = ["🔧 *Ações Corretivas Sugeridas — Aguardando Aprovação Humana*\n"]
    for i, acao in enumerate(acoes, 1):
        dealname = acao.get("dealname") or acao.get("deal_id", "?")
        descricao = acao.get("descricao") or acao.get("tipo", "?")
        linhas.append(f"{i}. *{dealname}* — {descricao}")

    linhas.append(
        "\n_Responda neste thread com ✅ para aprovar todas ou liste os números desejados._"
        "\n_O agente não realizará nenhuma alteração sem aprovação explícita._"
    )

    texto = "\n".join(linhas)
    payload = {"channel": SLACK_CHANNEL_ID, "text": texto}
    url = f"{SLACK_BASE_URL}/chat.postMessage"
    resp = requests.post(url, json=payload, headers=_SLACK_HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack error: {data.get('error')}")

    return {"ok": True, "ts": data.get("ts"), "acoes_sugeridas": len(acoes)}


# Exporta lista de tools para o agente
ALL_TOOLS = [
    buscar_deals_abertos,
    buscar_contatos_batch,
    buscar_detalhes_contatos,
    postar_canal_slack,
    buscar_historico_slack,
    solicitar_analise_workflow,
    calcular_metricas_pipeline,
    sugerir_acoes_corretivas,
]
