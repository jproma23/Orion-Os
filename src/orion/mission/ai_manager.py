"""AI Manager (Cap 7 secao 2-3) - a IA da conversa, remota (OpenAI/OpenRouter).

Prompt de sistema fixo (config/prompt_sistema.txt, Cap 17) + contexto vindo
da memoria (Cap 11, via MemoryClient/comm.request).

O OLLAMA LOCAL SAIU EM 2026-08-05, por decisao do dono. Ele era a reserva do
EDR-0021 - a ideia era nunca deixar a conversa muda. **Medido no dia em que
foi preciso**: os creditos do OpenRouter acabaram as 10:05, a queda para o
gemma3:1b disparou como projetado, e o robo simplesmente NAO respondeu -
60 s de espera e `{"erro": "o Fofao nao respondeu a tempo"}` na ponte do
chat. Era uma reserva que nao reservava, e ainda segurava ~1 GB de RAM no
Notebook (medido ao desligar) numa maquina que ja carrega YOLO,
reconhecimento facial e Whisper.

Sem IA remota o robo agora DIZ que esta sem pensar, em vez de pendurar quem
esta falando com ele. Tudo que nao depende de modelo continua de pe: parar,
a hora, o diario, a saudacao pelo nome e a entrega de recado.

Chave da API (OPENAI_API_KEY) nunca fixa no codigo/config (regra 6 do
CLAUDE.md) - vem de variavel de ambiente (EDR-0021).

O contexto entregue ao modelo passa pelo grounding
(orion/mission/grounding.py), que diz explicitamente o que o robo NAO
sabe - sem isso os modelos inventam observacoes (medido em 2026-07-19).
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from orion.mission.grounding import montar_contexto
from orion.mission.historico_conversa import HistoricoConversa

logger = logging.getLogger("orion.mission.ai_manager")

#: O que ele diz quando a IA remota nao responde - sem internet, sem credito,
#: erro de API.
#:
#: Texto pronto e curto de proposito: e justamente a hora em que nao ha modelo
#: para escrever uma frase melhor. Diz o que houve e delimita o estrago - o
#: corpo, a saudacao pelo nome, a hora e os recados continuam funcionando, e
#: quem esta ouvindo precisa saber que nao ficou sem robo, ficou sem conversa.
#:
#: NAO entra no historico. E um aviso do sistema, nao um turno: guardado, ele
#: viraria "assunto anterior" e a proxima resposta sairia comentando a propria
#: falha.
SEM_IA = "Agora eu nao consigo pensar - fiquei sem acesso a minha IA."


class AiManager:
    def __init__(
        self,
        temperatura: float = 0.6,
        caminho_prompt_sistema: str | Path = "config/prompt_sistema.txt",
        max_tokens_resposta: int | None = None,
        openai_model: str = "gemini-flash-lite-latest",
        openai_api_key: str | None = None,
        openai_base_url: str | None = None,
        max_turnos_memoria: int = 6,
        validade_memoria_s: float = 600.0,
    ) -> None:
        self._temperatura = temperatura
        self._prompt_sistema = Path(caminho_prompt_sistema).read_text(encoding="utf-8")
        # Numa conversa falada, resposta longa = espera longa duas vezes
        # (gerar + sintetizar/falar). Limitar os tokens mantem o dialogo agil.
        self._max_tokens_resposta = max_tokens_resposta
        self._openai_model = openai_model
        self._openai_api_key = openai_api_key or os.environ.get("OPENAI_API_KEY")
        # base_url alternativo (ex.: OpenRouter, "https://openrouter.ai/api/v1")
        # - a API e compativel com a da OpenAI, so muda o servidor (EDR-0021).
        self._openai_base_url = openai_base_url
        # Cliente nasce sob demanda, com o import DENTRO do metodo: assim o
        # mission_planner - que importa esta classe - continua importavel no
        # Raspberry e nos testes, sem a lib instalada.
        self._cliente_openai = None
        #: A memoria de curto prazo da CONVERSA (2026-08-02).
        #:
        #: Publico de proposito: quem sabe que o assunto acabou - a pessoa na
        #: frente do robo mudou, o operador assumiu - chama `.limpar()`. Essa
        #: informacao nao chega aqui dentro.
        self.historico = HistoricoConversa(
            max_turnos=max_turnos_memoria, validade_s=validade_memoria_s
        )

    def _montar_mensagens(
        self, prompt_sistema: str, texto_usuario: str, usar_historico: bool
    ) -> list[dict[str, str]]:
        """Monta o `messages` completo: sistema + turnos anteriores + fala atual.

        Existe para os TRES caminhos que falam com o modelo montarem a
        conversa no mesmo lugar. Antes cada um tinha a sua lista literal de
        duas mensagens, e foi exatamente por isso que a falta de memoria
        precisou ser corrigida em tres lugares - ou seria esquecida em um.
        """
        mensagens: list[dict[str, str]] = [{"role": "system", "content": prompt_sistema}]
        if usar_historico:
            mensagens.extend(self.historico.mensagens())
        mensagens.append({"role": "user", "content": texto_usuario})
        return mensagens

    async def responder(
        self,
        texto_usuario: str,
        contexto: dict | None = None,
        usar_historico: bool = True,
        prompt_sistema: str | None = None,
    ) -> str:
        """Responde uma fala, lembrando dos turnos anteriores.

        `usar_historico=False` existe para o `decidir()`: ele usa este mesmo
        modelo para ESCOLHER ACOES do robo, e aquilo nao e conversa. Se
        entrasse no historico, o proximo papo com o dono seria respondido no
        meio de um monte de deliberacao sobre avancar e virar - e, pior, a
        deliberacao seguinte viria contaminada pelo papo.
        """
        # `prompt_sistema` explicito TROCA a persona do Fofao (2026-08-02).
        #
        # Nem todo uso deste modelo e conversa. O extrator de fatos manda uma
        # INSTRUCAO, e ate aqui ela viajava como mensagem de usuario POR BAIXO
        # da persona - "voce e o Fofao, curioso, atencioso...". O resultado
        # apareceu num teste: com a persona por tras, ele passou a aceitar
        # "hoje estou muito cansado" como fato duravel, apesar de a propria
        # instrucao listar aquilo como exemplo do que NAO guardar. A persona
        # ganhava da instrucao.
        #
        # Quem faz um trabalho que nao e conversar passa o seu proprio prompt.
        prompt_sistema = prompt_sistema or self._montar_prompt_sistema(contexto)
        mensagens = self._montar_mensagens(prompt_sistema, texto_usuario, usar_historico)

        if not self._openai_api_key:
            logger.warning("sem chave de IA configurada - a conversa fica sem resposta")
            return SEM_IA
        try:
            resposta = await asyncio.to_thread(self._chamar_openai, mensagens)
        except Exception:
            logger.exception("IA remota indisponivel - respondendo que estou sem pensar")
            return SEM_IA

        if usar_historico:
            self.historico.registrar(texto_usuario, resposta)
        return resposta

    #: Onde uma frase termina. O Piper tira a prosodia inteira da pontuacao,
    #: entao cortar aqui e cortar onde ele mesmo faria a pausa - nunca no meio.
    FIM_DE_FRASE = (".", "!", "?", "\n")

    #: Nao vale a pena sintetizar "Sim." sozinho: o custo fixo de carregar o
    #: trecho no Piper come o ganho, e a fala sai picotada. Abaixo disto,
    #: acumula com a frase seguinte.
    MINIMO_CARACTERES_FRASE = 12

    async def responder_em_frases(self, texto_usuario: str, contexto: dict | None = None):
        """Gera a resposta e a entrega FRASE A FRASE, conforme sai do modelo.

        POR QUE EXISTE (2026-07-30, T5 da missao de naturalidade). Medido neste
        Notebook, com o mesmo prompt e o mesmo modelo:

            hoje (espera o texto inteiro, sintetiza o texto inteiro)  3,50 s
            streaming ate a 1a frase, sintetiza so ela               1,41 s
            reducao                                                    60%

        O alvo da missao era 40%. O ganho nao vem de gerar mais rapido - vem de
        parar de esperar o que ainda nao e preciso: o robo pode falar a primeira
        frase enquanto o modelo ainda escreve a segunda, que e o que uma pessoa
        faz.

        Quem consome deve sintetizar e tocar cada frase na ordem em que chega.

        ATENCAO A QUEM FOR LIGAR NUMA FILA DE AUDIO: o precedente do proprio
        projeto e o cliente do ESP32, que precisou drenar o socket
        continuamente - sem isso o buffer entregava som de segundos atras. Fila
        de fala tem o mesmo risco: se a conversa for interrompida, DESCARTAR o
        que estiver na fila. Senao o robo responde ao passado por um terceiro
        caminho.

        Cai para o caminho nao-streaming (uma frase so, o texto inteiro) quando
        nao ha chave de IA configurada - la a resposta e o aviso de que ficou
        sem pensar, e nao ha o que picotar.
        """
        prompt_sistema = self._montar_prompt_sistema(contexto)
        mensagens = self._montar_mensagens(prompt_sistema, texto_usuario, usar_historico=True)

        if not self._openai_api_key:
            # `responder` ja registra o turno sozinho - nao registrar de novo
            # aqui, senao a mesma pergunta entraria duas vezes no historico.
            yield await self.responder(texto_usuario, contexto)
            return

        fila: asyncio.Queue = asyncio.Queue()
        _FIM = object()
        laco = asyncio.get_running_loop()

        def _entregar(item) -> None:
            """Ponte thread -> laco de eventos.

            `asyncio.Queue` NAO e thread-safe, e `put_nowait` chamado de outra
            thread nao acorda o laco: a primeira versao disto media 3,95 s para
            a primeira frase e entregava as TRES no mesmo instante - ou seja,
            zero streaming, com todo o codigo de streaming no lugar. Falha
            silenciosa: nada levantava excecao, so o ganho nao existia.

            `call_soon_threadsafe` e o unico jeito correto de empurrar de uma
            thread para o laco.
            """
            laco.call_soon_threadsafe(fila.put_nowait, item)

        def _produzir() -> None:
            """Roda em thread: a lib da OpenAI e sincrona."""
            try:
                for frase in self._openai_em_frases(mensagens):
                    _entregar(frase)
            except Exception as erro:  # noqa: BLE001
                _entregar(erro)
            finally:
                _entregar(_FIM)

        tarefa = asyncio.create_task(asyncio.to_thread(_produzir))
        houve_frase = False
        # As frases sao entregues uma a uma para a voz comecar a falar antes
        # do fim, mas o HISTORICO precisa do texto inteiro: guardar "Oi." como
        # a resposta faria o modelo, no turno seguinte, achar que so disse
        # isso. Por isso as frases sao acumuladas aqui enquanto saem.
        ditas: list[str] = []
        try:
            while True:
                item = await fila.get()
                if item is _FIM:
                    break
                if isinstance(item, Exception):
                    # Falhou no meio: se ja falamos alguma frase, o corte e
                    # audivel e nao ha o que fazer; se nao falamos nada, ainda
                    # da para cair na reserva local em vez de emudecer.
                    logger.warning("streaming da IA falhou", exc_info=item)
                    if not houve_frase:
                        # Nada foi dito ainda: da para avisar que ficou sem
                        # pensar. Nao entra no historico - ver SEM_IA.
                        yield SEM_IA
                    else:
                        # Falou pela metade: o que saiu pela caixa foi ouvido e
                        # E o turno, por mais truncado que esteja. Guardar o
                        # pedaco mantem o historico fiel ao que a pessoa ouviu;
                        # descartar faria o robo negar depois ter dito aquilo.
                        self.historico.registrar(texto_usuario, " ".join(ditas))
                    return
                houve_frase = True
                ditas.append(item)
                yield item
            self.historico.registrar(texto_usuario, " ".join(ditas))
        finally:
            await tarefa

    def _openai_em_frases(self, mensagens: list[dict[str, str]]):
        """Gerador SINCRONO: consome o stream da OpenAI e solta frases fechadas."""
        from openai import OpenAI

        if self._cliente_openai is None:
            self._cliente_openai = OpenAI(
                api_key=self._openai_api_key, base_url=self._openai_base_url
            )

        fluxo = self._cliente_openai.chat.completions.create(
            model=self._openai_model,
            messages=mensagens,
            temperature=self._temperatura,
            max_tokens=self._max_tokens_resposta,
            stream=True,
        )

        acumulado = ""
        for pedaco in fluxo:
            if not pedaco.choices:
                continue
            acumulado += pedaco.choices[0].delta.content or ""
            # CORTE POR INDICE, e nao por `endswith` (2026-07-30, medido).
            #
            # A primeira versao testava `acumulado.endswith(".")`. Parece
            # equivalente e nao e: o modelo entrega tokens como ". Se voce",
            # com texto DEPOIS da pontuacao. Nesses casos o acumulado nunca
            # termina em ponto, o corte nunca dispara, e a "primeira frase" so
            # sai quando o stream inteiro acaba. Medido: 3,91 s para a primeira
            # frase, contra 0,66-1,29 s do experimento que motivou a mudanca -
            # o codigo de streaming inteiro no lugar, e ganho nenhum.
            while True:
                corte = self._fim_da_primeira_frase(acumulado)
                if corte is None:
                    break
                frase, acumulado = acumulado[:corte].strip(), acumulado[corte:]
                if frase:
                    yield frase
        if acumulado.strip():
            yield acumulado.strip()

    def _fim_da_primeira_frase(self, texto: str) -> int | None:
        """Indice logo apos a pontuacao que fecha a primeira frase, ou None.

        Devolve None enquanto a frase for curta demais para valer uma sintese
        propria: "Sim." sozinho custaria uma carga do Piper para meio segundo
        de audio, e a fala sairia picotada. Nesse caso o trecho segue
        acumulando com a frase seguinte.
        """
        for i, caractere in enumerate(texto):
            if caractere in self.FIM_DE_FRASE:
                if len(texto[: i + 1].strip()) >= self.MINIMO_CARACTERES_FRASE:
                    return i + 1
        return None

    def _chamar_openai(self, mensagens: list[dict[str, str]]) -> str:
        from openai import OpenAI

        if self._cliente_openai is None:
            self._cliente_openai = OpenAI(
                api_key=self._openai_api_key, base_url=self._openai_base_url
            )

        resposta = self._cliente_openai.chat.completions.create(
            model=self._openai_model,
            messages=mensagens,
            temperature=self._temperatura,
            max_tokens=self._max_tokens_resposta,
        )
        return resposta.choices[0].message.content

    async def decidir(self, estado: dict, acoes_validas: list[str]) -> str | None:
        """Pede uma decisao de acao ao maestro a partir de um vocabulario
        FECHADO (EDR-0022) - a IA nunca aciona hardware direto, so escolhe
        entre opcoes que o robo ja sabe executar com seguranca. Retorna
        None se a resposta vier fora do vocabulario ou a chamada falhar
        (Cap 6 s.8 - quem chama trata None como "sem decisao agora")."""
        prompt = (
            "Voce e o Javis, um robo cuidador do lar e da familia. Decida o "
            "proximo comportamento pensando no bem-estar e na seguranca de "
            "quem mora na casa. Escolha APENAS UMA destas acoes e responda so "
            f"com a palavra exata, sem mais nada: {', '.join(acoes_validas)}.\n\n"
            f"Estado atual do robo: {estado}"
        )
        try:
            # `usar_historico=False` NAO e detalhe (2026-08-02). Isto aqui usa
            # o mesmo modelo da conversa para ESCOLHER ACAO do robo, e as duas
            # coisas nao podem se misturar nos dois sentidos: a deliberacao
            # ("escolha entre avancar, virar_esquerda...") poluiria o papo com
            # o dono, e o papo com o dono influenciaria a escolha de
            # movimento. Sao dois usos do mesmo modelo, nao uma conversa so.
            resposta = await self.responder(prompt, contexto=None, usar_historico=False)
        except Exception:
            logger.warning("Falha ao consultar IA para decisao estrategica", exc_info=True)
            return None

        resposta_normalizada = resposta.strip().lower()
        for acao in acoes_validas:
            if acao in resposta_normalizada:
                return acao
        logger.warning("IA sugeriu acao fora do vocabulario: %r", resposta)
        return None

    # `descarregar()` SAIU JUNTO COM O OLLAMA (2026-08-05).
    #
    # Ele tirava o gemma3:1b da RAM (keep_alive=0) e era o segundo maior
    # alivio de memoria do Notebook. Sem modelo local nao ha nada para
    # descarregar, e manter o metodo devolvendo silencio seria repetir de
    # forma nova o bug de 30/07: ali ele levantava AttributeError, o
    # `AlivioCarga` engolia a excecao, e o guardiao de RAM aparecia inteiro
    # no log sem liberar um byte.
    #
    # Por isso o metodo foi REMOVIDO e nao esvaziado: quem monta o alivio
    # (conversar_fofao) parou de passar `descarregar_modelo`, e o alivio
    # agora conta so com o que de fato libera memoria - pausar a visao, que
    # e o maior consumidor da maquina (YOLO + reconhecimento facial).

    def _montar_prompt_sistema(self, contexto: dict | None) -> str:
        """Prompt fixo + bloco de fatos do grounding.

        O bloco entra SEMPRE, mesmo sem contexto nenhum: um contexto vazio
        vira "não tenho registro de nada hoje", que e justamente a
        informacao que impede a IA de inventar. Omitir o bloco quando nao ha
        dados seria repetir o erro medido em 2026-07-19, quando o silencio
        sobre um fato levou os modelos a afirmarem coisas que nunca viram.
        """
        contexto = contexto or {}

        pessoa = contexto.get("pessoa") or {}
        familia = contexto.get("familia")
        # Quem esta falando conosco tambem e alguem que conhecemos.
        if pessoa.get("nome") and not familia:
            familia = [pessoa["nome"]]

        bloco = montar_contexto(
            retrato=contexto.get("retrato"),
            familia=familia,
            observacoes=contexto.get("observacoes"),
            conversas_recentes=contexto.get("conversas_recentes"),
            visao_agora=contexto.get("visao_agora"),
        )

        partes = [self._prompt_sistema]
        if pessoa.get("nome"):
            partes.append(f"Voce esta falando com {pessoa['nome']}.")
        partes.append(bloco)

        conhecimento = contexto.get("conhecimento_relevante") or []
        if conhecimento:
            fatos = "\n".join(f"- {c['chave']}: {c['valor']}" for c in conhecimento[:5])
            partes.append(f"FATOS QUE EU JA SEI:\n{fatos}")

        notas = contexto.get("notas_relevantes") or []
        if notas:
            resumo = "\n".join(f"- {n['titulo']}: {n['trecho']}" for n in notas[:5])
            partes.append(f"Notas relevantes da sua memoria de longo prazo:\n{resumo}")

        # O QUE ELE SABE FAZER vai por ULTIMO, e isso e escolha e nao acaso:
        # e a unica parte do prompt que pede um FORMATO de resposta, e formato
        # obedecido e o que estiver mais perto da hora de escrever. Quem monta
        # este bloco e o mission_planner, a partir do catalogo em codigo - aqui
        # ele so e colado, para nao existir uma segunda lista de acoes que
        # envelhece sozinha.
        acoes = contexto.get("acoes")
        if acoes:
            partes.append(acoes)

        # `partes` ja comeca com self._prompt_sistema (acima) - concatenar de
        # novo aqui duplicaria o prompt inteiro no pedido ao modelo.
        return "\n\n".join(partes)
