import os

from database import (
    SessionLocal,
    create_user_with_password,
    get_or_create_user,
    hash_password,
    verify_password,
    verify_user_credentials,
)


def test_hash_password_roundtrip():
    hashed = hash_password("senha-forte-123")
    assert verify_password("senha-forte-123", hashed) is True


def test_hash_password_wrong_password_fails():
    hashed = hash_password("senha-forte-123")
    assert verify_password("senha-errada", hashed) is False


def test_hash_password_uses_random_salt_each_time():
    # mesma senha, hashes diferentes — nunca dá pra comparar hash direto (evita rainbow table)
    assert hash_password("mesma-senha") != hash_password("mesma-senha")


def test_verify_password_rejects_malformed_hash():
    assert verify_password("qualquer", "hash-sem-separador") is False
    assert verify_password("qualquer", None) is False


def test_create_user_with_password_new_user():
    db = SessionLocal()
    user = create_user_with_password(db, "vendedor_novo_teste", "senha123", "Vendedor Teste")
    assert user.username == "vendedor_novo_teste"
    assert user.nome == "Vendedor Teste"
    assert user.password_hash is not None
    db.close()


def test_create_user_with_password_updates_existing_user():
    db = SessionLocal()
    user = get_or_create_user(db, "vendedor_existente_teste", "Nome Original")
    assert user.password_hash is None  # criado sem senha (ex: via lead_historico)

    updated = create_user_with_password(db, "vendedor_existente_teste", "nova-senha")
    assert updated.id == user.id  # mesmo registro, não duplicou
    assert updated.password_hash is not None
    db.close()


def test_verify_user_credentials_success_and_failure():
    db = SessionLocal()
    create_user_with_password(db, "vendedor_cred_teste", "senha-certa")

    assert verify_user_credentials(db, "vendedor_cred_teste", "senha-certa") is True
    assert verify_user_credentials(db, "vendedor_cred_teste", "senha-errada") is False
    assert verify_user_credentials(db, "usuario_que_nao_existe", "qualquer") is False
    db.close()


def test_verify_user_credentials_user_without_password_always_fails():
    db = SessionLocal()
    get_or_create_user(db, "usuario_sem_senha_teste")  # ex: "IA", criado sem password_hash
    assert verify_user_credentials(db, "usuario_sem_senha_teste", "") is False
    assert verify_user_credentials(db, "usuario_sem_senha_teste", "qualquer-coisa") is False
    db.close()


def test_check_credentials_accepts_env_login():
    from admin.auth import check_credentials

    assert check_credentials(os.environ["ADMIN_USERNAME"], os.environ["ADMIN_PASSWORD"]) is True
    assert check_credentials(os.environ["ADMIN_USERNAME"], "senha-errada") is False


def test_check_credentials_accepts_individual_db_login():
    from admin.auth import check_credentials

    db = SessionLocal()
    create_user_with_password(db, "vendedor_login_individual_teste", "senha-do-vendedor")
    db.close()

    assert check_credentials("vendedor_login_individual_teste", "senha-do-vendedor") is True
    assert check_credentials("vendedor_login_individual_teste", "senha-errada") is False
