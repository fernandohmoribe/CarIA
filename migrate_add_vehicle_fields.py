"""
Migração pontual: adiciona as colunas novas do formulário de cadastro de veículo
(cidade, final_placa, blindado, aceita_troca, unico_dono, revisoes_concessionaria,
ipva_pago, licenciado, garantia_fabrica) na tabela `vehicles` já existente.

Idempotente — checa PRAGMA table_info antes de cada ALTER TABLE, então rodar de novo
por engano não quebra nada. Base.metadata.create_all() só cria tabela que falta, não
coluna nova em tabela que já existe — por isso esse script separado.

Uso:
    python migrate_add_vehicle_fields.py
"""

from dotenv import load_dotenv

load_dotenv()

from database import engine

NEW_COLUMNS = {
    "cidade": "VARCHAR",
    "final_placa": "VARCHAR",
    "blindado": "BOOLEAN DEFAULT 0",
    "aceita_troca": "BOOLEAN DEFAULT 0",
    "unico_dono": "BOOLEAN DEFAULT 0",
    "revisoes_concessionaria": "BOOLEAN DEFAULT 0",
    "ipva_pago": "BOOLEAN DEFAULT 0",
    "licenciado": "BOOLEAN DEFAULT 0",
    "garantia_fabrica": "BOOLEAN DEFAULT 0",
}


def run() -> None:
    with engine.begin() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(vehicles)").fetchall()}
        for column, coltype in NEW_COLUMNS.items():
            if column in existing:
                print(f"  {column} já existe, pulando")
                continue
            conn.exec_driver_sql(f"ALTER TABLE vehicles ADD COLUMN {column} {coltype}")
            print(f"  {column} adicionada")


if __name__ == "__main__":
    run()
    print("Migração concluída.")
