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
from typing import Any
from collections.abc import Awaitable, Callable

from orion.kernel.event_bus import EventBus

logger = logging.getLogger("orion.vision.modulo")

CallbackPanTilt = Callable[[float, float], Awaitable[None]]

#: De quanto em quanto tempo tentar de novo buscar os rostos no banco do Pi.
#: 10s cobre com folga o pior boot medido (enlace de pe 2min depois do
#: Notebook) sem transformar a espera num pedido a cada segundo.
INTERVALO_RETENTATIVA_FAMILIA_S = 10.0

#: 30 x 10s = 5 minutos. Passou disso, o problema nao e o Pi demorando.
MAX_TENTATIVAS_FAMILIA = 30


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
        comm: Any | None = None,
    ) -> None:
        # `nucleo` injetavel: os testes passam um dublê e nao precisam de
        # camera, YOLO nem face_recognition instalados.
        self._event_bus = event_bus
        self._conf = conf_visao
        self._publicar_pan_tilt = publicar_pan_tilt
        self._nucleo = nucleo
        self._conf_sentinela = conf_sentinela
        self._carregar_conhecidos = carregar_conhecidos
        self._comm = comm
        self._ultimo_pan_tilt_ts = 0.0
        self._ultimo_pan = 0.0
        self._ultimo_tilt = 0.0
        self._sentinela: Any | None = None
        self._tarefa: asyncio.Task | None = None
        self._tarefa_familia: asyncio.Task | None = None

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
        if not await self._carregar_familia():
            self._tarefa_familia = asyncio.create_task(self._insistir_familia())
        self._ligar_sentinela()
        await self._ligar_gestos()
        self._tarefa = asyncio.create_task(self._nucleo.executar())
        logger.info("Vision Core ativo (dono da camera %s)", self._conf["camera_indice_principal"])

    async def _carregar_familia(self) -> bool:
        """Ensina ao Vision Core quem e da casa. Devolve se conseguiu.

        Sem isso TODO mundo vira estranho e a Sentinela alerta sem parar -
        por isso a falha aqui e tolerada mas nunca silenciosa (Cap 6 s.8): o
        link com o Pi, que guarda os rostos, pode ainda estar subindo.
        """
        if self._carregar_conhecidos is None:
            # Unico caminho legitimamente mudo: nao ha de onde carregar
            # (testes e maquina de desenvolvimento). Nao ha o que reter.
            return True
        try:
            pessoas = await self._carregar_conhecidos()
        except Exception:  # noqa: BLE001
            logger.warning("Nao consegui carregar os rostos conhecidos - seguindo sem", exc_info=True)
            return False
        conhecidos = [p for p in (pessoas or []) if p.get("embedding_face")]
        if not conhecidos:
            logger.warning("Nenhum rosto conhecido no banco - Sentinela ficaria alertando de tudo")
            return False
        self._nucleo.carregar_pessoas_conhecidas(conhecidos)
        logger.info("Vision Core: %d rosto(s) conhecido(s) carregado(s)", len(conhecidos))
        return True

    async def _insistir_familia(self) -> None:
        """Tenta de novo ate os rostos entrarem - o boot nao e a unica chance.

        FALHA REAL DE 2026-08-05, de manha: o Notebook subiu antes do Pi. Os
        rostos moram no banco do Pi e sao pedidos pelo enlace TCP, que so
        ficou de pe 2 minutos depois. A carga acontecia UMA VEZ, no boot, e
        ja tinha passado - entao a lista ficou vazia pelo resto do dia.
        O robo olhou o dono de frente e a Sentinela registrou "1 rosto
        desconhecido"; depois, perguntado se tinha visto o Kamal, respondeu a
        verdade: nao tinha visto ninguem. Nada estava quebrado, nada deu erro
        e nenhum teste reprovou - o robo so nao conhecia mais a familia.
        O mesmo ja havia acontecido em 04/08 (00:37 e 21:29), com o aviso no
        log e ninguem para le-lo.

        Insistir e melhor que assinar um evento de "link subiu": nao cria
        topico novo (e este projeto ja publicou no vazio tres vezes) e cobre
        tambem o banco que demora, nao so o enlace que atrasa.

        Desiste alto depois de MAX_TENTATIVAS_FAMILIA: um robo que trata a
        casa inteira como estranha precisa dizer isso em voz alta no log, e
        nao seguir tentando calado para sempre.
        """
        for tentativa in range(1, MAX_TENTATIVAS_FAMILIA + 1):
            await asyncio.sleep(INTERVALO_RETENTATIVA_FAMILIA_S)
            if await self._carregar_familia():
                logger.info("Rostos da familia carregados na tentativa %d", tentativa)
                return
        logger.error(
            "Desisti de carregar os rostos apos %d tentativas - o robo vai tratar "
            "TODA a familia como estranha ate ser reiniciado",
            MAX_TENTATIVAS_FAMILIA,
        )

    async def _ligar_gestos(self) -> None:
        """Gestos de mao alimentados pelos frames de quem ja le a camera.

        Ate 2026-07-28 o LacoGestos nao era instanciado em lugar NENHUM do
        projeto - ele nasceu (20/07) como dono da camera, e o EDR-0023 passou
        esse papel para ca sem trazer os gestos junto. Resultado: modelo
        baixado, testes passando, e nenhum gesto jamais produzido.

        Falhar aqui nao pode derrubar a visao: gesto e acessorio.
        """
        from orion.vision.gestos import LacoGestos

        try:
            laco = LacoGestos(self._event_bus, captura=None)
            if await laco.preparar():
                self._nucleo.registrar_observador_de_frame(laco.processar_frame)
                self._gestos = laco
        except Exception:
            logger.exception("Gestos indisponiveis - visao segue sem eles")

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
        # A retentativa dos rostos morre junto: deixa-la viva depois do
        # encerramento e a tarefa orfa que o EDR-0023 ja pagou uma vez.
        if self._tarefa_familia is not None:
            self._tarefa_familia.cancel()
            self._tarefa_familia = None
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

    def ver_agora(self) -> dict | None:
        """O que a camera ve neste instante - para o planner responder
        "o que e isso?" (2026-08-09).

        A cadeia que faltava: a visao via e o DIARIO guardava, mas nenhum
        codigo consultava o AGORA. Este metodo entrega o estado da ultima
        cena processada pelo Vision Core, sem abrir um segundo leitor da
        camera (EDR-0023 s.3) e sem duplicar o trabalho de deteccao.

        None em dois casos legitimamente: o nucleo ainda nao existe
        (iniciar() nao rodou, ou falhou antes de montar a camera) ou
        nenhum frame foi processado ainda. Nunca levanta: visao fora e
        resposta honesta ("nao estou vendo nada"), nao mudo.
        """
        ver = getattr(self._nucleo, "ver_agora", None)
        if ver is None:
            return None
        try:
            return ver()
        except Exception:
            logger.debug("falha ao consultar a visao atual", exc_info=True)
            return None

    def foto_agora(self) -> bytes | None:
        """O ultimo quadro da camera em JPEG, ou None (2026-11-01).

        Serve o `/camera/snapshot.jpg` da API: ver de fora o que o robo ve,
        sem disputar a camera com o Vision Core. Nunca levanta.
        """
        foto = getattr(self._nucleo, "ultimo_frame_jpeg", None)
        if foto is None:
            return None
        try:
            return foto()
        except Exception:
            logger.debug("falha ao gerar o snapshot da camera", exc_info=True)
            return None

    def pausar(self) -> None:
        """Suspende a visao inteira sob pressao de memoria (EDR-0020).

        FECHA UMA PENDENCIA DECLARADA (2026-07-30). O comentario em
        `conversar_jarvis.py` dizia, desde o merge de 26/07: *"o ModuloVisao do
        EDR-0023 ainda nao expoe pausar()/retomar() - so iniciar()/encerrar().
        Ate ele expor, o alivio descarrega o modelo de IA mas NAO consegue
        pausar a visao, que e o maior consumidor de RAM"*. E o alivio recebia
        `pausar_visao=None`, entao nao pausava nada.

        Pausa o nucleo (para o YOLO e o reconhecimento facial de rodar) e a
        Sentinela (para nao alertar sobre quadros que ninguem esta olhando).
        Nunca levanta: alivio parcial e melhor que nenhum numa hora dessas.
        """
        for alvo, nome in ((self._nucleo, "Vision Core"), (self._sentinela, "Sentinela")):
            if alvo is None:
                continue
            try:
                alvo.pausar()
            except Exception:  # noqa: BLE001
                logger.warning("falha ao pausar %s", nome, exc_info=True)

    def retomar(self) -> None:
        for alvo, nome in ((self._nucleo, "Vision Core"), (self._sentinela, "Sentinela")):
            if alvo is None:
                continue
            try:
                alvo.retomar()
            except Exception:  # noqa: BLE001
                logger.warning("falha ao retomar %s", nome, exc_info=True)

    async def _mover_servos(self, pan: float, tilt: float) -> None:
        """Manda o Vision Core mover os servos FISICOS da camera.

        Ate 2026-07-25 o VisionCore calculava o pan/tilt, publicava
        motion.pan_tilt - e ninguem escutava do lado do hardware. So o avatar
        do Notebook consumia, entao a camera se mexia na TELA e ficava parada
        no mundo real. O fio final nunca tinha sido ligado.

        LIMITADOR DE TAXA: a visao roda a ate 15 quadros/s; mandar um comando
        por quadro pela serial afogaria o enlace e atrasaria os heartbeats,
        que e justamente como o sistema detecta falha. Envia no maximo a cada
        150 ms, e so quando o angulo mudou o suficiente para valer - servo
        nao resolve fracao de grau mesmo.
        """
        import time

        agora = time.monotonic()
        if agora - self._ultimo_pan_tilt_ts < 0.15:
            return
        if abs(pan - self._ultimo_pan) < 1.0 and abs(tilt - self._ultimo_tilt) < 1.0:
            return
        self._ultimo_pan_tilt_ts = agora
        self._ultimo_pan, self._ultimo_tilt = pan, tilt
        try:
            await self._comm.send(
                "hardware_core",
                {"comando": "SET_PAN_TILT", "pan_graus": pan, "tilt_graus": tilt},
            )
        except Exception:
            # Cap 6 s.8: Arduino ausente nao pode derrubar a visao - ela
            # continua detectando e publicando, so nao move o servo.
            logger.debug("falha ao mover pan/tilt", exc_info=True)

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
            # A CAMERA NAO MEXE MAIS A CABECA SOZINHA (2026-11-01, dono: muito
            # chefe para pouco indio). A visao segue vendo, reconhecendo e
            # informando; quem vira a cabeca e a IA (ou o joystick). Volta com
            # vision.cabeca_segue_pessoa: true no orion.yaml.
            publicar_pan_tilt=(
                (self._publicar_pan_tilt or self._mover_servos)
                if self._conf.get("cabeca_segue_pessoa", False)
                else None
            ),
            # Quadros seguidos com a mesma deteccao antes de confirmar o alvo
            # e mover o servo. Vinha FIXO em 3 aqui (contra a regra 6 da
            # arquitetura) e por isso nunca aparecia em investigacao nenhuma;
            # agora e `vision.target_confirmation_frames` no orion.yaml, onde o
            # comentario explica por que o valor e 1. O `.get` com default
            # cobre um orion.yaml mais antigo, como o `imgsz` logo abaixo.
            frames_confirmacao_alvo=self._conf.get("target_confirmation_frames", 1),
            fps=self._conf["fps"],
            # .get com default 640 para nao quebrar um orion.yaml antigo que
            # ainda nao tenha a chave (o Pi e o Notebook nem sempre atualizam
            # a config no mesmo instante).
            imgsz=self._conf.get("yolo_imgsz", 640),
            # Ritmo do reconhecimento facial. `.get` com default 1 mantem o
            # comportamento antigo (todo quadro) num orion.yaml que ainda nao
            # tenha a chave - o Pi e o Notebook nem sempre atualizam a config
            # no mesmo instante.
            reconhecer_rosto_a_cada=self._conf.get("face_recognition_every_n_frames", 1),
            confianca_para_descrever=self._conf.get("confianca_para_descrever"),
            odometria_visual=_odometria_visual(self._conf.get("odometria_visual") or {}),
        )


def _odometria_visual(conf: dict[str, Any]) -> Any:
    """O medidor de giro pela camera, ou None se `ativo: false` (2026-11-12).

    Liga por padrao numa config que tenha a secao: medir nao mexe em nada -
    quem decide se a medida corrige a pose e o peso na fusao do Pi.
    """
    if not conf or not conf.get("ativo", True):
        return None
    from orion.vision.odometria_visual import (
        FOV_HORIZONTAL_PADRAO_GRAUS,
        RESPOSTA_MINIMA_PADRAO,
        OdometriaVisual,
    )

    return OdometriaVisual(
        fov_horizontal_graus=conf.get("fov_horizontal_graus", FOV_HORIZONTAL_PADRAO_GRAUS),
        resposta_minima=conf.get("resposta_minima", RESPOSTA_MINIMA_PADRAO),
    )


def _nome_do_arquivo_do_modelo(valor: str) -> str:
    """A config traz "yolov8n"; o arquivo no disco e "yolov8n.pt".

    Sem o sufixo o Ultralytics trata como nome de catalogo e tenta BAIXAR o
    modelo - o que quebra a promessa de funcionar sem internet.
    """
    return valor if valor.endswith(".pt") else f"{valor}.pt"
