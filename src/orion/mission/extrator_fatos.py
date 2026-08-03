"""Destila FATOS duraveis a partir da conversa - a memoria que sobrevive.

## O buraco que isto fecha

Depois de `30ce49e` o robo passou a LER a memoria de longo prazo que ja
gravava: as conversas voltam do banco e entram no prompt. Mas conversa nao e
conhecimento. "meu dia foi bom, queria ouvir uma musica" e um turno; *"gosta
de musica a noite"* e um fato - e a tabela `conhecimento`, que existe no
schema desde sempre e que o `ai_manager` ja sabe injetar no prompt, continuava
**vazia porque ninguem escrevia nela**.

Este modulo e o produtor que faltava.

## Roda FORA do caminho critico, e isso nao e detalhe

Uma chamada de modelo a mais por turno viraria espera audivel. Este projeto ja
mediu o custo disso e lutou para baixar de 3,50 s para 0,66 s ate a primeira
frase; gastar esse ganho para guardar um fato seria um mau negocio evidente.

Duas decisoes protegem isso:

1. **Tarefa de fundo.** `agendar()` volta na hora; a extracao acontece depois,
   sem ninguem esperando por ela. Se falhar, o robo nem fica sabendo - perder
   um fato e barato, atrasar a fala nao e.
2. **Em lote.** So extrai a cada `turnos_por_lote` turnos. Alem de dividir o
   custo por varios turnos, a janela maior e o que permite ver o fato: "sim,
   esse mesmo" nao diz nada sozinho, e diz tudo depois da pergunta anterior.

## O que e um fato, e o que nao e

**So o que a PESSOA afirmou sobre ela mesma**, e que continua valendo amanha.

Nao entra: o que o ROBO disse (ele nao e fonte sobre a pessoa - e isso ficou
concreto em 2026-08-02, quando ele ouvia a propria voz e a gravava como fala
do dono); nada que dependa do momento ("estou com sono"); e nada inferido.
Preferir perder um fato a inventar um e a mesma regra que governa o resto do
projeto - a familia confia no que ele diz.
"""
from __future__ import annotations

import asyncio
import logging
import re

logger = logging.getLogger("orion.mission.extrator_fatos")

#: A resposta esperada NA MAIORIA DAS VEZES. Conversa comum nao produz fato
#: duravel, e um extrator que sempre acha alguma coisa esta inventando.
NADA = "NADA"

INSTRUCAO = """Voce le um trecho de conversa e extrai FATOS DURAVEIS sobre a
pessoa - coisas que continuarao verdadeiras amanha.

REGRAS:
- So o que A PESSOA afirmou sobre ela mesma. O que o robo disse NAO conta.
- Nada temporario ("estou com sono", "hoje choveu").
- Nada deduzido. Se nao foi dito com todas as letras, nao existe.
- Na duvida, responda NADA. E o normal: a maioria das conversas nao produz
  fato nenhum, e inventar um e pior que perder dez.

FORMATO: uma linha por fato, `chave: valor`, no maximo 3 linhas.
A chave e uma palavra curta e sem espacos (cafe, alergia, trabalho, musica).
Se nao houver nenhum fato, responda exatamente: NADA

CONVERSA:
{conversa}
"""


class ExtratorFatos:
    """Acumula turnos e, de tempos em tempos, destila fatos em segundo plano."""

    def __init__(
        self,
        ai_manager,
        memory_client,
        turnos_por_lote: int = 4,
        max_fatos_por_lote: int = 3,
    ) -> None:
        self._ia = ai_manager
        self._memoria = memory_client
        self._turnos_por_lote = turnos_por_lote
        self._max_fatos = max_fatos_por_lote
        self._acumulado: list[tuple[str, str]] = []
        #: Guardada para o desligamento poder esperar a ultima extracao, e
        #: para os testes nao precisarem dormir torcendo pelo agendador.
        self._tarefa: asyncio.Task | None = None

    def agendar(self, texto_usuario: str, resposta: str) -> None:
        """Registra um turno. Volta NA HORA - nunca espera pela extracao."""
        if not texto_usuario or not texto_usuario.strip():
            return
        self._acumulado.append((texto_usuario.strip(), (resposta or "").strip()))
        if len(self._acumulado) < self._turnos_por_lote:
            return

        lote = self._acumulado
        self._acumulado = []
        # Fogo e esquece. A excecao morreria em silencio dentro da task, entao
        # o callback abaixo existe so para ela chegar ao log.
        self._tarefa = asyncio.create_task(self._extrair(lote))
        self._tarefa.add_done_callback(self._reportar_falha)

    @staticmethod
    def _reportar_falha(tarefa: asyncio.Task) -> None:
        if tarefa.cancelled():
            return
        erro = tarefa.exception()
        if erro is not None:
            logger.warning("extracao de fatos falhou", exc_info=erro)

    async def encerrar(self) -> None:
        """Extrai o que sobrou e espera terminar. Para o desligamento limpo."""
        if self._acumulado:
            lote, self._acumulado = self._acumulado, []
            try:
                await self._extrair(lote)
            except Exception:
                logger.warning("extracao final falhou", exc_info=True)
        if self._tarefa is not None and not self._tarefa.done():
            await asyncio.gather(self._tarefa, return_exceptions=True)

    async def _extrair(self, lote: list[tuple[str, str]]) -> None:
        conversa = "\n".join(
            f"Pessoa: {pergunta}\nRobo: {resposta}" for pergunta, resposta in lote
        )
        # `usar_historico=False`: isto NAO e conversa. Se entrasse no historico
        # de curto prazo, a proxima fala do dono seria respondida no meio de
        # uma instrucao de extracao - e a extracao seguinte viria contaminada
        # pelo papo. Mesma separacao que `decidir()` ja faz.
        bruto = await self._ia.responder(
            INSTRUCAO.format(conversa=conversa), contexto=None, usar_historico=False
        )
        fatos = self._interpretar(bruto)
        if not fatos:
            logger.debug("nenhum fato durável no lote (%d turnos)", len(lote))
            return

        conhecidos = await self._chaves_conhecidas()
        for chave, valor in fatos:
            if chave in conhecidos:
                # Sobrescrever exigiria decidir qual versao vale, e nada aqui
                # sabe disso. Guardar os dois encheria o prompt de repeticao.
                # Ignorar e a escolha conservadora ate existir um caso real que
                # peca outra coisa.
                continue
            await self._memoria.remember(
                "conhecimento",
                {"chave": chave, "valor": valor, "fonte": "conversa", "confianca": 0.7},
            )
            logger.info("fato aprendido: %s = %s", chave, valor)

    async def _chaves_conhecidas(self) -> set[str]:
        try:
            registros = await self._memoria.recall("conhecimento", limite=200)
        except Exception:
            logger.warning("nao consegui ler os fatos ja conhecidos", exc_info=True)
            return set()
        return {str(r.get("chave", "")).strip().lower() for r in registros or []}

    def _interpretar(self, bruto: str | None) -> list[tuple[str, str]]:
        """Texto do modelo -> lista de (chave, valor), recusando o duvidoso."""
        if not bruto:
            return []
        texto = bruto.strip()
        # `NADA` em qualquer caixa, sozinho ou com pontuacao, encerra. Testar o
        # comeco tambem porque modelos pequenos gostam de acrescentar frase
        # ("NADA. Nao houve fatos.").
        if texto.upper().lstrip().startswith(NADA):
            return []

        fatos: list[tuple[str, str]] = []
        for linha in texto.splitlines():
            linha = linha.strip().lstrip("-•* ").strip()
            if not linha or ":" not in linha:
                continue
            chave, _, valor = linha.partition(":")
            chave = chave.strip().lower()
            valor = valor.strip()
            # Chave tem que ser UMA palavra curta. Modelo desobediente devolve
            # frase inteira antes dos dois pontos ("a pessoa gosta de: cafe") -
            # aquilo nao e chave e viraria lixo impossivel de casar na busca.
            if not re.fullmatch(r"[\wÀ-ÿ]{3,20}", chave):
                continue
            if not valor or len(valor) > 120:
                continue
            fatos.append((chave, valor))
            if len(fatos) >= self._max_fatos:
                break
        return fatos
