"""Testes do Database Manager (Cap 15)."""
import time
from datetime import datetime, timedelta, timezone

import pytest

from motion_core.memory.database import DatabaseManager, agora_iso


def _criar_gerenciador(tmp_path) -> DatabaseManager:
    return DatabaseManager(tmp_path / "orion.db", tmp_path / "backups")


def test_iniciar_cria_todas_as_tabelas(tmp_path):
    gm = _criar_gerenciador(tmp_path)
    gm.iniciar()

    tabelas = {
        linha["name"]
        for linha in gm.conexao.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    esperadas = {
        "schema_version",
        "pessoas",
        "ambientes",
        "objetos",
        "conhecimento",
        "conversas",
        "configuracao_memoria",
        "missoes",
        "eventos",
        "telemetria",
        "logs",
        "diagnosticos",
        "configuracao",
    }
    assert esperadas <= tabelas
    gm.fechar()


def test_iniciar_registra_versao_do_schema(tmp_path):
    gm = _criar_gerenciador(tmp_path)
    gm.iniciar()

    versao = gm.conexao.execute("SELECT MAX(versao) FROM schema_version").fetchone()[0]
    assert versao == 1
    gm.fechar()


def test_iniciar_e_idempotente(tmp_path):
    """Reabrir um banco ja migrado nao deve falhar nem duplicar a migracao."""
    gm = _criar_gerenciador(tmp_path)
    gm.iniciar()
    gm.fechar()

    gm2 = _criar_gerenciador(tmp_path)
    gm2.iniciar()
    quantidade = gm2.conexao.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    assert quantidade == 1
    gm2.fechar()


def test_modo_wal_habilitado(tmp_path):
    gm = _criar_gerenciador(tmp_path)
    gm.iniciar()
    modo = gm.conexao.execute("PRAGMA journal_mode").fetchone()[0]
    assert modo.lower() == "wal"
    gm.fechar()


def test_foreign_keys_habilitado(tmp_path):
    gm = _criar_gerenciador(tmp_path)
    gm.iniciar()
    assert gm.conexao.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    gm.fechar()


def test_backup_e_restauracao(tmp_path):
    gm = _criar_gerenciador(tmp_path)
    gm.iniciar()
    gm.conexao.execute(
        "INSERT INTO pessoas (nome, criado_em, atualizado_em) VALUES (?, ?, ?)",
        ("Joao", agora_iso(), agora_iso()),
    )

    caminho_backup = gm.fazer_backup()
    assert caminho_backup.exists()

    gm.conexao.execute("DELETE FROM pessoas")
    assert gm.conexao.execute("SELECT COUNT(*) FROM pessoas").fetchone()[0] == 0

    gm.fechar()
    gm2 = _criar_gerenciador(tmp_path)
    assert gm2.restaurar_backup_mais_recente() is True
    gm2.iniciar()
    assert gm2.conexao.execute("SELECT COUNT(*) FROM pessoas").fetchone()[0] == 1
    gm2.fechar()


def test_restaurar_sem_backup_retorna_falso(tmp_path):
    gm = _criar_gerenciador(tmp_path)
    gm.iniciar()
    assert gm.restaurar_backup_mais_recente() is False
    gm.fechar()


def test_rotacao_de_backup_mantem_apenas_os_mais_recentes(tmp_path):
    gm = _criar_gerenciador(tmp_path)
    gm.iniciar()

    for _ in range(10):
        gm.fazer_backup()
        time.sleep(0.01)  # garante nomes de arquivo (timestamp) distintos

    arquivos = gm._listar_backups()
    assert len(arquivos) <= 7 + 4
    gm.fechar()


def test_banco_corrompido_e_reconstruido_sem_backup(tmp_path):
    gm = _criar_gerenciador(tmp_path)
    gm.iniciar()
    gm.fechar()

    # corrompe o arquivo de proposito
    with open(tmp_path / "orion.db", "wb") as f:
        f.write(b"isto definitivamente nao e um banco sqlite valido")

    gm2 = _criar_gerenciador(tmp_path)
    gm2.iniciar()

    assert gm2.foi_reconstruido is True
    versao = gm2.conexao.execute("SELECT MAX(versao) FROM schema_version").fetchone()[0]
    assert versao == 1
    gm2.fechar()


def test_banco_corrompido_recupera_via_backup_quando_existe(tmp_path):
    gm = _criar_gerenciador(tmp_path)
    gm.iniciar()
    gm.conexao.execute(
        "INSERT INTO pessoas (nome, criado_em, atualizado_em) VALUES (?, ?, ?)",
        ("Maria", agora_iso(), agora_iso()),
    )
    gm.fazer_backup()
    gm.fechar()

    with open(tmp_path / "orion.db", "wb") as f:
        f.write(b"corrompido")

    gm2 = _criar_gerenciador(tmp_path)
    gm2.iniciar()

    assert gm2.foi_reconstruido is False
    assert gm2.conexao.execute("SELECT COUNT(*) FROM pessoas").fetchone()[0] == 1
    gm2.fechar()


def test_limpar_retencao_remove_registros_expirados(tmp_path):
    gm = _criar_gerenciador(tmp_path)
    gm.iniciar()

    antigo = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    recente = agora_iso()

    gm.conexao.execute(
        "INSERT INTO eventos (origem, tipo, payload_json, timestamp) VALUES (?, ?, ?, ?)",
        ("teste", "antigo", "{}", antigo),
    )
    gm.conexao.execute(
        "INSERT INTO eventos (origem, tipo, payload_json, timestamp) VALUES (?, ?, ?, ?)",
        ("teste", "recente", "{}", recente),
    )
    gm.conexao.execute(
        "INSERT INTO logs (nivel, origem, mensagem, timestamp) VALUES (?, ?, ?, ?)",
        ("ERROR", "teste", "erro antigo mantido", antigo),
    )
    gm.conexao.execute(
        "INSERT INTO logs (nivel, origem, mensagem, timestamp) VALUES (?, ?, ?, ?)",
        ("INFO", "teste", "info antigo removido", antigo),
    )

    removidos = gm.limpar_retencao(eventos_dias=90, logs_dias=30, logs_erro_dias=180)

    assert removidos["eventos"] == 1
    assert removidos["logs"] == 1
    assert gm.conexao.execute("SELECT COUNT(*) FROM eventos").fetchone()[0] == 1
    # log de erro antigo (100 dias) deve sobreviver: limite de erro e 180 dias
    assert gm.conexao.execute("SELECT COUNT(*) FROM logs WHERE nivel='ERROR'").fetchone()[0] == 1
    gm.fechar()


def test_acessar_conexao_antes_de_iniciar_falha(tmp_path):
    from motion_core.memory.database import ErroBancoDeDados

    gm = _criar_gerenciador(tmp_path)
    with pytest.raises(ErroBancoDeDados):
        _ = gm.conexao
