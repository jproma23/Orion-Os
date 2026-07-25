"""Modulo de visao do Mission Core (EDR-0023).

Primeiro modulo a cumprir o contrato ModuloOrion. Envolve o VisionCore, que
ja existia completo (pipeline, reconexao de camera, publicacao de eventos)
mas nunca era instanciado em producao por nao haver onde liga-lo.

Este modulo e o DONO da camera de visao (EDR-0023 secao 3): ninguem mais
abre /dev/videoN. Quem precisa do que a camera ve assina os eventos
`vision.*` no Event Bus. Foi exatamente essa disputa que travava a
integracao - a SentinelaVisao abria a propria captura em paralelo.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from orion.kernel.event_bus import EventBus

logger = logging.getLogger("orion.vision.modulo")

CallbackPanTilt = Callable[[float, float], Awaitable[None]]


class ModuloVisao:
    """Ciclo de vida do Vision Core para o Boot Manager."""

    nome = "vision"

    def __init__(
        self,
        event_bus: EventBus,
        conf_visao: dict[str, Any],
        publicar_pan_tilt: CallbackPanTilt | None = None,
        nucleo: Any | None = None,
        conf_sentinela: dict[str, Any] | None = None,
        carregar_conhecidos: Any | None = None,
    ) -> None:
        # `nucleo` injetavel: os testes passam um dublê e nao precisam de
        # camera, YOLO nem face_recognition instalados.
        self._event_bus = event_bus
        self._conf = conf_visao
        self._publicar_pan_tilt = publicar_pan_tilt
        self._nucleo = nucleo
        self._conf_sentinela = conf_sentinela
        self._carregar_conhecidos = carregar_conhecidos
        self._sentinela: Any | None = None
        self._tarefa: asyncio.Task | None = None

    async def iniciar(self) -> None:
        if self._nucleo is None:
            # YOLO(...) le o modelo do disco no __init__ e bloqueia por
            # segundos. Construir isso na thread do event loop atrasaria os
            # heartbeats no meio do boot - por isso vai para outra thread.
            self._nucleo = await asyncio.to_thread(self._construir)
        # executar() ja tolera camera ausente sozinho (Cap 8 s.9): publica
        # vision.camera_error e segue tentando reabrir, mantendo o resto do
        # ORION OS de pe em modo SEM_VISAO. Por isso o modulo sobe mesmo sem
        # camera - "sem imagem" nao e o mesmo que "modulo quebrado".
        await self._carregar_familia()
        self._ligar_sentinela()
        self._tarefa = asyncio.create_task(self._nucleo.executar())
        logger.info("Vision Core ativo (dono da camera %s)", self._conf["camera_indice_principal"])

    async def _carregar_familia(self) -> None:
        """Ensina ao Vision Core quem e da casa.

        Sem isso TODO mundo vira estranho e a Sentinela alerta sem parar -
        por isso a falha aqui e tolerada mas nunca silenciosa (Cap 6 s.8): o
        link com o Pi, que guarda os rostos, pode ainda estar subindo.
        """
        if self._carregar_conhecidos is None:
            return
        try:
            pessoas = await self._carregar_conhecidos()
        except Exception:  # noqa: BLE001
            logger.warning("Nao consegui carregar os rostos conhecidos - seguindo sem", exc_info=True)
            return
        conhecidos = [p for p in (pessoas or []) if p.get("embedding_face")]
        if not conhecidos:
            logger.warning("Nenhum rosto conhecido no banco - Sentinela ficaria alertando de tudo")
            return
        self._nucleo.carregar_pessoas_conhecidas(conhecidos)
        logger.info("Vision Core: %d rosto(s) conhecido(s) carregado(s)", len(conhecidos))

    def _ligar_sentinela(self) -> None:
        """A Sentinela agora e uma ouvinte dos eventos do Vision Core.

        Ela some da lista de donos de camera (EDR-0023 s.3) e para de rodar
        um segundo reconhecimento facial sobre os mesmos frames.
        """
        if not self._conf_sentinela or not self._conf_sentinela.get("habilitado"):
            return
        from orion.vision.sentinela_visao import SentinelaVisao

        self._sentinela = SentinelaVisao(
            self._event_bus,
            cooldown_s=self._conf_sentinela["cooldown_s"],
            pasta_fotos=self._conf_sentinela["pasta_fotos"],
            salvar_foto=self._nucleo.salvar_ultimo_frame,
        )
        self._sentinela.registrar()

    async def encerrar(self) -> None:
        if self._nucleo is not None:
            self._nucleo.parar()
        if self._tarefa is not None:
            self._tarefa.cancel()
            try:
                await self._tarefa
            except (asyncio.CancelledError, Exception):  # noqa: B014
                # encerrar() nunca levanta (contrato do EDR-0023): estamos
                # desligando, e uma falha aqui nao pode impedir os proximos
                # modulos de soltarem os recursos deles.
                pass
            self._tarefa = None

    def esta_saudavel(self) -> bool:
        return self._tarefa is not None and not self._tarefa.done()

    def _construir(self) -> Any:
        # import local: puxa cv2/ultralytics/face_recognition, que sao
        # pesados e podem faltar numa maquina de desenvolvimento. Assim o
        # simples import deste arquivo nao exige a pilha de visao inteira.
        from orion.vision.pan_tilt import LimitesPanTilt
        from orion.vision.vision_core import VisionCore

        pan_min, pan_max = self._conf["pan_limits_degrees"]
        tilt_min, tilt_max = self._conf["tilt_limits_degrees"]
        return VisionCore(
            self._event_bus,
            indice_camera=self._conf["camera_indice_principal"],
            camera_espelhada=self._conf["camera_frontal_espelhada"],
            confianca_minima=self._conf["confidence_threshold"],
            modelo_yolo=_nome_do_arquivo_do_modelo(self._conf["yolo_model"]),
            limites_pan_tilt=LimitesPanTilt(
                pan_min_graus=pan_min,
                pan_max_graus=pan_max,
                tilt_min_graus=tilt_min,
                tilt_max_graus=tilt_max,
                velocidade_max_graus_s=self._conf["servo_max_speed_deg_s"],
            ),
            publicar_pan_tilt=self._publicar_pan_tilt,
        )


def _nome_do_arquivo_do_modelo(valor: str) -> str:
    """A config traz "yolov8n"; o arquivo no disco e "yolov8n.pt".

    Sem o sufixo o Ultralytics trata como nome de catalogo e tenta BAIXAR o
    modelo - o que quebra a promessa de funcionar sem internet.
    """
    return valor if valor.endswith(".pt") else f"{valor}.pt"
