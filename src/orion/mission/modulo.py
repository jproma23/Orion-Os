"""Modulo de missao/IA do Mission Core (EDR-0023).

Reune o que estava espalhado dentro de tools/conversar_jarvis.py: o AiManager
(IA remota - a reserva local saiu em 2026-08-05), o MissionPlanner (Cap 7) e as
tres pontes que atendem pedidos vindos do Pi - PonteDecisao (EDR-0022, a IA
estrategica), PonteConselho (o Mentor) e PonteConversa (o chat de texto da
webui).

Depende do ComunicacaoService porque o planner precisa mandar comando ao
Hardware Core e consultar a memoria, e porque as pontes atendem pedidos
que chegam pelo enlace com o Pi. Continua sem conhecer outros modulos: tudo
que sai daqui sai pelo Event Bus (regra 1 do CLAUDE.md).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from orion.kernel.event_bus import EventBus

logger = logging.getLogger("orion.mission.modulo")

_RAIZ_PROJETO = Path(__file__).resolve().parents[3]


class ModuloMissao:
    nome = "mission"

    def __init__(
        self,
        event_bus: EventBus,
        comm: Any,
        conf_ia: dict[str, Any],
        ia: Any | None = None,
    ) -> None:
        # `ia` injetavel: os testes trocam por um dublê e nao precisam de
        # chave de API configurada.
        self._event_bus = event_bus
        self._comm = comm
        self._conf = conf_ia
        self._ia = ia
        self._planner: Any | None = None
        self._pronto = False
        self._corpo: Any | None = None

    async def iniciar(self) -> None:
        from orion.mission.decisao_estrategica import PonteDecisao
        from orion.mission.memory_client import MemoryClient
        from orion.mission.mission_planner import MissionPlanner
        from orion.mission.ponte_conversa import PonteConversa

        if self._ia is None:
            self._ia = self._construir_ia()

        from orion.mission.corpo_agora import URL_ESTADO_PADRAO, CorpoAgora

        # O corpo agora (2026-10-31): o mapa do LiDAR e a bussola entram no
        # prompt, e cada movimento e conferido pelo LiDAR antes/depois.
        self._corpo = CorpoAgora(self._conf.get("url_estado_corpo") or URL_ESTADO_PADRAO)

        self._planner = MissionPlanner(
            self._ia,
            enviar_comando_hardware=self._enviar_comando_hardware,
            memory_client=MemoryClient(self._comm),
            # O QUE ELE SABE DO PROPRIO CORPO. Estava ligado so no
            # tools/conversar_jarvis.py (o caminho do Notebook); desde que a
            # voz e o chat passaram por este modulo, no Pi, ninguem o passava
            # - e a pergunta "como voce anda?" caia na IA, que inventa sobre
            # hardware. Achado em 2026-10-31.
            autoconhecimento=self._construir_autoconhecimento(),
            estado_corpo=self._corpo.texto,
        )

        # A IA estrategica do maestro (EDR-0022) manda "mission.decidir" pelo
        # enlace com o Pi; sem esta ponte registrada o pedido chega e ninguem
        # responde - o comportamento fica mudo sem dar erro.
        PonteDecisao(self._ia, self._comm).registrar(self._event_bus)

        # O chat de texto da webui manda "mission.conversa" pelo mesmo
        # enlace; sem isto registrado o pedido tambem cai no vazio (mesmo
        # defeito recorrente do projeto: publish() sem assinante nao falha e
        # nao aparece em log nenhum - achado ao vivo em 2026-09-13, a webui
        # ficava "pensando" para sempre porque ninguem respondia).
        PonteConversa(self._processar_chat, self._comm).registrar(self._event_bus)

        self._registrar_mentor()
        self._pronto = True
        logger.info("Mission Core ativo (IA remota: %s)", self._conf.get("openai_model"))

    def _construir_ia(self) -> Any:
        from orion.mission.ai_manager import AiManager

        if not os.environ.get("OPENAI_API_KEY"):
            # Nao e erro fatal - o robo sobe e tudo que nao depende de modelo
            # continua de pe. Mas desde que o Ollama saiu (2026-08-05) NAO ha
            # mais reserva local: sem chave, toda conversa vira o aviso de que
            # ele esta sem pensar. Avisar alto aqui e o que evita descobrir
            # isso pela boca do robo.
            logger.warning(
                "sem OPENAI_API_KEY no ambiente - o robo vai dizer que esta sem IA "
                "em toda conversa (nao ha mais reserva local)"
            )
        return AiManager(
            temperatura=self._conf["temperature"],
            caminho_prompt_sistema=self._conf["system_prompt_file"],
            max_tokens_resposta=self._conf["resposta_max_tokens"],
            openai_model=self._conf.get("openai_model", "gemini-flash-lite-latest"),
            openai_base_url=self._conf.get("openai_base_url"),
        )

    @staticmethod
    def _construir_autoconhecimento() -> Any | None:
        """O indice do `content/sobre_mim.yaml`, RECONSTRUIDO a cada subida.

        Reconstruir e nao reaproveitar: o indice so era refeito por
        `tools/aulas.py`, na mao. Editar o YAML e esquecer o comando deixava o
        robo respondendo a versao velha do proprio corpo - e e exatamente o
        arquivo que muda quando o hardware muda. Sao 15 fatos: custa nada.
        """
        try:
            from orion.ensino.autoconhecimento import Autoconhecimento, carregar_arquivo
            from orion.ensino.banco_aulas import caminho_padrao

            auto = Autoconhecimento(caminho_padrao(_RAIZ_PROJETO))
            total = auto.construir(carregar_arquivo(_RAIZ_PROJETO / "content" / "sobre_mim.yaml"))
            logger.info("autoconhecimento: %d fatos sobre o proprio corpo", total)
            return auto
        except Exception:  # noqa: BLE001 - sem ele o robo conversa, so nao sabe de si
            logger.exception("autoconhecimento indisponivel - perguntas do corpo vao para a IA")
            return None

    def _registrar_mentor(self) -> None:
        """Mentor de comportamento: tolerado ausente (Cap 6 s.8).

        Sem OPENAI_API_KEY ou sem a biblioteca `openai`, o Mentor simplesmente
        nunca tem conselho a dar - nao pode derrubar o boot por isso.
        """
        try:
            from orion.mission.conselheiro_comportamento import ConselheiroComportamento
            from orion.mission.ponte_conselho import PonteConselho

            conselheiro = ConselheiroComportamento(
                modelo=self._conf.get("mentor_model", "gemini-flash-lite-latest"),
                base_url=self._conf.get("openai_base_url"),
            )
            PonteConselho(conselheiro, self._comm).registrar(self._event_bus)
            logger.info("Mentor de comportamento ativo (IA remota)")
        except Exception:  # noqa: BLE001
            logger.warning("Mentor de comportamento indisponivel - seguindo sem ele", exc_info=True)

    async def _enviar_comando_hardware(
        self, comando: str, parametros: dict[str, Any] | None = None
    ) -> None:
        """Manda um COMMAND ao ESP32 pela cadeia TCP -> serial e espera o ACK.

        `parametros` (2026-10-31) leva a medida do movimento: distancia_cm,
        graus, velocidade_percent. Movimento passa pelo `CorpoAgora`, que
        mede com o LiDAR antes e depois - o ACK so diz que o ESP32 recebeu,
        nao que a roda andou.
        """
        mensagem = {"comando": comando, **(parametros or {})}

        async def enviar() -> None:
            await self._comm.send("hardware_core", mensagem)

        if self._corpo is not None and self._corpo.e_movimento(comando):
            await self._corpo.conferir(comando, parametros or {}, enviar)
        else:
            await enviar()
        logger.info("Comando %s entregue ao Hardware Core (ACK recebido)", mensagem)

    async def processar(self, texto: str) -> str:
        """Entrada de texto -> resposta. Usada pela voz e pelo chat da web."""
        if self._planner is None:
            raise RuntimeError("ModuloMissao.iniciar() ainda nao foi chamado")
        return await self._planner.processar(texto)

    async def _processar_chat(self, texto: str) -> str:
        """So para o PonteConversa (chat da webui).

        `processar()` tambem e chamado pela voz (VoiceCore ja fala a
        resposta sozinho por ali - ver voice_core.py); publicar aqui de novo
        faria o robo falar duas vezes por comando de voz. O chat digitado
        nunca passa por VoiceCore, entao publica "mission.conversa_resposta"
        para quem quiser falar a resposta em voz alta tambem (ModuloVoz
        assina, 2026-09-13: pedido do dono - "antes ele respondia por voz").
        """
        resposta = await self.processar(texto)
        await self._event_bus.publish("mission.conversa_resposta", {"texto": resposta})
        return resposta

    async def encerrar(self) -> None:
        self._pronto = False
        self._planner = None

    def esta_saudavel(self) -> bool:
        return self._pronto
