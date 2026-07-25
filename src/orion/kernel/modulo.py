"""Contrato de modulo do Mission Core (EDR-0023).

Todo modulo de alto nivel do Notebook (Vision, Voice, Mission/IA, Display)
cumpre este protocolo, para o Boot Manager conseguir subir, vigiar e
desligar todos da mesma forma - em vez de encanamento diferente para cada
um, que foi como tools/conversar_fofao.py virou o sistema inteiro.

Regras do contrato (EDR-0023 secao 1):

- `iniciar()` PODE falhar. Falha significa "modulo ausente", nunca "sistema
  abortado" - quem decide isso e o Boot Manager (Cap 6 secao 8).
- `encerrar()` e idempotente e NUNCA levanta excecao: roda tanto no
  desligamento normal quanto depois de uma falha parcial de boot.
- `esta_saudavel()` e sincrono e sem I/O - o Watchdog o chama em laco.
- O modulo NAO conhece outros modulos. Toda troca continua passando pelo
  Event Bus (regra 1 do CLAUDE.md).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ModuloOrion(Protocol):
    """O que o Boot Manager espera de qualquer modulo do Mission Core."""

    #: identidade do modulo no Service Registry (ex.: "vision", "voice")
    nome: str

    async def iniciar(self) -> None:
        """Sobe o modulo: abre o hardware que lhe pertence e assina eventos.

        Pode levantar excecao - o Boot Manager trata como modulo ausente e
        segue o boot em modo degradado.
        """
        ...

    async def encerrar(self) -> None:
        """Solta hardware e cancela tarefas. Idempotente, nunca levanta."""
        ...

    def esta_saudavel(self) -> bool:
        """Resposta rapida para o Watchdog. Sem I/O, sem bloquear."""
        ...
