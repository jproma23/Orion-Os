"""Logger estruturado do Kernel (Cap 6, secao 8; Cap 17).

Escreve JSON em arquivo (para analise/auditoria) e texto legivel no console
(para acompanhar o boot em tempo real). O nivel vem de `system.log_level`
em `config/orion.yaml` - nunca fixo no codigo.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_NIVEIS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


class _FormatadorJSON(logging.Formatter):
    """Formata cada registro de log como uma linha JSON."""

    def format(self, record: logging.LogRecord) -> str:
        entrada = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "nivel": record.levelname,
            "modulo": record.name,
            "mensagem": record.getMessage(),
        }
        if record.exc_info:
            entrada["excecao"] = self.formatException(record.exc_info)
        return json.dumps(entrada, ensure_ascii=False)


def configurar_logger(
    nome: str,
    nivel: str = "INFO",
    arquivo_log: Path | str | None = None,
) -> logging.Logger:
    """Cria/configura um logger nomeado com saida em console (legivel) e arquivo (JSON).

    `nome` normalmente e o nome do modulo (ex.: "orion.kernel.boot"), o que
    aparece nos logs e ajuda a rastrear a origem de cada mensagem.
    """
    if nivel not in _NIVEIS:
        raise ValueError(
            f"Nivel de log invalido: {nivel!r}. Use um de {sorted(_NIVEIS)}."
        )

    logger = logging.getLogger(nome)
    logger.setLevel(_NIVEIS[nivel])
    logger.propagate = False

    # Evita duplicar handlers se a funcao for chamada mais de uma vez para o
    # mesmo logger (ex.: em testes que reconfiguram o sistema varias vezes).
    logger.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    # A HORA ENTRA AQUI, e nao so no _FormatadorJSON abaixo (2026-07-30).
    #
    # O formatador JSON tem "timestamp" desde sempre, mas ele so e instalado
    # quando `arquivo_log` e passado - e o Motion Core NAO passa
    # (motion_core/__main__.py chama configurar_logger so com `nivel`). No Pi
    # quem grava o arquivo e o systemd, redirecionando este stdout
    # (`StandardOutput=append:` em tools/orion-motion.service). Resultado
    # medido: 9,8 MB de log de producao, 63 mil linhas, nenhuma dizendo que
    # horas sao.
    #
    # Sem hora nao da para medir duracao de nada (quanto leva um explore? o
    # scan? o ACK?), nem para casar uma linha com o que o dono viu na bancada,
    # nem para saber se um WARNING e de agora ou de seis horas atras. Ja custou
    # um diagnostico errado em 29/07, quando um `awk` de timestamp foi usado
    # num log que nao tinha timestamp.
    #
    # Sem milissegundos de proposito: o que se mede aqui (varredura ~2,1s,
    # cegueira do loop ~124ms, ciclo da IA 30s) cabe folgado no segundo, e a
    # linha ja e longa.
    console.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(console)

    if arquivo_log is not None:
        caminho = Path(arquivo_log)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        arquivo = logging.FileHandler(caminho, encoding="utf-8")
        arquivo.setFormatter(_FormatadorJSON())
        logger.addHandler(arquivo)

    return logger
