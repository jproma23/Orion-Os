"""Desligar o robo inteiro pelo botao da interface.

Pedido do dono em 2026-08-03: *"colocar um botao para desligar o robo na webui
- esse botao dispara um shutdown para o notebook e o pi"*. A ideia ja estava na
lista de 26/07 e nunca tinha sido feita.

## A ORDEM IMPORTA, e ela nao e obvia

**O Notebook primeiro, o Pi depois.** Quem manda o comando e o Pi, e ele alcanca
o Notebook por SSH. Se o Pi caisse primeiro, o Notebook ficaria ligado sozinho,
sem ninguem para desliga-lo - e o dono ainda teria que ir la fechar na mao,
justamente o que este botao existe para evitar.

Por isso o Pi so se desliga DEPOIS de ter mandado o comando ao Notebook, e nao
espera confirmacao: se o Notebook estiver fora do ar, desligar o Pi continua
sendo a coisa certa. Perder o desligamento gracioso de uma maquina e melhor que
deixar as duas ligadas.

## Por que ha um atraso antes de desligar

O handler HTTP precisa RESPONDER antes de a maquina cair. Sem o atraso, o
`poweroff` derruba a rede no meio da resposta e o navegador mostra erro - o
dono clicaria de novo achando que nao funcionou, num robo que ja esta
desligando.

## A regra de sudoers JA EXISTE no Notebook (verificado 2026-08-04)

    (ALL) NOPASSWD: /usr/bin/systemctl poweroff

Ou seja: este modulo funciona hoje, sem preparo nenhum. O registro anterior
dizia que o Notebook pedia senha - **estava errado**, e vale saber por que o
teste mentiu: eu havia conferido com `sudo -n systemctl poweroff --dry-run`, e
o sudoers casa o comando **EXATO**. Com `--dry-run` no fim vira outro comando,
cai fora da regra e pede senha. Sem ele, passa.

Licao: para conferir permissao de sudoers, rode `sudo -n -l` e leia a regra -
nao invente uma variacao do comando para nao fazer estrago, porque a
variacao e justamente o que muda a resposta.

**NAO usar `systemctl poweroff` sem sudo** por SSH: o polkit recusa com
Interactive authentication required, porque a sessao SSH nao e local. O
caminho e o sudo com a regra acima.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("motion_core.desligamento")

#: Segundos entre responder ao navegador e cortar a energia de fato.
SEGUNDOS_ATE_DESLIGAR = 3.0

#: O comando roda com `-n`: nunca pergunta senha. Se faltar a regra de sudoers
#: ele falha na hora, em vez de ficar pendurado esperando um teclado que nao
#: existe - o modo de falha que mais custa em servico de fundo.
COMANDO_DESLIGAR = ("sudo", "-n", "systemctl", "poweroff")


async def _rodar(*argumentos: str) -> tuple[int, str]:
    processo = await asyncio.create_subprocess_exec(
        *argumentos,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    saida, _ = await processo.communicate()
    return processo.returncode or 0, saida.decode("utf-8", errors="replace").strip()


class Desligador:
    """Desliga Notebook e Raspberry, nesta ordem.

    `rodar` e injetavel para os testes nao desligarem a maquina de verdade -
    o que seria um teste caro de rodar duas vezes.
    """

    def __init__(
        self,
        host_notebook: str,
        usuario_notebook: str = "jproma23",
        rodar=_rodar,
        segundos_ate_desligar: float = SEGUNDOS_ATE_DESLIGAR,
    ) -> None:
        self._host = host_notebook
        self._usuario = usuario_notebook
        self._rodar = rodar
        self._espera = segundos_ate_desligar

    async def desligar_notebook(self) -> tuple[bool, str]:
        """Manda o Notebook desligar por SSH. Nao levanta: reporta."""
        try:
            codigo, saida = await self._rodar(
                "ssh",
                "-o", "BatchMode=yes",          # nunca pede senha nem trava
                "-o", "ConnectTimeout=5",
                f"{self._usuario}@{self._host}",
                *COMANDO_DESLIGAR,
            )
        except Exception as erro:
            logger.warning("nao consegui falar com o Notebook: %s", erro)
            return False, str(erro)
        if codigo != 0:
            logger.warning("Notebook recusou o desligamento (%d): %s", codigo, saida)
            return False, saida or f"codigo {codigo}"
        return True, ""

    async def desligar_raspberry(self) -> tuple[bool, str]:
        try:
            codigo, saida = await self._rodar(*COMANDO_DESLIGAR)
        except Exception as erro:
            return False, str(erro)
        return codigo == 0, "" if codigo == 0 else (saida or f"codigo {codigo}")

    async def desligar_tudo(self) -> dict:
        """Notebook e depois Pi. Devolve o que aconteceu com cada um."""
        ok_notebook, erro_notebook = await self.desligar_notebook()
        # O Pi cai MESMO se o Notebook falhar: deixar as duas ligadas por causa
        # de uma seria pior que perder o desligamento gracioso de uma.
        ok_pi, erro_pi = await self.desligar_raspberry()
        resultado = {
            "notebook": {"ok": ok_notebook, "erro": erro_notebook},
            "raspberry": {"ok": ok_pi, "erro": erro_pi},
        }
        logger.info("desligamento solicitado: %s", resultado)
        return resultado

    def agendar(self) -> asyncio.Task:
        """Responde primeiro, desliga depois - ver o cabecalho."""

        async def _depois():
            await asyncio.sleep(self._espera)
            await self.desligar_tudo()

        return asyncio.create_task(_depois())
