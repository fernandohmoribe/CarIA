from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from dealership_config import DEALERSHIP_STAFF_PHONE
from database import (
    STATUS_LEAD_FECHADOS,
    Lead,
    STATUS_LEAD_MANUAIS,
    STATUS_LEAD_SILENCIADOS,
    SessionLocal,
    agora_utc,
    criar_lead_apos_encerramento,
    obter_conversa,
    obter_historico_conversa_do_lead,
    obter_status_lead_mais_recente,
    obter_ou_criar_loja,
    salvar_conversa,
    definir_status_lead,
)


def _make_loja(db, nome="Loja Teste"):
    return obter_ou_criar_loja(db, nome=nome, tipo_conector="supabase", config_conector={})


def test_manual_and_closed_status_lists_are_consistent():
    # contatado/convertido/perdido precisam estar nos dois — são editáveis manualmente E fecham o lead
    assert STATUS_LEAD_FECHADOS == {"contatado", "convertido", "perdido"}
    assert STATUS_LEAD_FECHADOS.issubset(set(STATUS_LEAD_MANUAIS))
    assert "novo" not in STATUS_LEAD_MANUAIS
    assert "qualificado" not in STATUS_LEAD_MANUAIS
    assert "agendado" not in STATUS_LEAD_FECHADOS  # cliente pode ter dúvida antes da visita


def test_silenced_includes_transferido_but_transferido_is_not_closed():
    # transferido silencia o bot (IA já desistiu), mas não é "fechado": não reseta conversa
    # nem cria lead novo — é o mesmo atendimento, só esperando um humano assumir.
    assert STATUS_LEAD_SILENCIADOS == {"transferido", "contatado", "convertido", "perdido"}
    assert "transferido" in STATUS_LEAD_SILENCIADOS
    assert "transferido" not in STATUS_LEAD_FECHADOS


def test_set_lead_status_closed_resets_active_conversation():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Fechado")
    telefone = "5544900000101@c.us"
    lead = Lead(loja_id=loja.id, numero_telefone=telefone, status="agendado")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    salvar_conversa(db, telefone, [{"role": "user", "content": "oi"}])
    assert obter_conversa(db, telefone) != []

    definir_status_lead(db, lead, "convertido")

    assert lead.status == "convertido"
    assert obter_conversa(db, telefone) == []  # sessão foi resetada
    db.close()


def test_set_lead_status_non_closed_keeps_conversation():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Nao Fechado")
    telefone = "5544900000102@c.us"
    lead = Lead(loja_id=loja.id, numero_telefone=telefone, status="novo")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    salvar_conversa(db, telefone, [{"role": "user", "content": "oi"}])
    definir_status_lead(db, lead, "agendado")

    assert lead.status == "agendado"
    assert obter_conversa(db, telefone) != []
    db.close()


def test_set_lead_status_transferido_silences_without_resetting_conversation():
    # transferido silencia o bot (STATUS_LEAD_SILENCIADOS), mas não é FECHADO — a conversa
    # continua intacta, é o mesmo atendimento esperando um humano assumir.
    db = SessionLocal()
    loja = _make_loja(db, "Loja Transferido")
    telefone = "5544900000108@c.us"
    lead = Lead(loja_id=loja.id, numero_telefone=telefone, status="novo")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    salvar_conversa(db, telefone, [{"role": "user", "content": "quero falar com uma pessoa"}])
    definir_status_lead(db, lead, "transferido")

    assert lead.status == "transferido"
    assert obter_conversa(db, telefone) != []  # conversa NÃO foi resetada
    db.close()


def test_get_latest_lead_status_prefers_most_recent():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Ordem")
    telefone = "5544900000103@c.us"

    older = Lead(loja_id=loja.id, numero_telefone=telefone, status="perdido")
    db.add(older)
    db.commit()
    db.refresh(older)
    older.criado_em = agora_utc() - timedelta(days=1)
    db.commit()

    newer = Lead(loja_id=loja.id, numero_telefone=telefone, status="novo")
    db.add(newer)
    db.commit()

    assert obter_status_lead_mais_recente(db, loja.id, telefone) == "novo"
    db.close()


def test_get_latest_lead_status_none_when_no_lead():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Vazia")
    assert obter_status_lead_mais_recente(db, loja.id, "5544900000199@c.us") is None
    db.close()


def test_create_lead_after_closure_creates_fresh_novo_lead():
    db = SessionLocal()
    loja = _make_loja(db, "Loja Reengajamento")
    telefone = "5544900000104@c.us"

    lead = criar_lead_apos_encerramento(db, loja.id, telefone, "convertido")

    assert lead.status == "novo"
    assert lead.loja_id == loja.id
    assert "convertido" in lead.observacoes
    db.close()


def test_admin_can_change_status_via_route():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    client.post("/admin/login", data={"nome_usuario": "admin", "senha": "test-password"})

    db = SessionLocal()
    loja = _make_loja(db, "Loja Painel")
    lead = Lead(loja_id=loja.id, numero_telefone="5544900000105@c.us", status="novo")
    db.add(lead)
    db.commit()
    lead_id = lead.id
    db.close()

    resp = client.post(f"/admin/leads/{lead_id}/status", data={"status": "contatado"}, follow_redirects=False)
    assert resp.status_code == 302

    db = SessionLocal()
    updated = db.query(Lead).filter(Lead.id == lead_id).first()
    assert updated.status == "contatado"
    db.close()


def test_admin_rejects_status_outside_manual_list():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    client.post("/admin/login", data={"nome_usuario": "admin", "senha": "test-password"})

    db = SessionLocal()
    loja = _make_loja(db, "Loja Rejeita")
    lead = Lead(loja_id=loja.id, numero_telefone="5544900000106@c.us", status="novo")
    db.add(lead)
    db.commit()
    lead_id = lead.id
    db.close()

    # "qualificado" não é status manual — não deve ser aceito vindo do painel
    client.post(f"/admin/leads/{lead_id}/status", data={"status": "qualificado"}, follow_redirects=False)

    db = SessionLocal()
    unchanged = db.query(Lead).filter(Lead.id == lead_id).first()
    assert unchanged.status == "novo"
    db.close()


def test_webhook_silences_bot_for_closed_lead_and_creates_followup():
    import time

    from fastapi.testclient import TestClient
    import main
    from database import obter_loja_padrao

    db = SessionLocal()
    # obter_loja_padrao() sempre pega a primeira loja do banco — usa a mesma que o
    # webhook de verdade vai resolver, em vez de criar uma loja isolada pro teste.
    loja = obter_loja_padrao(db) or _make_loja(db, "Loja Webhook")
    loja_id = loja.id
    numero_telefone = "5544900000107"
    telefone = f"{numero_telefone}@c.us"
    lead = Lead(loja_id=loja_id, numero_telefone=telefone, status="perdido")
    db.add(lead)
    db.commit()
    db.close()

    payload = {
        "event": "message",
        "payload": {
            "fromMe": False,
            "from": telefone,
            "hasMedia": False,
            "body": "oi, ainda quero saber de outro carro",
            "_data": {"notifyName": "Cliente Antigo"},
        },
    }

    with patch.object(main, "obter_resposta_ia") as mock_ai, \
         patch.object(main, "enviar_mensagem", new=AsyncMock()) as mock_send, \
         patch.object(main, "definir_digitando", new=AsyncMock()):
        client = TestClient(main.app)
        resp = client.post("/webhook/whatsapp", json=payload)
        assert resp.status_code == 200

        # a resposta do webhook não espera o asyncio.create_task terminar (é fire-and-forget,
        # de propósito, pra não travar o WAHA) — dá um tempo pro background task rodar.
        time.sleep(0.3)

        mock_ai.assert_not_called()  # bot não deve processar a mensagem via IA

    db = SessionLocal()
    leads = db.query(Lead).filter(Lead.loja_id == loja_id, Lead.numero_telefone == telefone).all()
    db.close()
    assert len(leads) == 2  # o antigo "perdido" + o novo criado pro atendente revisar
    assert any(l.status == "novo" for l in leads)


def test_webhook_silences_bot_for_transferido_without_courtesy_or_followup():
    import time

    from fastapi.testclient import TestClient
    import main
    from database import obter_loja_padrao

    db = SessionLocal()
    loja = obter_loja_padrao(db) or _make_loja(db, "Loja Webhook Transferido")
    loja_id = loja.id
    telefone = "5544900000109@c.us"
    lead = Lead(loja_id=loja_id, numero_telefone=telefone, status="transferido")
    db.add(lead)
    db.commit()
    db.close()

    payload = {
        "event": "message",
        "payload": {
            "fromMe": False,
            "from": telefone,
            "hasMedia": False,
            "body": "alo? cade o vendedor",
            "_data": {"notifyName": "Cliente Impaciente"},
        },
    }

    with patch.object(main, "obter_resposta_ia") as mock_ai, \
         patch.object(main, "enviar_mensagem", new=AsyncMock()) as mock_send, \
         patch.object(main, "definir_digitando", new=AsyncMock()):
        client = TestClient(main.app)
        resp = client.post("/webhook/whatsapp", json=payload)
        assert resp.status_code == 200
        time.sleep(0.3)

        mock_ai.assert_not_called()  # bot não processa via IA
        mock_send.assert_not_called()  # e não manda nenhuma mensagem, nem cortesia

    db = SessionLocal()
    leads = db.query(Lead).filter(Lead.loja_id == loja_id, Lead.numero_telefone == telefone).all()
    db.close()
    assert len(leads) == 1  # não cria lead novo — é o mesmo atendimento aguardando o vendedor


# um telefone fixo por status, pra não colidir entre execuções do parametrize no mesmo banco
_STATUS_TEST_PHONES = {
    "novo": "5544900000200",
    "qualificado": "5544900000201",
    "agendado": "5544900000202",
    "transferido": "5544900000203",
    "contatado": "5544900000204",
    "convertido": "5544900000205",
    "perdido": "5544900000206",
}


@pytest.mark.parametrize("status", list(_STATUS_TEST_PHONES.keys()))
def test_webhook_ai_called_only_for_non_silenced_statuses(status):
    """Cobertura explícita dos 7 status: confirma se o bot chama a IA (responde) ou não,
    pra cada um — não só os casos de silêncio, mas também os 3 que devem responder normal."""
    import time

    from fastapi.testclient import TestClient
    import main
    from database import obter_loja_padrao

    db = SessionLocal()
    loja = obter_loja_padrao(db) or _make_loja(db, "Loja Parametrizada")
    loja_id = loja.id
    telefone = f"{_STATUS_TEST_PHONES[status]}@c.us"
    lead = Lead(loja_id=loja_id, numero_telefone=telefone, status=status)
    db.add(lead)
    db.commit()
    db.close()

    payload = {
        "event": "message",
        "payload": {
            "fromMe": False,
            "from": telefone,
            "hasMedia": False,
            "body": "quero saber sobre um carro",
            "_data": {"notifyName": "Cliente Parametrizado"},
        },
    }

    with patch.object(main, "obter_resposta_ia") as mock_ai, \
         patch.object(main, "enviar_mensagem", new=AsyncMock()), \
         patch.object(main, "definir_digitando", new=AsyncMock()):
        mock_ai.return_value = ("resposta de teste", None, None)
        client = TestClient(main.app)
        resp = client.post("/webhook/whatsapp", json=payload)
        assert resp.status_code == 200
        time.sleep(0.3)

        if status in STATUS_LEAD_SILENCIADOS:
            mock_ai.assert_not_called()
        else:
            mock_ai.assert_called_once()


def test_processar_mensagem_nunca_manda_whatsapp_pro_vendedor():
    """O bot já mandou mensagem de "novo lead"/"lead quente" pro WhatsApp do vendedor
    (DEALERSHIP_STAFF_PHONE) toda vez que um lead era criado/atualizado — isso é justamente
    o tipo de "reach out" pra um contato novo que fez o WhatsApp (conexão não-oficial)
    restringir e derrubar a sessão em produção. Removido de propósito — o vendedor acompanha
    lead novo só pelo quadro kanban do admin (/admin/leads), nunca mais via mensagem
    automática. Testa processar_mensagem direto (não via webhook fire-and-forget, que roda
    em asyncio.create_task e não tem garantia de terminar a tempo fora do event loop real
    da aplicação) — este teste tranca esse comportamento pra não voltar sem querer."""
    import asyncio

    import main

    assert DEALERSHIP_STAFF_PHONE, "precisa estar configurado no .env pro teste valer alguma coisa"

    telefone = "5544900000112@c.us"

    with patch.object(main, "obter_resposta_ia") as mock_ai, \
         patch.object(main, "enviar_mensagem", new=AsyncMock()) as mock_send, \
         patch.object(main, "definir_digitando", new=AsyncMock()):
        # simula a IA criando um lead novo/quente (o que antes disparava notificar_equipe)
        mock_ai.return_value = (
            "Anotado! Já vou te ajudar 🎯",
            {"nome": None, "prioridade": "quente", "status": "novo"},
            None,
        )
        asyncio.run(main.processar_mensagem(telefone, "quero um carro urgente, tenho um Corolla pra trocar", "Cliente Lead Quente"))

    telefones_notificados = [chamada.args[0] for chamada in mock_send.call_args_list]
    assert DEALERSHIP_STAFF_PHONE not in telefones_notificados
    assert telefones_notificados == [telefone]  # só respondeu o próprio cliente


def test_processar_contato_lead_fechado_nunca_manda_whatsapp_pro_vendedor():
    """Mesma trava do teste acima, mas pro outro call site que existia (reengajamento de
    lead fechado) — também não pode mais mandar WhatsApp pro vendedor."""
    import asyncio

    import main
    from database import obter_loja_padrao

    assert DEALERSHIP_STAFF_PHONE, "precisa estar configurado no .env pro teste valer alguma coisa"

    db = SessionLocal()
    loja = obter_loja_padrao(db) or _make_loja(db, "Loja Reengajamento Sem Notificar")
    loja_id = loja.id
    telefone = "5544900000113@c.us"
    lead = Lead(loja_id=loja_id, numero_telefone=telefone, status="perdido")
    db.add(lead)
    db.commit()
    db.close()

    with patch.object(main, "enviar_mensagem", new=AsyncMock()) as mock_send:
        asyncio.run(main.processar_contato_lead_fechado(telefone, loja_id, "perdido"))

    telefones_notificados = [chamada.args[0] for chamada in mock_send.call_args_list]
    assert DEALERSHIP_STAFF_PHONE not in telefones_notificados
    assert telefones_notificados == [telefone]  # só a cortesia pro próprio cliente


def test_conversation_history_scoped_to_lead_not_mixed_across_reopened_leads():
    """Reproduz o cenário que motivou conversas.lead_id: mesmo telefone gera um lead
    fechado e depois um lead novo (reengajamento) — as conversas de cada um não podem se
    misturar quando alguém abre o lead no painel."""
    import time

    from fastapi.testclient import TestClient
    import main
    from database import obter_loja_padrao

    db = SessionLocal()
    loja = obter_loja_padrao(db) or _make_loja(db, "Loja Escopo Conversa")
    loja_id = loja.id
    telefone = "5544900000110@c.us"
    old_lead = Lead(loja_id=loja_id, numero_telefone=telefone, status="novo")
    db.add(old_lead)
    db.commit()
    db.refresh(old_lead)
    old_lead_id = old_lead.id
    db.close()

    payload = {
        "event": "message",
        "payload": {
            "fromMe": False,
            "from": telefone,
            "hasMedia": False,
            "body": "quero saber sobre um carro",
            "_data": {"notifyName": "Cliente Escopo"},
        },
    }

    # 1) primeira mensagem, ainda no lead antigo
    with patch.object(main, "obter_resposta_ia") as mock_ai, \
         patch.object(main, "enviar_mensagem", new=AsyncMock()), \
         patch.object(main, "definir_digitando", new=AsyncMock()):
        mock_ai.return_value = ("resposta pro lead antigo", None, None)
        client = TestClient(main.app)
        client.post("/webhook/whatsapp", json=payload)
        time.sleep(0.3)

    # 2) vendedor fecha o lead antigo (reseta a sessão)
    db = SessionLocal()
    old_lead = db.query(Lead).filter(Lead.id == old_lead_id).first()
    definir_status_lead(db, old_lead, "perdido")
    db.close()

    # 3) cliente escreve de novo -> silenciado, cria lead novo automaticamente (cortesia)
    with patch.object(main, "obter_resposta_ia") as mock_ai2, \
         patch.object(main, "enviar_mensagem", new=AsyncMock()), \
         patch.object(main, "definir_digitando", new=AsyncMock()):
        client = TestClient(main.app)
        client.post("/webhook/whatsapp", json=payload)
        time.sleep(0.3)
        mock_ai2.assert_not_called()

    db = SessionLocal()
    new_lead = (
        db.query(Lead)
        .filter(Lead.loja_id == loja_id, Lead.numero_telefone == telefone, Lead.status == "novo")
        .first()
    )
    assert new_lead is not None
    new_lead_id = new_lead.id
    db.close()

    # 4) próxima mensagem já processa normal no lead novo
    with patch.object(main, "obter_resposta_ia") as mock_ai3, \
         patch.object(main, "enviar_mensagem", new=AsyncMock()), \
         patch.object(main, "definir_digitando", new=AsyncMock()):
        mock_ai3.return_value = ("resposta pro lead novo", None, None)
        client = TestClient(main.app)
        client.post("/webhook/whatsapp", json=payload)
        time.sleep(0.3)

    db = SessionLocal()
    old_history = obter_historico_conversa_do_lead(db, old_lead_id)
    new_history = obter_historico_conversa_do_lead(db, new_lead_id)
    db.close()

    assert old_lead_id != new_lead_id
    assert len(old_history) >= 1
    assert len(new_history) >= 1
    assert all("resposta pro lead novo" not in c.mensagens_json for c in old_history)
    assert all("resposta pro lead antigo" not in c.mensagens_json for c in new_history)


def test_stale_conversation_expires_and_creates_new_session_for_same_lead():
    """Cenário diferente do reengajamento pós-fechamento: um lead "agendado" (nunca fechado
    manualmente) fica sem interação por mais de 24h — ao voltar, o bot NÃO silencia (status não é
    terminal), mas a sessão antiga expira e uma conversa nova é criada, ligada ao MESMO lead
    (não cria lead novo, porque ninguém fechou esse lead de verdade)."""
    import time
    from datetime import timedelta

    from fastapi.testclient import TestClient
    import main
    from database import Conversa, obter_historico_conversa_do_lead, obter_loja_padrao

    db = SessionLocal()
    loja = obter_loja_padrao(db) or _make_loja(db, "Loja Expiracao")
    loja_id = loja.id
    telefone = "5544900000111@c.us"
    lead = Lead(loja_id=loja_id, numero_telefone=telefone, status="agendado")
    db.add(lead)
    db.commit()
    db.refresh(lead)
    lead_id = lead.id

    # simula uma sessão antiga (mês passado) já ligada a esse lead
    old_conv = Conversa(
        numero_telefone=telefone,
        lead_id=lead_id,
        status="ativa",
        mensagens_json='[{"role": "user", "content": "quero agendar"}]',
        criado_em=agora_utc() - timedelta(days=30),
        atualizado_em=agora_utc() - timedelta(days=30),
    )
    db.add(old_conv)
    db.commit()
    old_conv_id = old_conv.id
    db.close()

    payload = {
        "event": "message",
        "payload": {
            "fromMe": False,
            "from": telefone,
            "hasMedia": False,
            "body": "oi, ainda quero saber sobre esse carro",
            "_data": {"notifyName": "Cliente Antigo Agendado"},
        },
    }

    with patch.object(main, "obter_resposta_ia") as mock_ai, \
         patch.object(main, "enviar_mensagem", new=AsyncMock()), \
         patch.object(main, "definir_digitando", new=AsyncMock()):
        mock_ai.return_value = ("resposta depois de um mês", None, None)
        client = TestClient(main.app)
        resp = client.post("/webhook/whatsapp", json=payload)
        assert resp.status_code == 200
        time.sleep(0.3)
        mock_ai.assert_called_once()  # não foi silenciado — "agendado" não é status fechado

    db = SessionLocal()
    old_conv = db.query(Conversa).filter(Conversa.id == old_conv_id).first()
    assert old_conv.status == "expirada"  # sessão antiga foi marcada como expirada

    # não deve ter criado um lead novo — só um lead esperado (o mesmo de sempre)
    leads = db.query(Lead).filter(Lead.loja_id == loja_id, Lead.numero_telefone == telefone).all()
    assert len(leads) == 1
    assert leads[0].id == lead_id

    historico_conversas = obter_historico_conversa_do_lead(db, lead_id)
    assert len(historico_conversas) == 2  # a antiga (expirada) + a nova sessão
    db.close()


def test_webhook_ignores_duplicate_event_id():
    """WAHA às vezes entrega o mesmo evento 2x (mesmo "id" de nível raiz) — sem dedup, o
    bot processa e responde duas vezes pra mesma mensagem (visto em produção)."""
    import time

    from fastapi.testclient import TestClient
    import main
    from database import obter_loja_padrao

    db = SessionLocal()
    loja = obter_loja_padrao(db) or _make_loja(db, "Loja Dedup")
    loja_id = loja.id
    telefone = "5544900000111@c.us"
    lead = Lead(loja_id=loja_id, numero_telefone=telefone, status="novo")
    db.add(lead)
    db.commit()
    db.close()

    payload = {
        "id": "evt_01teste_duplicado_mesmo_id",
        "event": "message",
        "payload": {
            "fromMe": False,
            "from": telefone,
            "hasMedia": False,
            "body": "oi, quero ver um carro",
            "_data": {"notifyName": "Cliente Duplicado"},
        },
    }

    with patch.object(main, "obter_resposta_ia") as mock_ai, \
         patch.object(main, "enviar_mensagem", new=AsyncMock()), \
         patch.object(main, "definir_digitando", new=AsyncMock()):
        mock_ai.return_value = ("resposta única", None, None)
        client = TestClient(main.app)

        resp1 = client.post("/webhook/whatsapp", json=payload)
        resp2 = client.post("/webhook/whatsapp", json=payload)  # mesma entrega duplicada
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        time.sleep(0.3)

        mock_ai.assert_called_once()  # só processou a primeira vez, apesar de 2 POSTs
