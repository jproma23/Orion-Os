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

## Por que o lote sozinho nao bastava (medido em 2026-08-03)

O lote por CONTAGEM presumia conversas longas. Os numeros do banco desmentiram:
das 37 sessoes gravadas, **32 tiveram menos de 4 turnos** - o gatilho nunca
disparava. E o resto do lote morria no desligamento, porque `encerrar()` nao
era chamado por ninguem. Resultado: 152 conversas gravadas e a tabela
`conhecimento` com ZERO linhas. O consumidor existia (o `ai_manager` injeta os
fatos no prompt), o produtor estava ligado, e mesmo assim nada passava - a
mesma familia de defeito silencioso que este projeto ja teve outras vezes.

O conserto e destilar tambem por OCIOSIDADE: passados `segundos_ate_destilar`
sem turno novo, a conversa acabou e o que houver e destilado como esta. Uma
conversa de dois turnos vale tanto quanto uma de dez - so acaba mais cedo.

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

#: O prompt de sistema DO EXTRATOR - e nao a persona do Fofao.
#:
#: Descoberto num teste em 2026-08-02: enquanto a instrucao viajava como
#: mensagem de usuario por baixo da persona conversacional, o modelo aceitou
#: "hoje estou muito cansado" como fato duravel - apesar de a instrucao listar
#: exatamente aquilo como exemplo do que NAO guardar. A persona ("curioso,
#: atencioso, use o que a pessoa te contou") empurrava na direcao oposta e
#: ganhava. Extrair nao e conversar, e o prompt tem que dizer isso.
PROMPT_SISTEMA = (
    "Voce e um extrator de fatos. Nao conversa, nao cumprimenta, nao opina, "
    "nao ajuda. Le um trecho de conversa e devolve apenas o formato pedido. "
    "Na duvida, devolve NADA - perder um fato e barato, inventar um nao e."
)

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
        segundos_ate_destilar: float = 90.0,
    ) -> None:
        """
        `segundos_ate_destilar` e o silencio que declara a conversa encerrada.
        Noventa segundos e bem mais que a pausa entre duas frases faladas e bem
        menos que o intervalo entre duas conversas do dia (a menor separacao
        real medida no banco foi de 14 minutos).
        """
        self._ia = ai_manager
        self._memoria = memory_client
        self._turnos_por_lote = turnos_por_lote
        self._max_fatos = max_fatos_por_lote
        self._segundos_ate_destilar = segundos_ate_destilar
        self._acumulado: list[tuple[str, str]] = []
        #: Guardada para o desligamento poder esperar a ultima extracao, e
        #: para os testes nao precisarem dormir torcendo pelo agendador.
        self._tarefa: asyncio.Task | None = None
        #: A espera pelo fim da conversa. Rearmada a cada turno novo.
        self._tarefa_ociosidade: asyncio.Task | None = None

    #: Uma fala precisa ter ao menos isto para caber um fato.
    #:
    #: Sem este piso, o gatilho por ociosidade gastaria uma chamada de modelo
    #: em toda sessao de uma palavra - e "Fofao!" sozinho e a sessao mais comum
    #: no banco (a palavra de ativacao entra como turno). Nao ha fato duravel
    #: dentro de um chamado; so ha custo.
    _MINIMO_LETRAS_FALA = 15

    def agendar(self, texto_usuario: str, resposta: str) -> None:
        """Registra um turno. Volta NA HORA - nunca espera pela extracao."""
        if not texto_usuario or not texto_usuario.strip():
            return
        self._acumulado.append((texto_usuario.strip(), (resposta or "").strip()))
        if len(self._acumulado) >= self._turnos_por_lote:
            self._cancelar_espera()
            self._disparar(self._tomar_lote())
            return
        # Ainda nao encheu o lote: rearma a espera. Se vier outro turno antes
        # do prazo, esta espera e cancelada e comeca de novo - o relogio conta
        # SILENCIO, nao tempo desde o inicio da conversa.
        self._rearmar_espera()

    def _tomar_lote(self) -> list[tuple[str, str]]:
        lote, self._acumulado = self._acumulado, []
        return lote

    def _disparar(self, lote: list[tuple[str, str]]) -> None:
        # Fogo e esquece. A excecao morreria em silencio dentro da task, entao
        # o callback abaixo existe so para ela chegar ao log.
        self._tarefa = asyncio.create_task(self._extrair(lote))
        self._tarefa.add_done_callback(self._reportar_falha)

    def _cancelar_espera(self) -> asyncio.Task | None:
        """Pede o cancelamento e devolve a tarefa, para quem puder esperar.

        `cancel()` so AGENDA o cancelamento - a tarefa so morre quando o laco
        a escalar de novo. No `agendar` isso basta (o laco continua rodando);
        no `encerrar` nao, e por isso a tarefa volta daqui em vez de ser
        simplesmente esquecida.
        """
        tarefa = self._tarefa_ociosidade
        if tarefa is not None and not tarefa.done():
            tarefa.cancel()
        self._tarefa_ociosidade = None
        return tarefa

    def _rearmar_espera(self) -> None:
        self._cancelar_espera()
        self._tarefa_ociosidade = asyncio.create_task(self._destilar_quando_ociosa())
        self._tarefa_ociosidade.add_done_callback(self._reportar_falha)

    async def _destilar_quando_ociosa(self) -> None:
        """Passado o silencio, destila o que houver - a conversa acabou."""
        try:
            await asyncio.sleep(self._segundos_ate_destilar)
        except asyncio.CancelledError:
            return
        lote = self._tomar_lote()
        if not self._vale_destilar(lote):
            # Descartar aqui e deliberado: o lote e ruido (chamado sem
            # pergunta), nao conteudo adiado. Deixa-lo acumulado faria a
            # proxima conversa comecar contaminada por ele.
            logger.debug("lote ocioso sem substancia (%d turnos) - descartado", len(lote))
            return
        await self._extrair(lote)

    def _vale_destilar(self, lote: list[tuple[str, str]]) -> bool:
        """Ha fala longa o bastante para caber um fato?"""
        return any(len(pergunta) >= self._MINIMO_LETRAS_FALA for pergunta, _ in lote)

    @staticmethod
    def _reportar_falha(tarefa: asyncio.Task) -> None:
        if tarefa.cancelled():
            return
        erro = tarefa.exception()
        if erro is not None:
            logger.warning("extracao de fatos falhou", exc_info=erro)

    async def encerrar(self) -> None:
        """Extrai o que sobrou e espera terminar. Para o desligamento limpo.

        No desligamento nao se aplica o piso de `_vale_destilar`: quem fechou a
        sessao ja pagou o tempo, e este e o ultimo momento em que aquele lote
        existe. O piso serve para nao gastar chamada a toa NO MEIO do uso.
        """
        espera = self._cancelar_espera()
        if espera is not None:
            await asyncio.gather(espera, return_exceptions=True)
        if self._acumulado:
            lote = self._tomar_lote()
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
            INSTRUCAO.format(conversa=conversa),
            contexto=None,
            usar_historico=False,
            prompt_sistema=PROMPT_SISTEMA,
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
