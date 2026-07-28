"""Gestos de mão pela câmera (Cap 8; pedido do dono em 2026-07-20).

O canal do Kamal (3 anos): ele não digita e a escuta por voz saiu - então
a mão fala. Vocabulário definido pelo dono:

    joinha (polegar pra cima)        = SIM
    mão aberta balançando            = VEM CÁ

O "não" (dedo indicador balançando) foi SUBSTITUÍDO pelo chamado em
2026-07-28, a pedido do dono. Consequência que vale dizer em voz alta: o
Kamal fica sem gesto para dizer NÃO - o canal ficou com "sim" e "vem cá".
Se o não fizer falta, é só escolher outra pose (mão espalmada parada, por
exemplo) e ligar aqui.

Duas peças, separadas de propósito:

- `DetectorGestos`: SÓ a lógica - recebe os 21 pontos da mão (normalizados
  0..1, como o MediaPipe entrega) e decide o gesto. Nenhuma dependência de
  câmera/mediapipe → testável com pontos sintéticos.
- `LacoGestos`: o laço ao vivo - lê a câmera (~5 fps), extrai os pontos
  com o MediaPipe Hands (model_complexity=0, o mais leve) e publica
  `gesto.detectado {tipo: "sim"|"nao"}` no Event Bus. Também guarda o
  último frame num cache que a `CameraCompartilhada` serve à sentinela de
  rostos - UMA leitura de câmera para os dois consumidores, sem briga
  pelo dispositivo.

Índices dos pontos do MediaPipe Hands usados aqui:
    0=pulso  2=polegar_mcp 3=polegar_ip 4=polegar_ponta
    5/6/8   =indicador mcp/pip/ponta   9/10/12=médio
    13/14/16=anelar                    17/18/20=mindinho
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from orion.kernel.event_bus import EventBus

logger = logging.getLogger("orion.vision.gestos")

Ponto = tuple[float, float]  # (x, y) normalizados 0..1 (y cresce pra BAIXO)


def _dobrado(pontos: list[Ponto], ponta: int, pip: int) -> bool:
    """Dedo dobrado = ponta abaixo da junta do meio (y cresce pra baixo)."""
    return pontos[ponta][1] > pontos[pip][1]


def _esticado(pontos: list[Ponto], ponta: int, pip: int) -> bool:
    return pontos[ponta][1] < pontos[pip][1]


def eh_joinha(pontos: list[Ponto]) -> bool:
    """Polegar esticado apontando pra CIMA, os outros quatro dobrados."""
    if len(pontos) < 21:
        return False
    polegar_para_cima = (
        pontos[4][1] < pontos[3][1] < pontos[2][1]  # ponta acima das juntas
        and (pontos[2][1] - pontos[4][1]) > 0.06    # esticado de verdade
    )
    quatro_dobrados = all(
        _dobrado(pontos, ponta, pip)
        for ponta, pip in ((8, 6), (12, 10), (16, 14), (20, 18))
    )
    return polegar_para_cima and quatro_dobrados


def mao_aberta(pontos: list[Ponto]) -> bool:
    """Quatro dedos esticados - a pose base do "vem cá" (o balanço é
    decidido pelo DetectorGestos, no tempo).

    O polegar fica de fora da conta de propósito: ao chamar alguém a mão
    raramente abre o polegar por completo, e exigir isso fazia o gesto
    falhar justamente em quem chama de verdade."""
    if len(pontos) < 21:
        return False
    return all(
        _esticado(pontos, ponta, pip)
        for ponta, pip in ((8, 6), (12, 10), (16, 14), (20, 18))
    )


class DetectorGestos:
    """Decide SIM/NÃO a partir da sequência de mãos vistas.

    - SIM: joinha estável (maioria dos últimos frames) - gesto parado.
    - VEM: mão aberta + balançando (>= `balancos_minimos` trocas de direção
      com amplitude real dentro da janela). O movimento é o que separa
      "chamando" de uma mão aberta parada no quadro - sem ele, qualquer
      pessoa com a mão levantada chamaria o robô sem querer.

      Conta o balanço nos DOIS eixos e fica com o maior: chamar com a mão
      é vertical para uns e horizontal para outros, e exigir um eixo só
      deixaria metade das pessoas de fora.
    Depois de emitir, um cooldown segura repetições (criança adora repetir).
    """

    #: A janela de 1,6s do projeto original (20/07) assumia ~5 quadros/s,
    #: quando o LacoGestos tinha camera propria. Ao passar a consumir os
    #: frames do VisionCore (2026-07-28) o ritmo real caiu para ~1,65 q/s -
    #: medido: 738 quadros em 448s. Nessa cadencia a janela de 1,6s comporta
    #: 2,6 quadros, e o balanco exige 4 para ter duas trocas de direcao:
    #: virou MATEMATICAMENTE impossivel disparar. Sintoma relatado pelo
    #: usuario: \"fiz varias vezes e nada\".
    #:
    #: Com 4s o gesto disparou de ponta a ponta pela primeira vez (28/07).
    #:
    #: LICAO: estes limiares sao contados em QUADROS, entao dependem do ritmo
    #: de quem alimenta o detector. Se a fonte de frames mudar de velocidade,
    #: eles precisam ser revistos junto.
    def __init__(
        self,
        janela_s: float = 4.0,
        amplitude_minima: float = 0.03,
        balancos_minimos: int = 2,
        frames_joinha: int = 2,
        cooldown_s: float = 3.0,
    ) -> None:
        self._janela_s = janela_s
        self._amplitude_minima = amplitude_minima
        self._balancos_minimos = balancos_minimos
        self._frames_joinha = frames_joinha
        self._cooldown_s = cooldown_s
        self._historico: list[tuple[float, bool, bool, float, float]] = []
        # (quando, era_joinha, era_mao_aberta, x_da_ponta_do_medio, y_dela)
        self._ultimo_emitido = 0.0

    def alimentar(self, pontos: list[Ponto] | None, agora: float | None = None) -> str | None:
        """Um frame novo (pontos=None quando nenhuma mão no quadro).
        Devolve "sim", "vem" ou None."""
        agora = time.monotonic() if agora is None else agora

        if pontos is None:
            self._historico.append((agora, False, False, 0.0, 0.0))
        else:
            # ponta do dedo médio (12): o ponto mais central da mão aberta,
            # menos sujeito a sumir do quadro que o indicador na borda.
            self._historico.append(
                (agora, eh_joinha(pontos), mao_aberta(pontos), pontos[12][0], pontos[12][1])
            )
        corte = agora - self._janela_s
        self._historico = [h for h in self._historico if h[0] >= corte]

        if agora - self._ultimo_emitido < self._cooldown_s:
            return None

        if self._eh_sim():
            self._ultimo_emitido = agora
            self._historico.clear()
            return "sim"
        if self._eh_chamando():
            self._ultimo_emitido = agora
            self._historico.clear()
            return "vem"
        return None

    def _eh_sim(self) -> bool:
        joinhas = [h for h in self._historico if h[1]]
        return len(joinhas) >= self._frames_joinha

    def _eh_chamando(self) -> bool:
        abertas = [h for h in self._historico if h[2]]
        if len(abertas) < 4:
            return False
        # o eixo que mais balançou decide - chamar com a mão é vertical
        # para uns e horizontal para outros.
        return any(
            self._balancou([h[indice] for h in abertas]) for indice in (3, 4)
        )

    def _balancou(self, valores: list[float]) -> bool:
        """Trocas de direção com deslocamento significativo entre extremos."""
        deltas = [b - a for a, b in zip(valores, valores[1:]) if abs(b - a) > 0.005]
        if len(deltas) < 2:
            return False
        trocas = sum(1 for a, b in zip(deltas, deltas[1:]) if (a > 0) != (b > 0))
        amplitude = max(valores) - min(valores)
        return trocas >= self._balancos_minimos and amplitude >= self._amplitude_minima


class LacoGestos:
    """Lê a câmera, roda o MediaPipe e publica gesto.detectado.

    Também é a FONTE de frames da casa: `ultimo_frame_bgr` guarda o quadro
    mais novo para a CameraCompartilhada (sentinela de rostos) - assim só
    existe um leitor do dispositivo físico.
    """

    def __init__(
        self,
        event_bus: EventBus,
        captura,
        caminho_modelo: str = "data/modelos/hand_landmarker.task",
        intervalo_s: float = 0.2,
        detector: DetectorGestos | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._captura = captura
        self._caminho_modelo = caminho_modelo
        self._intervalo_s = intervalo_s
        self._detector = detector or DetectorGestos()
        self._maos = None  # HandLandmarker - criado em executar()
        self._mp = None
        self._timestamp_ms = 0
        self.ultimo_frame_bgr: Any | None = None
        self.momento_ultimo_frame = 0.0

    async def executar(self) -> None:
        # try largo de proposito: este metodo roda como task solta - uma
        # excecao aqui morreria MUDA (foi exatamente o que aconteceu com a
        # API antiga `mp.solutions`, removida no mediapipe 0.10.35: o
        # AttributeError sumiu e so a sentinela reclamou de falta de frame).
        try:
            await self._executar()
        except Exception:
            logger.exception("Gestos desativados: erro no laço")

    async def preparar(self) -> bool:
        """Carrega o MediaPipe e o modelo, SEM abrir câmera.

        Separado de `_executar` em 2026-07-28 para o laço poder ser
        alimentado por quem já é dono da câmera (o `ModuloVisao`, EDR-0023),
        em vez de disputar o dispositivo. Devolve False se faltar alguma
        peça - gesto é acessório, nunca derruba a visão nem a conversa.
        """
        try:
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision as mp_vision
        except ImportError:
            logger.warning("Gestos desativados: mediapipe/cv2 não instalados")
            return False
        self._mp = mp

        # API nova (Tasks, mediapipe >= 0.10.x): precisa do arquivo de
        # modelo baixado (data/modelos/hand_landmarker.task, ~7,8 MB - ver
        # docs/journal.md 2026-07-20 para a URL oficial).
        try:
            opcoes = mp_vision.HandLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=self._caminho_modelo),
                running_mode=mp_vision.RunningMode.VIDEO,
                num_hands=1,
                min_hand_detection_confidence=0.6,
                min_tracking_confidence=0.5,
            )
            self._maos = await asyncio.to_thread(
                mp_vision.HandLandmarker.create_from_options, opcoes
            )
        except Exception:
            logger.exception(
                "Gestos desativados: falha ao carregar o modelo %s", self._caminho_modelo
            )
            return False
        logger.info("Gestos ativos: joinha = SIM, mão aberta balançando = VEM CÁ")
        return True

    async def processar_frame(self, frame_bgr) -> None:
        """Um quadro vindo de FORA (do dono da câmera).

        Respeita o `intervalo_s` por conta própria: o laço da visão roda bem
        mais rápido que os ~5 fps que o gesto precisa, e rodar o MediaPipe em
        todo quadro da visão só queimaria CPU do Notebook.

        Nunca deixa exceção subir: quem chama é o laço da visão, e derrubar a
        visão por causa de um gesto seria trocar o essencial pelo acessório.
        """
        if self._maos is None:
            return
        agora = time.monotonic()
        if agora - self.momento_ultimo_frame < self._intervalo_s:
            return
        try:
            import cv2

            await self._analisar(cv2, frame_bgr)
        except Exception:
            logger.debug("falha ao processar frame de gesto", exc_info=True)

    async def _executar(self) -> None:
        try:
            import cv2
        except ImportError:
            logger.warning("Gestos desativados: cv2 não instalado")
            return
        if not await self.preparar():
            return

        try:
            await self._captura.abrir()
        except Exception:
            logger.warning("Gestos desativados: câmera indisponível", exc_info=True)
            return

        try:
            while True:
                inicio = time.monotonic()
                await self._um_frame(cv2)
                # mantém o passo mesmo se a inferência demorar
                espera = self._intervalo_s - (time.monotonic() - inicio)
                await asyncio.sleep(max(0.02, espera))
        finally:
            self._maos.close()
            await self._captura.fechar()

    async def _um_frame(self, cv2) -> None:
        try:
            frame_bgr = await self._captura.ler_frame()
        except Exception:
            logger.debug("falha ao ler frame (gestos)", exc_info=True)
            await asyncio.sleep(1.0)
            return
        await self._analisar(cv2, frame_bgr)

    async def _analisar(self, cv2, frame_bgr) -> None:
        self.ultimo_frame_bgr = frame_bgr
        self.momento_ultimo_frame = time.monotonic()

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        imagem = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        # detect_for_video exige timestamps crescentes em ms
        self._timestamp_ms += max(1, int(self._intervalo_s * 1000))
        resultado = await asyncio.to_thread(
            self._maos.detect_for_video, imagem, self._timestamp_ms
        )

        pontos = None
        if resultado.hand_landmarks:
            pontos = [(m.x, m.y) for m in resultado.hand_landmarks[0]]

        gesto = self._detector.alimentar(pontos)
        if gesto is not None:
            # ONDE a mão estava importa tanto quanto o gesto: sem isso o robô
            # sabe que foi chamado e não sabe para que lado virar. Ponto 9
            # (base do dedo médio) = centro da palma, o mais estável da mão.
            # Mesma convenção do `vision.person_recognized`: 0..1 no quadro,
            # 0.5 é o centro.
            centro_x = pontos[9][0] if pontos else 0.5
            logger.info("GESTO detectado: %s (centro_x=%.2f)", gesto.upper(), centro_x)
            await self._event_bus.publish(
                "gesto.detectado", {"tipo": gesto, "centro_x": centro_x}
            )


class CameraCompartilhada:
    """Adaptador com a mesma cara da CapturaCamera, servindo o frame do
    cache do LacoGestos - a sentinela de rostos usa isto no lugar da câmera
    real (dois VideoCapture no mesmo dispositivo brigam)."""

    def __init__(self, laco: LacoGestos, validade_s: float = 3.0) -> None:
        self._laco = laco
        self._validade_s = validade_s

    @property
    def aberta(self) -> bool:
        return self._laco.ultimo_frame_bgr is not None

    async def abrir(self) -> None:
        # a câmera real é aberta pelo LacoGestos; aqui só esperamos o
        # primeiro frame chegar (ou desistimos - sentinela tolera).
        for _ in range(50):
            if self._laco.ultimo_frame_bgr is not None:
                return
            await asyncio.sleep(0.2)
        raise RuntimeError("LacoGestos não produziu nenhum frame em 10s")

    async def ler_frame(self) -> Any:
        frame = self._laco.ultimo_frame_bgr
        idade = time.monotonic() - self._laco.momento_ultimo_frame
        if frame is None or idade > self._validade_s:
            raise RuntimeError(f"sem frame fresco (idade {idade:.1f}s)")
        return frame

    async def fechar(self) -> None:
        pass  # a câmera real pertence ao LacoGestos
