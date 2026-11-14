"""Obstaculo no CHAO pela camera: o que o LiDAR, a 45 cm, nao ve.

Pedido do dono (2026-11-14): *"a ia precisa receber frame quando o robo anda
para ver se vai bater em algo no chao pq o lidar esta em cima"*, *"usando o
ptz da camera vamos mandar frame para ia quando ver obstaculos se
aproximando em sua direcao"*.

DUAS PECAS, de custo muito diferente:

1. `DetectorChao` - LOCAL, em todo quadro (~5 ms, OpenCV puro). Compara cada
   ponto da imagem com o piso logo a frente da roda (faixa de baixo, centro)
   em saturacao e brilho. O que difere e e GROSSO vira candidato. Calibrado
   nas fotos reais da casa em 14/11 (piso branco brilhante, rejunte marrom):
   - reflexo: mais CLARO que o piso -> nao conta;
   - rejunte e migalha: finos -> somem na abertura morfologica, e o rejunte
     perto da camera (que engrossa a ~12 px) cai no filtro de espessura;
   - pano roxo e tapete enrolado (80+ px de espessura): acusados.

2. `VigiaChao` - so quando o detector acusa algo PERTO (base na parte de baixo
   da imagem = perto da roda, com a cabeca a -25 graus) e por dois quadros
   seguidos na mesma direcao: manda ESTE quadro a IA, que diz o que e e
   decide PARAR ou SEGUIR. Uma chamada por obstaculo, nunca por quadro (2
   quadros/s para a IA seriam 172.800 chamadas/dia contra 500 da cota).

Quem decide e a IA (decisao do dono, 2026-07-30: "sensores medem e
informam"). Sem resposta da IA o robo NAO para sozinho - fica so o aviso no
log e na webui.

So roda com a varredura ao andar ligada pelo Pi (`vision.varredura_chao`):
com a cabeca olhando para frente, "parte de baixo da imagem" nao e chao.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

logger = logging.getLogger("orion.vision.obstaculo_chao")

#: Faixa de referencia do piso: o que esta logo a frente da roda.
REF_ALTURA = 0.15
REF_LARGURA = (0.30, 0.70)
#: Diferenca que separa "outra coisa" do piso. Saturacao: o piso branco tem
#: S~10; o rejunte ~60, o pano roxo ~120. Brilho: so conta o mais ESCURO
#: (reflexo de lampada e mais claro e nao e obstaculo).
DELTA_SATURACAO = 35
DELTA_BRILHO = 55
NUCLEO_LIMPEZA_PX = 11
#: Menos que isto da imagem e migalha.
AREA_MINIMA = 0.004
#: Espessura media (area / maior lado). Rejunte perto da camera ~12 px.
ESPESSURA_MINIMA_PX = 25
#: Corredor do robo no quadro (centro) e "perto" (base abaixo disto).
CORREDOR = (0.20, 0.80)
PERTO = 0.72
MUITO_PERTO = 0.92
#: Referencia que mudou tanto assim do piso conhecido = ha algo EM CIMA dela.
DESVIO_REF_SAT = 30
DESVIO_REF_BRILHO = 50


@dataclass(frozen=True)
class Achado:
    """Algo que nao e piso. `base` = 0 (topo, longe) a 1 (embaixo, perto)."""

    x: float
    largura: float
    base: float
    area: float
    no_corredor: bool


class DetectorChao:
    def __init__(self) -> None:
        #: Piso conhecido (S, V medianos). Aprende com quadros em que a
        #: referencia e homogenea; e o que salva quando o obstaculo cobre a
        #: propria faixa de referencia (muito perto).
        self._piso: tuple[float, float] | None = None
        self._nucleo = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (NUCLEO_LIMPEZA_PX, NUCLEO_LIMPEZA_PX)
        )

    def analisar(self, frame: np.ndarray) -> list[Achado]:
        h, w = frame.shape[:2]
        hsv = cv2.cvtColor(cv2.GaussianBlur(frame, (5, 5), 0), cv2.COLOR_BGR2HSV)
        ref = hsv[int((1 - REF_ALTURA) * h):, int(REF_LARGURA[0] * w):int(REF_LARGURA[1] * w)]
        s_ref, v_ref = float(np.median(ref[..., 1])), float(np.median(ref[..., 2]))
        homogenea = float(np.std(ref[..., 1])) < 15 and float(np.std(ref[..., 2])) < 30

        achados: list[Achado] = []
        if self._piso is not None:
            s_piso, v_piso = self._piso
            if abs(s_ref - s_piso) > DESVIO_REF_SAT or v_piso - v_ref > DESVIO_REF_BRILHO:
                # A faixa logo a frente da roda NAO e o piso: algo esta ali.
                achados.append(Achado(0.5, REF_LARGURA[1] - REF_LARGURA[0], 1.0, REF_ALTURA, True))
                s_ref, v_ref = s_piso, v_piso
            elif homogenea:
                self._piso = (0.8 * s_piso + 0.2 * s_ref, 0.8 * v_piso + 0.2 * v_ref)
        elif homogenea:
            self._piso = (s_ref, v_ref)

        s = hsv[..., 1].astype(np.int16)
        v = hsv[..., 2].astype(np.int16)
        diferente = (np.abs(s - s_ref) > DELTA_SATURACAO) | ((v_ref - v) > DELTA_BRILHO)
        mascara = diferente.astype(np.uint8) * 255
        mascara = cv2.morphologyEx(mascara, cv2.MORPH_OPEN, self._nucleo)
        mascara = cv2.morphologyEx(mascara, cv2.MORPH_CLOSE, self._nucleo)

        n, _, stats, _ = cv2.connectedComponentsWithStats(mascara)
        for i in range(1, n):
            x, y, bw, bh, area = (int(valor) for valor in stats[i])
            if area < AREA_MINIMA * h * w:
                continue
            if area / max(bw, bh) < ESPESSURA_MINIMA_PX:
                continue
            centro = (x + bw / 2) / w
            achados.append(Achado(
                x=centro,
                largura=bw / w,
                base=(y + bh) / h,
                area=area / (h * w),
                no_corredor=x / w < CORREDOR[1] and (x + bw) / w > CORREDOR[0],
            ))
        return achados


#: Pergunta a IA. Curta: a resposta e lida por codigo (1a palavra).
PROMPT_CHAO = (
    "Voce e a visao de um robo de rodas pequeno (39 cm de largura, rodas de "
    "6 cm). O sensor de distancia dele fica a 45 cm do chao e NAO ve nada "
    "baixo. A foto e da camera olhando o CHAO a frente enquanto ele anda. Um "
    "detector achou algo diferente do piso chegando perto, {onde}.\n"
    "Responda em UMA linha: PARAR ou SEGUIR, depois ' - ' e o que e, em ate "
    "12 palavras.\n"
    "PARAR: o que a roda nao passa por cima, ou que pode se enroscar, quebrar "
    "ou machucar (fio, brinquedo, sapato, pano, tapete enrolado, pe de movel, "
    "degrau, animal, pessoa).\n"
    "SEGUIR: so se for piso, sombra, reflexo, rejunte, mancha ou tapete plano."
)

#: Entre duas perguntas a IA. Obstaculo ainda a frente depois disto conta de
#: novo - o robo pode ter seguido e ele continuar la.
INTERVALO_ENTRE_PERGUNTAS_S = 8.0
#: Dois quadros com algo perto, dentro disto, na mesma direcao da cabeca.
JANELA_CONFIRMACAO_S = 3.0

PerguntarIA = Callable[[str, bytes], Awaitable[str]]
ComandarNavegacao = Callable[[dict], Awaitable[None]]
Publicar = Callable[[str, dict], Awaitable[None]]


class VigiaChao:
    """Liga o detector a cabeca, a IA e a navegacao do Pi."""

    def __init__(
        self,
        perguntar_ia: PerguntarIA,
        comandar_navegacao: ComandarNavegacao,
        publicar_no_pi: Publicar | None = None,
        detector: DetectorChao | None = None,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        self._perguntar_ia = perguntar_ia
        self._comandar_navegacao = comandar_navegacao
        self._publicar = publicar_no_pi
        self._detector = detector or DetectorChao()
        self._relogio = relogio
        self.ativo = False
        self._pan = 0.0
        self._pan_mudou_em = float("-inf")
        self._perto_em: dict[float, float] = {}
        self._perguntou_em = float("-inf")
        self._ocupado = False
        self.tarefa: asyncio.Task | None = None
        #: O ultimo veredito, para log/depuracao.
        self.ultimo: dict[str, Any] | None = None

    # ---- eventos do Pi ----
    async def ao_varredura(self, evento) -> None:
        ativo = bool(evento.dados.get("ativo"))
        if ativo != self.ativo:
            logger.info("obstaculo no chao: vigia %s", "LIGADA" if ativo else "desligada")
        self.ativo = ativo
        self._perto_em.clear()

    async def ao_mover_cabeca(self, evento) -> None:
        try:
            pan = round(float(evento.dados.get("pan", 0.0)))
        except (TypeError, ValueError):
            return
        if pan != self._pan:
            self._pan = pan
            self._pan_mudou_em = self._relogio()

    # ---- cada quadro ----
    async def processar_frame(self, frame) -> None:
        if not self.ativo or self._ocupado or frame is None:
            return
        agora = self._relogio()
        # Quadro tirado com o servo ainda girando sai borrado e com o chao
        # "andando": espera a cabeca assentar.
        if agora - self._pan_mudou_em < 0.4:
            return
        perto = [
            a for a in self._detector.analisar(frame)
            if a.no_corredor and a.base >= PERTO
        ]
        if not perto:
            self._perto_em.pop(self._pan, None)
            return
        mais_perto = max(perto, key=lambda a: a.base)
        anterior = self._perto_em.get(self._pan)
        self._perto_em[self._pan] = agora
        confirmado = mais_perto.base >= MUITO_PERTO or (
            anterior is not None and agora - anterior <= JANELA_CONFIRMACAO_S
        )
        if not confirmado or agora - self._perguntou_em < INTERVALO_ENTRE_PERGUNTAS_S:
            return
        self._perguntou_em = agora
        self._ocupado = True
        # NUMA TAREFA: o laco da visao espera cada observador terminar, e a
        # IA leva 2-4 s - a camera inteira congelaria esperando a resposta.
        self.tarefa = asyncio.create_task(self._decidir_e_liberar(frame.copy(), mais_perto))

    async def _decidir_e_liberar(self, frame, achado: Achado) -> None:
        try:
            await self._decidir(frame, achado)
        finally:
            self._ocupado = False

    async def _decidir(self, frame, achado: Achado) -> None:
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            return
        lado = "a esquerda" if achado.x < 0.4 else "a direita" if achado.x > 0.6 else "no meio"
        onde = f"{lado} da imagem, cabeca virada {self._pan:+.0f} graus"
        logger.info(
            "obstaculo no chao %s (base %.2f, area %.1f%%) - perguntando a IA",
            onde, achado.base, achado.area * 100,
        )
        try:
            resposta = (await self._perguntar_ia(PROMPT_CHAO.format(onde=onde), jpeg.tobytes()))
        except Exception:
            logger.exception("obstaculo no chao: a IA nao respondeu")
            resposta = ""
        decisao, descricao = _ler_resposta(resposta)
        self.ultimo = {
            "decisao": decisao,
            "descricao": descricao,
            "pan": self._pan,
            "base": round(achado.base, 2),
            "quando": time.strftime("%H:%M:%S"),
        }
        logger.info("obstaculo no chao: IA decidiu %s - %s", decisao, descricao)
        if decisao == "parar":
            await self._comandar_navegacao({"acao": "HOLD", "origem": "visao_chao"})
        if self._publicar is not None:
            try:
                await self._publicar("vision.obstaculo_chao", dict(self.ultimo))
            except Exception:
                logger.debug("obstaculo no chao: aviso ao Pi nao chegou", exc_info=True)


def _ler_resposta(texto: str) -> tuple[str, str]:
    """'PARAR - tapete enrolado' -> ('parar', 'tapete enrolado').

    O que nao comeca com PARAR/SEGUIR (IA fora, cota, resposta torta) vira
    'sem_decisao': o robo nao para por conta propria - decisao do dono.
    """
    limpo = (texto or "").strip().lstrip("*#- ").strip()
    palavra = limpo.split(maxsplit=1)[0].strip(".:,-*").upper() if limpo else ""
    resto = limpo.split("-", 1)[1].strip() if "-" in limpo else limpo[len(palavra):].strip()
    if palavra == "PARAR":
        return "parar", resto
    if palavra == "SEGUIR":
        return "seguir", resto
    return "sem_decisao", limpo[:120]
