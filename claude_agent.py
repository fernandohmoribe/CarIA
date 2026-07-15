import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

import anthropic

import inventory
from database import SessionLocal, atualizar_lead, lead_para_dict, obter_loja_padrao, obter_ou_criar_lead
from dealership_config import BUSINESS_TZ, SYSTEM_PROMPT

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1300
MAX_HISTORY_MESSAGES = 20
MAX_TOOL_ITERATIONS = 6

# A Anthropic rejeita (400) qualquer mensagem futura na mesma conversa se um bloco de texto
# vazio for salvo no histórico — visto em produção: a resposta final saiu "" (causa exata não
# confirmada), ficou salva assim, e todas as mensagens seguintes desse cliente passaram a falhar
# com o mesmo erro 400 até a conversa expirar/reiniciar. Nunca deixa um texto vazio nem ir pro
# cliente nem ser persistido — corta o problema na raiz independente da causa.
MENSAGEM_RESPOSTA_VAZIA = "Desculpa, tive uma falha ao montar a resposta agora — pode repetir sua pergunta? 🙏"

DIAS_SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]

PRICE_INPUT = 1.00
PRICE_OUTPUT = 5.00
PRICE_CACHE_WRITE = 1.25
PRICE_CACHE_READ = 0.10

logger = logging.getLogger(__name__)
_client = anthropic.Anthropic()

LEAD_TOOL = {
    "name": "criar_ou_atualizar_lead",
    "description": (
        "Cria (na primeira chamada) ou atualiza o cadastro do lead do cliente atual. "
        "Chame sempre que houver informação nova relevante — não espere o fim da conversa."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "nome": {"type": "string"},
            "email": {"type": "string"},
            "telefone": {"type": "string"},
            "veiculo_interesse": {"type": "string", "description": "Ex: 'BMW X5 xDrive45e'"},
            "veiculo_slug": {"type": "string", "description": "Slug do veículo, se conhecido de uma busca anterior"},
            "forma_pagamento": {"type": "string", "description": "à vista ou financiado"},
            "tem_troca": {"type": "boolean"},
            "veiculo_troca_desc": {"type": "string"},
            "orcamento_aproximado": {"type": "string"},
            "urgencia_compra": {"type": "string"},
            "uso_pretendido": {"type": "string"},
            "como_conheceu": {"type": "string"},
            "dia_visita": {
                "type": "string",
                "enum": ["hoje", "amanhã", *DIAS_SEMANA],
                "description": (
                    "Dia que o cliente mencionou pra visita/test-drive, só o NOME do dia (ou "
                    "'hoje'/'amanhã') — NUNCA calcule a data (dia/mês) você mesmo, é conta que "
                    "você erra com frequência. O código resolve a data exata a partir desse "
                    "valor. Use isso sempre que o cliente citar um dia da semana."
                ),
            },
            "periodo_visita": {
                "type": "string", "enum": ["manhã", "tarde"],
                "description": "Período do dia da visita, se o cliente mencionar",
            },
            "preferencia_contato": {
                "type": "string",
                "description": (
                    "SÓ use quando NÃO for um dia da semana simples (ex: data específica que o "
                    "cliente já deu pronta tipo '15 de agosto', ou algo vago tipo 'depois das "
                    "férias') — nesse caso raro, descreva em texto livre. Se o cliente citou um "
                    "dia da semana, use `dia_visita` + `periodo_visita` em vez deste campo."
                ),
            },
            "resumo_executivo": {"type": "string", "description": "Resumo curto (3-4 linhas) pro vendedor"},
            "observacoes": {"type": "string"},
            "status": {
                "type": "string",
                "description": "novo | qualificado | agendado | transferido | contatado | convertido | perdido",
            },
        },
    },
}

# cache_control no último tool marca um breakpoint de cache que cobre TODAS as
# definições de tools acumuladas até aqui — somado ao system prompt (que vem
# logo depois, na contagem de tokens da Anthropic), passa do mínimo cacheável
# do Haiku (2048 tokens), o que o system prompt sozinho não atingia.
LEAD_TOOL["cache_control"] = {"type": "ephemeral"}

TOOLS = inventory.TOOLS + [LEAD_TOOL]

_INDICE_DIA_SEMANA = {nome: i for i, nome in enumerate(DIAS_SEMANA)}


def _resolver_dia_visita(dia_visita: Optional[str], periodo_visita: Optional[str]) -> Optional[str]:
    """Resolve dia_visita ('quinta-feira', 'hoje', 'amanhã') pra uma data concreta calculada em
    Python — a IA só diz QUAL dia da semana, nunca a data — isso é aritmética simples que ela
    erra de vez em quando (já vimos calcular "quinta que vem" e cair numa sexta)."""
    if not dia_visita:
        return None
    agora = datetime.now(BUSINESS_TZ)
    chave = dia_visita.strip().lower()
    if chave == "hoje":
        alvo = agora
    elif chave in ("amanhã", "amanha"):
        alvo = agora + timedelta(days=1)
    elif chave in _INDICE_DIA_SEMANA:
        delta = (_INDICE_DIA_SEMANA[chave] - agora.weekday()) % 7
        # "quinta-feira" citada quando hoje já é quinta significa a quinta que vem, não hoje —
        # pra hoje mesmo a IA usa o valor "hoje", que cai no primeiro if acima.
        delta = delta or 7
        alvo = agora + timedelta(days=delta)
    else:
        return None

    resolvido = f"{DIAS_SEMANA[alvo.weekday()]}, {alvo.strftime('%d/%m/%Y')}"
    if periodo_visita:
        resolvido += f" de {periodo_visita}"
    return resolvido


def _processar_tool_lead(entrada_tool: dict, loja_id: int, telefone: str) -> dict:
    dia_visita = entrada_tool.pop("dia_visita", None)
    periodo_visita = entrada_tool.pop("periodo_visita", None)
    resolvido = _resolver_dia_visita(dia_visita, periodo_visita)
    if resolvido:
        entrada_tool["preferencia_contato"] = resolvido

    db = SessionLocal()
    try:
        lead, eh_novo = obter_ou_criar_lead(db, loja_id, telefone)
        status_antes = lead.status
        preferencia_antes = lead.preferencia_contato

        lead = atualizar_lead(db, lead, entrada_tool)

        notificar = (
            eh_novo
            or lead.status != status_antes
            or (entrada_tool.get("preferencia_contato") and not preferencia_antes)
            or lead.prioridade == "quente"
        )
        resultado = lead_para_dict(lead)
        resultado["_notify"] = notificar
        resultado["_is_new"] = eh_novo
        return resultado
    finally:
        db.close()


def _blocos_conteudo(content) -> list:
    """Normaliza content (string, blocks do SDK ou lista de dicts) pra lista de dicts —
    necessário pra poder anexar cache_control num bloco específico."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [block.model_dump(exclude_none=True) if hasattr(block, "model_dump") else dict(block) for block in content]


def _atualizar_ponto_cache(mensagens_api: list) -> None:
    """Move o breakpoint de cache pro fim do histórico atual, removendo o anterior.

    Sem isso, cada chamada extra do loop de tool use (e o turno seguinte, que reenvia
    o mesmo histórico) paga preço cheio de input em vez de cache read (~10x mais barato)
    pra reenviar o mesmo prefixo de conversa repetidamente.
    """
    for msg in mensagens_api:
        for block in msg["content"]:
            block.pop("cache_control", None)
    if mensagens_api:
        mensagens_api[-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}


def _processar_tool_fotos(entrada_tool: dict, loja_id: int) -> dict:
    data = inventory.listar_fotos_veiculo(loja_id=loja_id, slug=entrada_tool.get("slug", ""))
    if data.get("erro") or not data.get("fotos"):
        return {"erro": data.get("erro") or "Nenhuma foto encontrada pra esse veículo."}
    return {
        "veiculo": data["veiculo"],
        "fotos_enviadas": len(data["fotos"]),
        # chave privada, removida antes de mandar o resultado pro Claude — carrega os
        # caminhos reais só pra camada de envio (main.py/admin), nunca pro texto da conversa.
        # Regra do projeto: a IA NUNCA recebe imagem (nem base64, nem bloco de imagem da API) —
        # só o nome do arquivo/contagem em texto. Enviar imagem pro modelo custa MUITO mais
        # tokens (visão) do que texto — ver CLAUDE.md.
        "_fotos": data["fotos"],
    }


def _despachar_tool(name: str, entrada_tool: dict, loja_id: int, telefone: str) -> dict:
    if name == "buscar_veiculos":
        return inventory.buscar_veiculos(loja_id=loja_id, **entrada_tool)
    if name == "detalhes_veiculo":
        return inventory.detalhes_veiculo(loja_id=loja_id, **entrada_tool)
    if name == "enviar_fotos_veiculo":
        return _processar_tool_fotos(entrada_tool, loja_id)
    if name == "criar_ou_atualizar_lead":
        return _processar_tool_lead(entrada_tool, loja_id, telefone)
    return {"erro": f"tool desconhecida: {name}"}


def obter_resposta_ia(
    mensagens: list,
    mensagem_usuario: str,
    telefone: str,
    nome_exibicao: str = "",
) -> Tuple[str, Optional[dict], Optional[dict]]:
    """
    Gera resposta da IA, executando o loop de tool use (consulta de estoque e
    gestão de lead) quando necessário.

    Retorna: (texto_resposta, lead_para_notificar | None, fotos_para_enviar | None)
    """
    primeira_mensagem = mensagem_usuario
    if nome_exibicao and not mensagens:
        primeira_mensagem = f"[Cliente: {nome_exibicao}] {mensagem_usuario}"

    mensagens_api = [{"role": m["role"], "content": _blocos_conteudo(m["content"])} for m in mensagens]
    mensagens_api.append({"role": "user", "content": _blocos_conteudo(primeira_mensagem)})

    if len(mensagens_api) > MAX_HISTORY_MESSAGES:
        mensagens_api = mensagens_api[-MAX_HISTORY_MESSAGES:]

    agora = datetime.now(BUSINESS_TZ)
    dia_semana = DIAS_SEMANA[agora.weekday()]
    periodo = "manhã" if agora.hour < 12 else "tarde" if agora.hour < 18 else "noite"
    hoje = f"{dia_semana}, {agora.strftime('%d/%m/%Y')}, {periodo} (horário de Brasília)"
    system_com_data = f"Hoje é {hoje}.\n\n{SYSTEM_PROMPT}"

    db = SessionLocal()
    try:
        loja = obter_loja_padrao(db)
    finally:
        db.close()
    loja_id = loja.id if loja else None

    lead_para_notificar = None
    fotos_para_enviar = None

    for _ in range(MAX_TOOL_ITERATIONS):
        _atualizar_ponto_cache(mensagens_api)
        response = _client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": system_com_data, "cache_control": {"type": "ephemeral"}}],
            messages=mensagens_api,
            tools=TOOLS,
        )
        _registrar_uso(response.usage, telefone)

        if response.stop_reason != "tool_use":
            texto_final = "".join(block.text for block in response.content if block.type == "text").strip()
            return texto_final or MENSAGEM_RESPOSTA_VAZIA, lead_para_notificar, fotos_para_enviar

        # Turno intermediário (ainda vai chamar mais tool): o texto que vem junto ("vou
        # verificar...", "aqui está a ficha completa:") é descartado de propósito — só o texto
        # do turno final (acima) chega pro cliente. Sem isso, cada narração de "vou fazer X"
        # entre uma tool call e outra se acumulava e ia toda pro WhatsApp.
        mensagens_api.append({"role": "assistant", "content": _blocos_conteudo(response.content)})
        resultados_tool = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            resultado = _despachar_tool(block.name, block.input, loja_id, telefone)
            if block.name == "criar_ou_atualizar_lead" and resultado.get("_notify"):
                lead_para_notificar = resultado
            if block.name == "enviar_fotos_veiculo" and "_fotos" in resultado:
                fotos_para_enviar = {"veiculo": resultado.get("veiculo"), "fotos": resultado.pop("_fotos")}
            resultados_tool.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(resultado, ensure_ascii=False, default=str),
                }
            )
        mensagens_api.append({"role": "user", "content": resultados_tool})

    # Estourou o limite de iterações de tool use: força uma resposta final sem tools
    _atualizar_ponto_cache(mensagens_api)
    response = _client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[{"type": "text", "text": system_com_data, "cache_control": {"type": "ephemeral"}}],
        messages=mensagens_api,
    )
    texto_final = "".join(block.text for block in response.content if block.type == "text").strip()
    return texto_final or MENSAGEM_RESPOSTA_VAZIA, lead_para_notificar, fotos_para_enviar


def _registrar_uso(usage, telefone: str):
    input_tok = getattr(usage, "input_tokens", 0) or 0
    output_tok = getattr(usage, "output_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

    custo = (
        input_tok * PRICE_INPUT / 1_000_000
        + output_tok * PRICE_OUTPUT / 1_000_000
        + cache_write * PRICE_CACHE_WRITE / 1_000_000
        + cache_read * PRICE_CACHE_READ / 1_000_000
    )

    logger.info(
        f"[CUSTO] {telefone} | "
        f"in={input_tok} out={output_tok} "
        f"cache_w={cache_write} cache_r={cache_read} | "
        f"${custo:.6f} (~R${custo * 5.5:.4f})"
    )
