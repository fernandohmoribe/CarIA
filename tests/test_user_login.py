import os

from database import (
    SessionLocal,
    criar_usuario_com_senha,
    obter_ou_criar_usuario,
    gerar_hash_senha,
    verificar_senha,
    verificar_credenciais_usuario,
)


def test_hash_password_roundtrip():
    hashed = gerar_hash_senha("senha-forte-123")
    assert verificar_senha("senha-forte-123", hashed) is True


def test_hash_password_wrong_password_fails():
    hashed = gerar_hash_senha("senha-forte-123")
    assert verificar_senha("senha-errada", hashed) is False


def test_hash_password_uses_random_salt_each_time():
    # mesma senha, hashes diferentes — nunca dá pra comparar hash direto (evita rainbow table)
    assert gerar_hash_senha("mesma-senha") != gerar_hash_senha("mesma-senha")


def test_verify_password_rejects_malformed_hash():
    assert verificar_senha("qualquer", "hash-sem-separador") is False
    assert verificar_senha("qualquer", None) is False


def test_create_user_with_password_new_user():
    db = SessionLocal()
    usuario = criar_usuario_com_senha(db, "vendedor_novo_teste", "senha123", "Vendedor Teste")
    assert usuario.nome_usuario == "vendedor_novo_teste"
    assert usuario.nome == "Vendedor Teste"
    assert usuario.senha_hash is not None
    db.close()


def test_create_user_with_password_updates_existing_user():
    db = SessionLocal()
    usuario = obter_ou_criar_usuario(db, "vendedor_existente_teste", "Nome Original")
    assert usuario.senha_hash is None  # criado sem senha (ex: via lead_historico)

    updated = criar_usuario_com_senha(db, "vendedor_existente_teste", "nova-senha")
    assert updated.id == usuario.id  # mesmo registro, não duplicou
    assert updated.senha_hash is not None
    db.close()


def test_verify_user_credentials_success_and_failure():
    db = SessionLocal()
    criar_usuario_com_senha(db, "vendedor_cred_teste", "senha-certa")

    assert verificar_credenciais_usuario(db, "vendedor_cred_teste", "senha-certa") is True
    assert verificar_credenciais_usuario(db, "vendedor_cred_teste", "senha-errada") is False
    assert verificar_credenciais_usuario(db, "usuario_que_nao_existe", "qualquer") is False
    db.close()


def test_verify_user_credentials_user_without_password_always_fails():
    db = SessionLocal()
    obter_ou_criar_usuario(db, "usuario_sem_senha_teste")  # ex: "IA", criado sem senha_hash
    assert verificar_credenciais_usuario(db, "usuario_sem_senha_teste", "") is False
    assert verificar_credenciais_usuario(db, "usuario_sem_senha_teste", "qualquer-coisa") is False
    db.close()


def test_check_credentials_accepts_env_login():
    from admin.auth import verificar_credenciais

    assert verificar_credenciais(os.environ["ADMIN_USERNAME"], os.environ["ADMIN_PASSWORD"]) is True
    assert verificar_credenciais(os.environ["ADMIN_USERNAME"], "senha-errada") is False


def test_check_credentials_accepts_individual_db_login():
    from admin.auth import verificar_credenciais

    db = SessionLocal()
    criar_usuario_com_senha(db, "vendedor_login_individual_teste", "senha-do-vendedor")
    db.close()

    assert verificar_credenciais("vendedor_login_individual_teste", "senha-do-vendedor") is True
    assert verificar_credenciais("vendedor_login_individual_teste", "senha-errada") is False
