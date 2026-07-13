"""
Migração pontual (roda uma vez, depois pode ser apagado): lê um banco antigo (schema em
inglês, de antes da tradução do código pra português) e insere os dados no banco novo
(schema atual, em português), preservando os IDs originais pra manter os relacionamentos
(FK) intactos entre as tabelas.

Uso:
    python scripts/migrar_banco_pt.py /caminho/pro/banco_antigo.db

O banco de origem é só leitura (sqlite3 puro, nunca é alterado). O banco de destino é o
configurado em DATABASE_URL (mesmo .env do resto do projeto) — cria as tabelas na hora
(Base.metadata.create_all, já roda ao importar database.py) e insere por cima.

Idempotente o suficiente pra um único uso: assume que o banco de destino está vazio (uso
recomendado: apontar DATABASE_URL pra um arquivo novo antes de rodar). Rodar duas vezes
sobre o mesmo destino duplica os dados.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import sqlite3

from database import (
    AvaliacaoGoogle,
    Conversa,
    ImagemVeiculo,
    Lead,
    LeadHistorico,
    Loja,
    Novidade,
    PostInstagram,
    SessionLocal,
    Usuario,
    Veiculo,
)

# active -> ativa, completed -> concluida, expired -> expirada, reset -> reiniciada
_STATUS_CONVERSA = {
    "active": "ativa",
    "completed": "concluida",
    "expired": "expirada",
    "reset": "reiniciada",
}


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _rows(cur, tabela):
    cur.execute(f"SELECT * FROM {tabela}")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def migrar(caminho_origem: str) -> None:
    origem = sqlite3.connect(caminho_origem)
    origem.row_factory = sqlite3.Row
    cur = origem.cursor()

    db = SessionLocal()
    contagens = {}

    try:
        # ── Lojas ────────────────────────────────────────────────────────
        for r in _rows(cur, "dealerships"):
            db.add(Loja(
                id=r["id"], nome=r["nome"], tipo_conector=r["connector_type"],
                config_conector_json=r["connector_config_json"], telefone_equipe=r["staff_phone"],
                ultima_sincronizacao=_parse_dt(r["last_sync_at"]), criado_em=_parse_dt(r["created_at"]),
            ))
        db.commit()
        contagens["lojas"] = len(_rows(cur, "dealerships"))

        # ── Usuários ─────────────────────────────────────────────────────
        for r in _rows(cur, "users"):
            db.add(Usuario(
                id=r["id"], nome_usuario=r["username"], nome=r["nome"],
                senha_hash=r["password_hash"], criado_em=_parse_dt(r["created_at"]),
            ))
        db.commit()
        contagens["usuarios"] = len(_rows(cur, "users"))

        # ── Veículos ─────────────────────────────────────────────────────
        for r in _rows(cur, "vehicles"):
            db.add(Veiculo(
                id=r["id"], loja_id=r["dealership_id"], id_externo=r["external_id"], slug=r["slug"],
                codigo=r["code"], marca=r["brand"], modelo=r["model"], versao=r["version"], ano=r["year"],
                preco=r["price"], quilometragem=r["mileage"], status=r["status"],
                status_publicacao=r["publication_status"], carroceria=r["body"], cambio=r["transmission"],
                combustivel=r["fuel"], cor=r["color"], especificacao=r["spec"], descricao=r["overview"],
                destaques_json=r["highlights_json"], url_imagem_capa=r["cover_image_url"],
                sincronizado_em=_parse_dt(r["synced_at"]), cidade=r["cidade"], final_placa=r["final_placa"],
                blindado=bool(r["blindado"]), aceita_troca=bool(r["aceita_troca"]),
                unico_dono=bool(r["unico_dono"]), revisoes_concessionaria=bool(r["revisoes_concessionaria"]),
                ipva_pago=bool(r["ipva_pago"]), licenciado=bool(r["licenciado"]),
                garantia_fabrica=bool(r["garantia_fabrica"]),
            ))
        db.commit()
        contagens["veiculos"] = len(_rows(cur, "vehicles"))

        # ── Imagens de veículo ───────────────────────────────────────────
        for r in _rows(cur, "vehicle_images"):
            db.add(ImagemVeiculo(
                id=r["id"], veiculo_id=r["vehicle_id"], url_imagem=r["image_url"],
                caminho_local=r["local_path"], eh_capa=bool(r["is_cover"]), ordem=r["sort_order"],
            ))
        db.commit()
        contagens["imagens_veiculo"] = len(_rows(cur, "vehicle_images"))

        # ── Leads ────────────────────────────────────────────────────────
        for r in _rows(cur, "leads"):
            db.add(Lead(
                id=r["id"], loja_id=r["dealership_id"], numero_telefone=r["phone_number"], nome=r["nome"],
                email=r["email"], telefone=r["telefone"], veiculo_interesse=r["veiculo_interesse"],
                veiculo_slug=r["veiculo_slug"], forma_pagamento=r["forma_pagamento"],
                tem_troca=r["tem_troca"] if r["tem_troca"] is None else bool(r["tem_troca"]),
                veiculo_troca_desc=r["veiculo_troca_desc"], orcamento_aproximado=r["orcamento_aproximado"],
                urgencia_compra=r["urgencia_compra"], uso_pretendido=r["uso_pretendido"],
                como_conheceu=r["como_conheceu"], preferencia_contato=r["preferencia_contato"],
                resumo_executivo=r["resumo_executivo"], prioridade=r["prioridade"],
                observacoes=r["observacoes"], status=r["status"], origem=r["origem"],
                criado_em=_parse_dt(r["created_at"]), atualizado_em=_parse_dt(r["updated_at"]),
            ))
        db.commit()
        contagens["leads"] = len(_rows(cur, "leads"))

        # ── Histórico de status do lead ──────────────────────────────────
        for r in _rows(cur, "lead_historico"):
            db.add(LeadHistorico(
                id=r["id"], lead_id=r["lead_id"], usuario_id=r["user_id"],
                status_anterior=r["status_anterior"], status_novo=r["status_novo"],
                observacao=r["observacao"], data=_parse_dt(r["data"]),
            ))
        db.commit()
        contagens["lead_historico"] = len(_rows(cur, "lead_historico"))

        # ── Conversas ────────────────────────────────────────────────────
        for r in _rows(cur, "conversations"):
            db.add(Conversa(
                id=r["id"], numero_telefone=r["phone_number"], lead_id=r["lead_id"],
                status=_STATUS_CONVERSA.get(r["status"], r["status"]), mensagens_json=r["messages_json"],
                criado_em=_parse_dt(r["created_at"]), atualizado_em=_parse_dt(r["updated_at"]),
            ))
        db.commit()
        contagens["conversas"] = len(_rows(cur, "conversations"))

        # ── Novidades ────────────────────────────────────────────────────
        for r in _rows(cur, "news_posts"):
            db.add(Novidade(
                id=r["id"], loja_id=r["dealership_id"], titulo=r["titulo"], slug=r["slug"],
                resumo=r["resumo"], conteudo=r["conteudo"], url_imagem=r["imagem_url"],
                caminho_local_imagem=r["imagem_local_path"], publicado=bool(r["publicado"]),
                criado_em=_parse_dt(r["created_at"]),
            ))
        db.commit()
        contagens["novidades"] = len(_rows(cur, "news_posts"))

        # ── Posts do Instagram ───────────────────────────────────────────
        for r in _rows(cur, "instagram_posts"):
            db.add(PostInstagram(
                id=r["id"], loja_id=r["dealership_id"], id_midia=r["media_id"], legenda=r["caption"],
                tipo_midia=r["media_type"], url_midia=r["media_url"], url_miniatura=r["thumbnail_url"],
                link_permanente=r["permalink"], data_hora=_parse_dt(r["timestamp"]),
                visivel=bool(r["visivel"]), sincronizado_em=_parse_dt(r["synced_at"]),
            ))
        db.commit()
        contagens["posts_instagram"] = len(_rows(cur, "instagram_posts"))

        # ── Avaliações do Google ─────────────────────────────────────────
        for r in _rows(cur, "google_reviews"):
            db.add(AvaliacaoGoogle(
                id=r["id"], loja_id=r["dealership_id"], nome_autor=r["author_name"],
                url_foto_perfil=r["profile_photo_url"], nota=r["rating"], texto=r["text"],
                tempo_relativo=r["relative_time_description"], sincronizado_em=_parse_dt(r["synced_at"]),
            ))
        db.commit()
        contagens["avaliacoes_google"] = len(_rows(cur, "google_reviews"))
    finally:
        db.close()
        origem.close()

    print("Migração concluída:")
    for tabela, n in contagens.items():
        print(f"  {tabela}: {n} linha(s)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python scripts/migrar_banco_pt.py /caminho/pro/banco_antigo.db")
        sys.exit(1)
    migrar(sys.argv[1])
