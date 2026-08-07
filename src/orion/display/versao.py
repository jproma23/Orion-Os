"""Identificacao da build para a marca d'agua da tela.

POR QUE ISTO NAO E UM DICIONARIO FIXO NO HTML: data de atualizacao escrita
a mao mente no dia seguinte. Aqui ela sai do `git`, que e a unica fonte que
nao envelhece sozinha - lida UMA VEZ na subida, nao a cada pedido.

Se o git nao responder (copia sem .git, binario ausente), cada campo cai
para "desconhecido" em vez de derrubar a tela: marca d'agua e informacao,
nao funcao vital (Cap 6 s.8).
"""

from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

#: Nome do produto. Trocado de "Fofao" para "ORION" em 2026-08-07 a pedido
#: do dono - o robo deixou de ser brinquedo de bancada e passou a ser
#: apresentado como produto de ensino. "Fofao" segue como o nome PROPRIO do
#: robo na fala (config `robot_name`); ORION e o sistema.
SOFTWARE = "ORION OS"

DESCONHECIDO = "desconhecido"


@dataclass(frozen=True)
class Build:
    software: str
    versao: str
    autor: str
    criado_em: str
    atualizado_em: str
    commit: str

    def como_dict(self) -> dict:
        return asdict(self)


def _git(raiz: Path, *args: str) -> str:
    try:
        saida = subprocess.run(
            ["git", *args],
            cwd=raiz,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
    return saida


def descobrir(raiz: Path, versao: str, autor: str) -> Build:
    """Monta a identificacao da build. Nunca levanta."""
    # `--reverse | head -1` nao serve aqui: o git escreve tudo e o head fecha
    # o cano, o que vira BrokenPipe dentro do subprocess. `--max-parents=0`
    # pede o commit raiz direto.
    criado = _git(raiz, "log", "--max-parents=0", "--pretty=%ad", "--date=short")
    atualizado = _git(raiz, "log", "-1", "--pretty=%ad", "--date=short")
    commit = _git(raiz, "log", "-1", "--pretty=%h")

    # repositorio com mais de uma raiz devolve varias linhas - a primeira e a
    # mais antiga porque `git log` ja vem em ordem decrescente de data
    if criado:
        criado = criado.splitlines()[-1].strip()

    return Build(
        software=SOFTWARE,
        versao=versao or DESCONHECIDO,
        autor=autor or DESCONHECIDO,
        criado_em=criado or DESCONHECIDO,
        atualizado_em=atualizado or DESCONHECIDO,
        commit=commit or DESCONHECIDO,
    )


#: Versao do PRODUTO, e ela e diferente de proposito das outras duas do
#: repo: `VERSAO_MOTION_CORE` e `VERSAO_KERNEL` sao versoes de MODULO, usadas
#: no aperto de mao entre as maquinas (discovery). Esta aqui e o que aparece
#: na tela para quem olha o robo. Confundir as tres faria uma mudanca de
#: protocolo mexer no rodape da TV, e vice-versa.
VERSAO_PRODUTO = "0.1.0"

#: Autoria, para a marca d'agua. Nome de pessoa nao vai para config: config
#: e o que muda por instalacao, e isto nao muda.
AUTOR = "João Paulo"
