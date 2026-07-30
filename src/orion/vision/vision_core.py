"""Orquestrador do Vision Core (Cap 8).

Liga captura, deteccao YOLO, reconhecimento facial, rastreamento e
Pan/Tilt num unico pipeline, publicando eventos no Event Bus local do
Notebook (Cap 8 s.5). O Vision Core nunca toma decisao estrategica (Cap 8
s.6) - so publica informacao estruturada.

Recuperacao de camera desconectada (Cap 8 s.9): ao falhar, publica
vision.camera_error e tenta reabrir periodicamente, mantendo o resto do
ORION OS operacional (modo SEM_VISAO) em vez de derrubar o processo.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

import cv2

from orion.kernel.event_bus import EventBus, Prioridade
from orion.vision.captura import CapturaCamera, ErroCamera
from orion.vision.deteccao import Deteccao, DetectorYolo
from orion.vision.pan_tilt import CalculadoraPanTilt, LimitesPanTilt
from orion.vision.rastreamento import Caixa, Rastreador
from orion.vision.reconhecimento_facial import ReconhecedorFacial, RostoReconhecido

logger = logging.getLogger("orion.vision.vision_core")

CallbackPanTilt = Callable[[float, float], Awaitable[None]]

#: Marca os `motion.pan_tilt` que ESTA classe publicou, para nao ressincronizar
#: a estimativa com o proprio eco (ver `_ao_outro_dono_mover_a_cabeca`).
#:
#: So a visao se identifica, de proposito: quem NAO se identifica e tratado
#: como outro dono. Assim qualquer publicador futuro de `motion.pan_tilt` passa
#: a ressincronizar a visao sem precisar saber que esta calculadora existe -
#: o default fica no lado correto.
ORIGEM_VISAO = "visao"


class VisionCore:
    def __init__(
        self,
        event_bus: EventBus,
        indice_camera: int | str = 0,
        camera_espelhada: bool = False,
        confianca_minima: float = 0.55,
        modelo_yolo: str = "yolov8n.pt",
        limites_pan_tilt: LimitesPanTilt | None = None,
        publicar_pan_tilt: CallbackPanTilt | None = None,
        captura: CapturaCamera | None = None,
        detector: DetectorYolo | None = None,
        reconhecedor: ReconhecedorFacial | None = None,
        timeout_alvo_perdido_s: float = 2.0,
        frames_confirmacao_alvo: int = 1,
        fps: float = 0.0,
        imgsz: int = 640,
        reconhecer_rosto_a_cada: int = 1,
    ) -> None:
        # captura/detector/reconhecedor sao injetaveis para testar a logica
        # de orquestracao (associacao de identidade, eventos, rastreamento)
        # sem precisar de camera/modelos de verdade.
        self._event_bus = event_bus
        self._captura = captura or CapturaCamera(indice=indice_camera, espelhado=camera_espelhada)
        #: Quem quer ver os frames sem abrir a camera (EDR-0023 s.3: so o
        #: dono da camera tem o frame). Hoje: o detector de gestos.
        self._observadores_de_frame: list = []
        # modelo_yolo vem do orion.yaml (vision.yolo_model). Antes o nome do
        # modelo ficava fixo no default do DetectorYolo e a config era
        # ignorada - configuracao morta, contra a regra 6 do CLAUDE.md.
        self._detector = detector or DetectorYolo(
            modelo=modelo_yolo, confianca_minima=confianca_minima, imgsz=imgsz
        )
        self._reconhecedor = reconhecedor or ReconhecedorFacial()
        self._rastreador = Rastreador(
            timeout_perdido_s=timeout_alvo_perdido_s, frames_confirmacao=frames_confirmacao_alvo
        )
        self._pan_tilt = CalculadoraPanTilt(limites_pan_tilt)
        self._publicar_pan_tilt = publicar_pan_tilt
        self._executando = False
        #: Pausa o PROCESSAMENTO sem fechar a camera (alivio de carga,
        #: EDR-0020). Fechar o dispositivo seria pior: reabrir custa segundos
        #: e nem sempre da certo, e o objetivo e passar por um aperto de
        #: memoria, nao perder a visao de vez.
        self._pausado = False
        self._tinha_alvo = False
        #: A cada quantos quadros rodar o reconhecimento facial (Cap 8 s.4).
        #:
        #: 1 = todo quadro, o comportamento anterior a 2026-07-30. Vem de
        #: `vision.face_recognition_every_n_frames` no orion.yaml, onde estao
        #: as medicoes que justificam o valor. Resumo: o reconhecimento custa
        #: 442-716 ms contra 125 ms do YOLO, e nao da para baratea-lo sem
        #: perder rosto - entao o que muda e o RITMO, nao a qualidade.
        #:
        #: `max(1, ...)` porque 0 ou negativo aqui nao significa nada e
        #: dividiria por zero no `%` abaixo.
        self._reconhecer_a_cada = max(1, int(reconhecer_rosto_a_cada))
        self._quadros_processados = 0
        #: Ultimo resultado do reconhecimento, reaproveitado nos quadros em
        #: que ele nao roda. NAO e cache de otimizacao: e o que impede a
        #: identidade de piscar. Devolver lista vazia nos quadros
        #: intermediarios faria o alvo perder o nome e voltar a te-lo, e o
        #: FOLLOW (que so segue gente RECONHECIDA) largaria a pessoa a cada
        #: dois quadros.
        self._ultimos_rostos: list[RostoReconhecido] = []
        self._ultimo_processamento_ts: float | None = None
        self._ultimo_frame = None
        # 0 = sem limite (comportamento antigo). Vem de vision.fps no
        # orion.yaml: ate 2026-07-25 essa chave era configuracao MORTA e o
        # laco rodava a toda velocidade, medido em 131% de CPU disputando os
        # 4 nucleos com o Whisper. Ver _dormir_o_resto_do_quadro.
        self._intervalo_alvo_s = 1.0 / fps if fps and fps > 0 else 0.0
        # A cabeca tem DOIS donos e ate 2026-07-29 este modulo nao tinha um
        # unico `subscribe`: ele mandava SET_PAN_TILT e assumia que ninguem
        # mais tocava nos servos. Os comportamentos do Pi tocam. Assinar aqui
        # e o que impede a estimativa de virar ficcao - ver
        # CalculadoraPanTilt.sincronizar.
        event_bus.subscribe("motion.pan_tilt", self._ao_outro_dono_mover_a_cabeca)

    async def _ao_outro_dono_mover_a_cabeca(self, evento) -> None:
        """Ressincroniza a posicao assumida dos servos.

        Ignora o proprio eco: esta classe publica `motion.pan_tilt` a cada
        quadro com alvo, e o Pi repassa os DELE para ca - as duas coisas
        chegam no mesmo topico do mesmo barramento.
        """
        if evento.dados.get("origem") == ORIGEM_VISAO:
            return
        pan = evento.dados.get("pan")
        tilt = evento.dados.get("tilt")
        if pan is None or tilt is None:
            return
        try:
            self._pan_tilt.sincronizar(pan, tilt)
        except (TypeError, ValueError):
            # payload de outra maquina: dado ruim nao pode derrubar o handler.
            logger.warning("motion.pan_tilt com pan/tilt invalido: %r", evento.dados)
            return
        logger.debug("pan/tilt ressincronizado por outro dono: %s, %s", pan, tilt)

    def salvar_ultimo_frame(self, caminho: str) -> str | None:
        """Grava em disco a ultima imagem lida. Devolve o caminho, ou None se
        ainda nao houve frame (camera ausente ou recem-aberta).

        Existe aqui porque so o dono da camera tem o frame: quem precisa da
        foto pede, em vez de abrir o dispositivo por conta propria.
        """
        if self._ultimo_frame is None:
            return None
        cv2.imwrite(caminho, self._ultimo_frame)
        return caminho

    def carregar_pessoas_conhecidas(self, pessoas: list[dict]) -> None:
        self._reconhecedor.carregar_pessoas_conhecidas(pessoas)

    async def iniciar_camera(self) -> None:
        try:
            await self._captura.abrir()
        except ErroCamera as erro:
            await self._event_bus.publish(
                "vision.camera_error", {"motivo": str(erro)}, prioridade=Prioridade.ALTA
            )
            raise

    def registrar_observador_de_frame(self, callback) -> None:
        """Recebe cada frame lido, sem abrir um segundo leitor do device.

        Existe porque o EDR-0023 fez o ModuloVisao dono da camera, e o
        detector de gestos - que ANTES era a fonte de frames da casa - ficou
        orfao: a classe existia, com testes e modelo baixado, e nunca era
        instanciada. Nenhum gesto chegou a ser produzido em producao.
        """
        self._observadores_de_frame.append(callback)

    async def processar_um_frame(self):
        """Uma iteracao do pipeline (Cap 8 s.3). Retorna o frame lido (BGR,
        formato OpenCV) - util para quem quiser exibir/depurar."""
        agora = time.monotonic()
        intervalo_s = (
            agora - self._ultimo_processamento_ts if self._ultimo_processamento_ts else 0.1
        )
        self._ultimo_processamento_ts = agora

        try:
            frame = await self._captura.ler_frame()
        except ErroCamera as erro:
            await self._event_bus.publish(
                "vision.camera_error", {"motivo": str(erro)}, prioridade=Prioridade.ALTA
            )
            raise

        # Observadores do frame (EDR-0023 s.3: só o dono da câmera tem o
        # frame). Existe para o detector de gestos poder trabalhar sem abrir
        # um segundo leitor do dispositivo. Falha de observador NUNCA derruba
        # a visão - é acessório reagindo a um frame que já foi lido.
        for observador in self._observadores_de_frame:
            try:
                await observador(frame)
            except Exception:
                logger.debug("observador de frame falhou", exc_info=True)

        deteccoes = await self._detector.detectar(frame)
        pessoas_detectadas = DetectorYolo.pessoas(deteccoes)

        # O RECONHECIMENTO FACIAL TEM RITMO PROPRIO (2026-07-30), porque ele
        # custa 442-716 ms contra 125 ms do YOLO e nao da para baratea-lo sem
        # perder rosto (as medicoes estao no orion.yaml, em
        # `face_recognition_every_n_frames`). Com `_reconhecer_a_cada = 1` o
        # comportamento e exatamente o de antes.
        #
        # O contador incrementa DEPOIS do teste para que o primeiro quadro
        # sempre reconheca: comecar a vida sem identidade nenhuma por N
        # quadros e o pior momento para economizar.
        reconheceu_agora = (self._quadros_processados % self._reconhecer_a_cada) == 0
        self._quadros_processados += 1
        if reconheceu_agora:
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            self._ultimos_rostos = await self._reconhecedor.reconhecer(frame_rgb)
        rostos = self._ultimos_rostos
        # guardado para quem precisar da imagem do momento (a Sentinela salva
        # a foto do estranho). So o dono da camera tem o frame (EDR-0023 s.3).
        self._ultimo_frame = frame

        # Rostos que nao casaram com ninguem conhecido. A Sentinela consome
        # este evento em vez de abrir a propria camera e rodar um SEGUNDO
        # reconhecimento facial sobre os mesmos frames - era o trabalho mais
        # pesado do Notebook, feito em dobro ate 2026-07-25.
        #
        # `and reconheceu_agora`: so anuncia desconhecido quando ACABOU de
        # olhar. Sem isso, um mesmo rosto reaproveitado dispararia o evento a
        # cada quadro e a Sentinela salvaria a mesma cara N vezes - ela ja
        # tinha 37 fotos acumuladas em data/sentinela antes desta mudanca.
        desconhecidos = [rosto for rosto in rostos if rosto.pessoa_id is None]
        if desconhecidos and reconheceu_agora:
            await self._event_bus.publish(
                "vision.faces_desconhecidas",
                {"quantidade": len(desconhecidos), "timestamp": agora},
                prioridade=Prioridade.ALTA,
            )

        for deteccao in deteccoes:
            topico = "vision.person_detected" if deteccao.classe == "person" else "vision.object_detected"
            await self._event_bus.publish(
                topico,
                {
                    "classe": deteccao.classe,
                    "confianca": deteccao.confianca,
                    "caixa": deteccao.caixa,
                    "timestamp": agora,
                },
            )

        identidades = self._associar_identidades(pessoas_detectadas, rostos)
        caixas = [d.caixa for d in pessoas_detectadas]
        alvo = self._rastreador.atualizar(caixas, identidades, agora=agora)

        if alvo is not None:
            self._tinha_alvo = True
            altura, largura = frame.shape[:2]
            cx, cy = self._centro_caixa(alvo.caixa)
            if alvo.pessoa_id is not None:
                # centro_x normalizado (0..1, 0.5 = centralizado): o FOLLOW
                # do NavigationCore (Pi) so anda atras de gente RECONHECIDA -
                # por isso ele escuta este evento, e nao vision.person_detected
                # (que dispara para qualquer pessoa, reconhecida ou nao).
                await self._event_bus.publish(
                    "vision.person_recognized",
                    {
                        "pessoa_id": alvo.pessoa_id,
                        "nome": alvo.nome,
                        "centro_x": cx / largura,
                        "timestamp": agora,
                    },
                )
            pan, tilt = self._pan_tilt.calcular_correcao(cx, cy, largura, altura, intervalo_s)
            # publica sempre (mesmo sem callback de hardware) - o avatar
            # (Cap 13) reflete a direcao calculada independente do robo
            # fisico estar montado ou nao.
            await self._event_bus.publish(
                "motion.pan_tilt", {"pan": pan, "tilt": tilt, "origem": ORIGEM_VISAO}
            )
            if self._publicar_pan_tilt is not None:
                await self._publicar_pan_tilt(pan, tilt)

            if self._pan_tilt.alvo_centralizado(cx, cy, largura, altura):
                await self._event_bus.publish(
                    "vision.target_centered", {"pan": pan, "tilt": tilt, "timestamp": agora}
                )
        elif self._tinha_alvo:
            await self._event_bus.publish("vision.person_lost", {"timestamp": agora})
            self._tinha_alvo = False

        return frame

    async def executar(self, intervalo_reconexao_s: float = 3.0) -> None:
        """Loop continuo ate parar() ser chamado. Reconecta automaticamente
        se a camera cair (Cap 8 s.9)."""
        self._executando = True
        while self._executando:
            if self._pausado:
                # Nao processa quadro nenhum: e aqui que o alivio de carga
                # economiza de verdade. O YOLO e o face_recognition sao o
                # trabalho mais pesado do Notebook, e ate 2026-07-30 o alivio
                # nunca chegava a pausa-los - `pausar_visao` era passado como
                # None em conversar_fofao.py.
                await asyncio.sleep(0.5)
                continue
            if not self._captura.aberta:
                try:
                    await self.iniciar_camera()
                except ErroCamera:
                    await asyncio.sleep(intervalo_reconexao_s)
                    continue
            inicio = time.monotonic()
            try:
                await self.processar_um_frame()
            except ErroCamera:
                await self._captura.fechar()
                await asyncio.sleep(intervalo_reconexao_s)
                continue
            await self._dormir_o_resto_do_quadro(inicio)

    async def _dormir_o_resto_do_quadro(self, inicio: float) -> None:
        """Devolve ao sistema o tempo que sobrou do orcamento do quadro.

        Sem isto o laco processa o mais rapido que a CPU aguentar - e num
        Notebook de 4 nucleos onde o Whisper tambem precisa de CPU, isso
        engasga a voz. Se o quadro ja estourou o orcamento, nao dorme (mas
        cede a vez com sleep(0), senao um quadro lento monopoliza o loop de
        eventos e ninguem mais roda).
        """
        if not self._intervalo_alvo_s:
            return
        sobra = self._intervalo_alvo_s - (time.monotonic() - inicio)
        await asyncio.sleep(sobra if sobra > 0 else 0)

    def parar(self) -> None:
        self._executando = False

    def pausar(self) -> None:
        """Suspende o processamento, mantendo a camera aberta."""
        if not self._pausado:
            self._pausado = True
            logger.warning("Vision Core PAUSADO (alivio de carga) - camera segue aberta")

    def retomar(self) -> None:
        if self._pausado:
            self._pausado = False
            logger.info("Vision Core retomado")

    @property
    def pausado(self) -> bool:
        return self._pausado

    @staticmethod
    def _centro_caixa(caixa: Caixa) -> tuple[float, float]:
        x1, y1, x2, y2 = caixa
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    @staticmethod
    def _associar_identidades(
        pessoas_detectadas: list[Deteccao], rostos: list[RostoReconhecido]
    ) -> dict[Caixa, tuple[int | None, str | None]]:
        """Casa cada caixa de pessoa (YOLO) com o rosto (face_recognition)
        de maior sobreposicao - vem de dois modelos diferentes, cada um
        com sua propria caixa."""
        identidades: dict[Caixa, tuple[int | None, str | None]] = {}
        for pessoa in pessoas_detectadas:
            melhor: RostoReconhecido | None = None
            melhor_sobreposicao = 0.0
            for rosto in rostos:
                sobreposicao = VisionCore._sobreposicao(pessoa.caixa, rosto.caixa)
                if sobreposicao > melhor_sobreposicao:
                    melhor_sobreposicao = sobreposicao
                    melhor = rosto
            if melhor is not None and melhor_sobreposicao > 0:
                identidades[pessoa.caixa] = (melhor.pessoa_id, melhor.nome)
        return identidades

    @staticmethod
    def _sobreposicao(caixa_a: Caixa, caixa_b: Caixa) -> float:
        ax1, ay1, ax2, ay2 = caixa_a
        bx1, by1, bx2, by2 = caixa_b
        largura_i = max(0, min(ax2, bx2) - max(ax1, bx1))
        altura_i = max(0, min(ay2, by2) - max(ay1, by1))
        return float(largura_i * altura_i)
