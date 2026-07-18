"""Database Manager (Cap 15).

Ponto unico de acesso ao SQLite - nenhum outro modulo abre o arquivo
diretamente (Cap 15 s.2; Cap 11 s.2). Cuida de: modo WAL, migracoes
versionadas, integrity_check, recuperacao de falhas (Cap 15 s.7), backup e
retencao (Cap 15 s.5-6).

Deliberadamente sincrono (sqlite3 e uma biblioteca bloqueante) - quem chama
em contexto assincrono (MemoryAPI) delega para `asyncio.to_thread`, o mesmo
padrao ja usado para pyserial em `orion.communication.transport`.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from motion_core.memory.schema import MIGRACOES

logger = logging.getLogger("motion_core.memory.database")

NOME_ARQUIVO_BACKUP_GLOB = "orion_*.db"
BACKUPS_DIARIOS_MANTIDOS = 7
BACKUPS_SEMANAIS_MANTIDOS = 4


def agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ErroBancoDeDados(Exception):
    """Falha irrecuperavel no banco (sem backup valido para restaurar)."""


class DatabaseManager:
    def __init__(self, caminho_db: Path | str, diretorio_backup: Path | str) -> None:
        self._caminho_db = Path(caminho_db)
        self._diretorio_backup = Path(diretorio_backup)
        self._conexao: sqlite3.Connection | None = None
        self.foi_reconstruido = False

    @property
    def conexao(self) -> sqlite3.Connection:
        if self._conexao is None:
            raise ErroBancoDeDados("Banco nao inicializado - chame iniciar() primeiro")
        return self._conexao

    @property
    def caminho_db(self) -> Path:
        return self._caminho_db

    def iniciar(self) -> None:
        """Sequencia do Cap 15 s.7: verifica integridade, recupera se
        necessario (WAL -> backup -> reconstruir do zero), aplica migracoes."""
        self._caminho_db.parent.mkdir(parents=True, exist_ok=True)
        self._diretorio_backup.mkdir(parents=True, exist_ok=True)

        try:
            self._conexao = self._abrir()
            integro = self._integridade_ok()
        except sqlite3.DatabaseError:
            # Arquivo presente mas nao e um SQLite valido - mesmo tratamento
            # de um integrity_check reprovado (Cap 15 s.7).
            integro = False

        if not integro:
            logger.warning("database.integrity_error: tentando recuperar via checkpoint do WAL")
            if self._conexao is not None:
                self._conexao.close()
            if self._recuperar_via_wal():
                logger.info("Recuperado via checkpoint do WAL")
            elif self.restaurar_backup_mais_recente():
                logger.info("database.rebuilt: restaurado a partir do backup mais recente")
            else:
                logger.error("database.rebuilt: sem backup valido - recriando banco do zero")
                self._recriar_do_zero()
                self.foi_reconstruido = True
            self._conexao = self._abrir()

        self._aplicar_migracoes()

    def _abrir(self) -> sqlite3.Connection:
        # check_same_thread=False: quem chama em contexto assincrono
        # (motion_core.memory.manutencao) delega cada operacao a
        # `asyncio.to_thread`, que pode escalar em threads diferentes do
        # executor padrao. Seguro aqui porque o acesso e sempre sequencial -
        # cada chamada e aguardada (await) antes da proxima comecar, nunca
        # duas threads mexendo na mesma conexao ao mesmo tempo.
        conexao = sqlite3.connect(
            str(self._caminho_db), isolation_level=None, check_same_thread=False
        )
        conexao.execute("PRAGMA journal_mode=WAL")
        conexao.execute("PRAGMA foreign_keys=ON")
        conexao.row_factory = sqlite3.Row
        return conexao

    def _integridade_ok(self) -> bool:
        try:
            resultado = self._conexao.execute("PRAGMA integrity_check").fetchone()
            return resultado[0] == "ok"
        except sqlite3.DatabaseError:
            return False

    def _recuperar_via_wal(self) -> bool:
        try:
            conexao = self._abrir()
            conexao.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            ok = conexao.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            conexao.close()
            return ok
        except sqlite3.DatabaseError:
            return False

    def _recriar_do_zero(self) -> None:
        for sufixo in ("", "-wal", "-shm"):
            Path(str(self._caminho_db) + sufixo).unlink(missing_ok=True)

    def _versao_atual(self) -> int:
        try:
            linha = self._conexao.execute("SELECT MAX(versao) FROM schema_version").fetchone()
            return linha[0] or 0
        except sqlite3.OperationalError:
            return 0  # schema_version ainda nao existe (banco novo/reconstruido)

    def _aplicar_migracoes(self) -> None:
        versao_atual = self._versao_atual()
        for versao, sql in MIGRACOES:
            if versao <= versao_atual:
                continue
            self._conexao.executescript(sql)
            self._conexao.execute(
                "INSERT INTO schema_version (versao, aplicado_em) VALUES (?, ?)",
                (versao, agora_iso()),
            )
            logger.info("Migracao %d aplicada", versao)

    def fechar(self) -> None:
        if self._conexao is not None:
            self._conexao.close()
            self._conexao = None

    # --- Backup (Cap 15 s.6) ---

    def fazer_backup(self) -> Path:
        """Copia consistente via API de backup do SQLite (nao para o
        sistema); rotaciona mantendo os backups mais recentes."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destino = self._diretorio_backup / f"orion_{timestamp}.db"
        alvo = sqlite3.connect(str(destino))
        try:
            self.conexao.backup(alvo)
        finally:
            alvo.close()
        self._rotacionar_backups()
        return destino

    def _listar_backups(self) -> list[Path]:
        return sorted(self._diretorio_backup.glob(NOME_ARQUIVO_BACKUP_GLOB))

    def _rotacionar_backups(self) -> None:
        """Mantem os BACKUPS_DIARIOS_MANTIDOS mais recentes, mais uma
        amostra semanal dos mais antigos (Cap 15 s.6: "7 diarios + 4
        semanais")."""
        arquivos = self._listar_backups()
        diarios = set(arquivos[-BACKUPS_DIARIOS_MANTIDOS:])
        mais_antigos = arquivos[: -BACKUPS_DIARIOS_MANTIDOS] if len(arquivos) > BACKUPS_DIARIOS_MANTIDOS else []
        semanais = set(mais_antigos[::7][-BACKUPS_SEMANAIS_MANTIDOS:])
        manter = diarios | semanais
        for arquivo in arquivos:
            if arquivo not in manter:
                arquivo.unlink()

    def restaurar_backup_mais_recente(self) -> bool:
        arquivos = self._listar_backups()
        if not arquivos:
            return False
        shutil.copy(arquivos[-1], self._caminho_db)
        for sufixo in ("-wal", "-shm"):
            Path(str(self._caminho_db) + sufixo).unlink(missing_ok=True)
        return True

    # --- Retencao (Cap 15 s.5) ---

    def limpar_retencao(
        self,
        telemetria_dias: int = 30,
        eventos_dias: int = 90,
        logs_dias: int = 30,
        logs_erro_dias: int = 180,
    ) -> dict[str, int]:
        """Remove registros expirados por politica de retencao e roda VACUUM."""
        agora = datetime.now(timezone.utc)

        def limite(dias: int) -> str:
            return (agora - timedelta(days=dias)).isoformat()

        removidos = {
            "telemetria": self.conexao.execute(
                "DELETE FROM telemetria WHERE timestamp < ?", (limite(telemetria_dias),)
            ).rowcount,
            "eventos": self.conexao.execute(
                "DELETE FROM eventos WHERE timestamp < ?", (limite(eventos_dias),)
            ).rowcount,
            "logs": self.conexao.execute(
                "DELETE FROM logs WHERE nivel != 'ERROR' AND timestamp < ?", (limite(logs_dias),)
            ).rowcount,
            "logs_erro": self.conexao.execute(
                "DELETE FROM logs WHERE nivel = 'ERROR' AND timestamp < ?",
                (limite(logs_erro_dias),),
            ).rowcount,
        }
        self.conexao.execute("VACUUM")
        return removidos
