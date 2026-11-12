"""O rosto leve: DOIS OLHOS e UMA BOCA numa tela preta (2026-11-12).

POR QUE EXISTE. O dono: *"a tela kiosk consome bastante RAM, poderiamos usar
outra solucao"* - *"usariamos apenas uma tela preta e dois olhos emotivos"* -
*"uma boca que abre e fecha ao falar"* - *"tudo bem desenhado"*. O avatar do
Notebook e um Firefox em modo kiosk: o maior consumidor da maquina (medido em
31/10: ~50% de um nucleo, mais que o proprio robo) e centenas de MB de RAM
para desenhar um rosto. Este rosto e um processo `pygame` de dezenas de MB.

ESTE ARQUIVO SO DECIDE, NAO DESENHA. Recebe os eventos que o robo ja publica
(os mesmos que o avatar recebe por SSE em :8090/eventos) e diz, a cada quadro,
como o rosto esta: expressao, abertura dos olhos (piscar), para onde olham e
quanto a boca abre. Separado do desenho de proposito: e testavel sem tela, e
o desenho (`tools/rosto.py`) fica burro.

De onde vem cada coisa:

- `voice.status`: LISTENING/WAKE_DETECTED = ouvindo; TRANSCRIBING/THINKING =
  pensando; SPEAKING = falando; ERROR = triste; IDLE = neutro.
- `voice.fala_envelope`: o VOLUME REAL da fala a cada `passo_s` (40 ms),
  calculado do audio que vai tocar (`sintese.envelope_da_fala`). A boca segue
  isso - abre nas silabas, fecha nas pausas -, e nao um movimento generico.
- `voice.saudacao`, `vision.person_recognized`: feliz por alguns segundos.
- `vision.person_detected`/`person_recognized`: os olhos olham para a pessoa
  (centro da caixa na imagem).
- `motion.obstacle_front`: susto por um instante.
- Sem evento nenhum por muito tempo: sonolento.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

NEUTRO = "neutro"
OUVINDO = "ouvindo"
PENSANDO = "pensando"
FALANDO = "falando"
FELIZ = "feliz"
SUSTO = "susto"
TRISTE = "triste"
SONOLENTO = "sonolento"

#: Estado da voz -> expressao de fundo.
EXPRESSAO_DA_VOZ = {
    "IDLE": NEUTRO,
    "LISTENING": OUVINDO,
    "WAKE_DETECTED": OUVINDO,
    "TRANSCRIBING": PENSANDO,
    "THINKING": PENSANDO,
    "SPEAKING": FALANDO,
    "ERROR": TRISTE,
}

#: Quanto dura cada expressao passageira (s).
DURACAO_FELIZ_S = 3.0
DURACAO_SUSTO_S = 1.2
#: Sem nenhum evento por isto, ele fica sonolento.
OCIOSO_PARA_SONOLENTO_S = 180.0
#: Piscar: intervalo aleatorio entre estes, e duracao de uma piscada.
PISCAR_MIN_S = 2.5
PISCAR_MAX_S = 6.0
DURACAO_PISCADA_S = 0.16
#: O olhar perde a pessoa depois disto sem ve-la de novo.
OLHAR_VALIDO_S = 1.5
#: Largura do quadro da camera (o centro da caixa vem em pixels).
LARGURA_QUADRO_CAMERA = 640.0


@dataclass(frozen=True)
class Rosto:
    """Como o rosto esta AGORA - tudo normalizado, o desenho escala."""

    expressao: str
    #: 1 = olho aberto, 0 = fechado (piscada, sonolento).
    abertura_olhos: float
    #: Para onde olham, de -1 (esquerda/cima) a +1 (direita/baixo).
    olhar_x: float
    olhar_y: float
    #: 0 = boca fechada (traco), 1 = toda aberta.
    abertura_boca: float


class EstadoRosto:
    """Junta os eventos e devolve o `Rosto` de cada quadro."""

    def __init__(self, agora: float, sorteio: random.Random | None = None) -> None:
        self._sorteio = sorteio or random.Random()
        self._expressao_voz = NEUTRO
        self._passageira: tuple[str, float] | None = None  # (expressao, ate)
        self._ultimo_evento = agora
        self._proxima_piscada = agora + self._intervalo_piscar()
        self._piscando_desde: float | None = None
        self._olhar_alvo: tuple[float, float] = (0.0, 0.0)
        self._olhar_visto_em = float("-inf")
        self._olhar = [0.0, 0.0]
        self._envelope: list[float] = []
        self._envelope_passo = 0.04
        self._envelope_inicio = float("-inf")
        self._boca = 0.0
        self._ultimo_quadro = agora

    def _intervalo_piscar(self) -> float:
        return self._sorteio.uniform(PISCAR_MIN_S, PISCAR_MAX_S)

    # ------------------------------------------------------------ eventos

    def ao_evento(self, topico: str, dados: dict, agora: float) -> None:
        self._ultimo_evento = agora
        dados = dados if isinstance(dados, dict) else {}
        if topico == "voice.status":
            self._expressao_voz = EXPRESSAO_DA_VOZ.get(str(dados.get("estado")), NEUTRO)
            if self._expressao_voz != FALANDO:
                self._envelope = []
        elif topico == "voice.fala_envelope":
            niveis = dados.get("niveis") or []
            self._envelope = [max(0.0, min(1.0, float(n))) for n in niveis]
            self._envelope_passo = float(dados.get("passo_s") or 0.04)
            self._envelope_inicio = agora
        elif topico in ("voice.saudacao", "vision.person_recognized"):
            self._passageira = (FELIZ, agora + DURACAO_FELIZ_S)
            self._olhar_para(dados, agora)
        elif topico == "vision.person_detected":
            self._olhar_para(dados, agora)
        elif topico == "motion.obstacle_front":
            self._passageira = (SUSTO, agora + DURACAO_SUSTO_S)

    def _olhar_para(self, dados: dict, agora: float) -> None:
        caixa = dados.get("caixa")
        if isinstance(caixa, (list, tuple)) and len(caixa) == 4:
            centro = (float(caixa[0]) + float(caixa[2])) / 2.0 / LARGURA_QUADRO_CAMERA
        elif dados.get("centro_x") is not None:
            centro = float(dados["centro_x"])
        else:
            return
        self._olhar_alvo = (max(-1.0, min(1.0, (centro - 0.5) * 2.0)), 0.0)
        self._olhar_visto_em = agora

    # ------------------------------------------------------------- quadro

    def quadro(self, agora: float) -> Rosto:
        dt = max(0.0, min(0.2, agora - self._ultimo_quadro))
        self._ultimo_quadro = agora
        expressao = self._expressao(agora)
        alvo_x, alvo_y = self._alvo_olhar(expressao, agora)
        return Rosto(
            expressao=expressao,
            abertura_olhos=self._abertura_olhos(expressao, agora),
            olhar_x=self._suavizar_olhar(0, alvo_x, dt),
            olhar_y=self._suavizar_olhar(1, alvo_y, dt),
            abertura_boca=self._abertura_boca(expressao, agora, dt),
        )

    def _expressao(self, agora: float) -> str:
        if self._passageira is not None:
            expressao, ate = self._passageira
            if agora < ate and self._expressao_voz != FALANDO:
                return expressao
            if agora >= ate:
                self._passageira = None
        if self._expressao_voz == NEUTRO and agora - self._ultimo_evento > OCIOSO_PARA_SONOLENTO_S:
            return SONOLENTO
        return self._expressao_voz

    def _abertura_olhos(self, expressao: str, agora: float) -> float:
        base = {SONOLENTO: 0.25, SUSTO: 1.0, PENSANDO: 0.85}.get(expressao, 1.0)
        if self._piscando_desde is None and agora >= self._proxima_piscada:
            self._piscando_desde = agora
        if self._piscando_desde is not None:
            fase = (agora - self._piscando_desde) / DURACAO_PISCADA_S
            if fase >= 1.0:
                self._piscando_desde = None
                self._proxima_piscada = agora + self._intervalo_piscar()
            else:
                # fecha e abre: 1 -> 0 -> 1 numa meia senoide
                return base * (1.0 - math.sin(math.pi * fase))
        return base

    def _alvo_olhar(self, expressao: str, agora: float) -> tuple[float, float]:
        if expressao == PENSANDO:
            return (0.35, -0.55)  # olha para cima, de lado: "deixa eu pensar"
        if agora - self._olhar_visto_em <= OLHAR_VALIDO_S:
            return self._olhar_alvo
        return (0.0, 0.0)

    def _suavizar_olhar(self, eixo: int, alvo: float, dt: float) -> float:
        # olho nao teleporta: aproxima ~90% do caminho em 0,25 s
        self._olhar[eixo] += (alvo - self._olhar[eixo]) * min(1.0, dt * 9.0)
        return self._olhar[eixo]

    def _abertura_boca(self, expressao: str, agora: float, dt: float) -> float:
        alvo = 0.0
        if expressao == FALANDO and self._envelope:
            indice = int((agora - self._envelope_inicio) / self._envelope_passo)
            if 0 <= indice < len(self._envelope):
                alvo = self._envelope[indice]
        # abre rapido, fecha um pouco mais devagar - parece fala, nao pisca
        rapidez = 30.0 if alvo > self._boca else 18.0
        self._boca += (alvo - self._boca) * min(1.0, dt * rapidez)
        return max(0.0, min(1.0, self._boca))
