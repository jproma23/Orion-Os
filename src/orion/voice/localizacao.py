"""De onde veio a voz: direcao de chegada por diferenca de tempo (GCC-PHAT).

Objetivo do usuario (2026-07-28): *"um mic de cada lado e ele sabe de onde
o audio esta vindo e segue a voz quando chamado"*. Este modulo e a conta
que transforma dois canais de audio numa direcao; quem gira a cabeca e a
camada de cima.

POR QUE TEMPO E NAO VOLUME
--------------------------
A tentacao e comparar o nivel (RMS) dos dois microfones e dizer "esta mais
alto na esquerda, entao veio da esquerda". Nao funciona de forma confiavel:
o nivel muda com a distancia da pessoa, com o volume da voz dela e com o
eco da sala. Ja a diferenca de TEMPO de chegada depende so da geometria -
o som anda a 343 m/s e ponto. Por isso aqui se mede atraso.

A ARITMETICA QUE LIMITA TUDO (medida, nao estimada)
---------------------------------------------------
A 16000 Hz, uma amostra dura 62,5 us, e nesse tempo o som anda **2,14 cm**.
Com os microfones separados por D, o atraso maximo possivel (som vindo
exatamente de um dos lados) e D/343 segundos:

    D = 15 cm -> 437 us -> 7,0 amostras
    D = 20 cm -> 583 us -> 9,3 amostras
    D = 30 cm -> 875 us -> 14,0 amostras

Ou seja: TODA a faixa de -90 a +90 graus cabe em ~9 amostras com 20 cm de
separacao. Por isso o pico da correlacao e interpolado (parabola nos tres
pontos ao redor do maximo) - sem isso a resolucao seria grosseira demais
para virar a cabeca com jeito. **Quanto mais afastados os microfones,
melhor**: a precisao angular e proporcional a D.

LIMITACOES CONSCIENTES (nao sao bugs)
--------------------------------------
1. **Frente e tras nao se distinguem.** Dois microfones em linha dao o
   MESMO atraso para uma voz a +30 graus a frente e a +30 graus atras. E
   geometria, nao falha de codigo. Quem resolve e o robo: gira um pouco e
   mede de novo - se o angulo mudou no sentido esperado, era frente.
2. **Eco piora tudo.** Numa sala reverberante o pico da correlacao compete
   com as reflexoes. O PHAT (branqueamento do espectro) ja e a defesa
   padrao contra isso, e e por isso que ele esta aqui em vez de uma
   correlacao cruzada simples.
3. **Precisao realista: 10 a 15 graus** em sala silenciosa. Esquerda /
   centro / direita e confiavel; angulo fino, nao.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger("orion.voice.localizacao")

#: Velocidade do som no ar a ~20 C, em m/s.
VELOCIDADE_SOM_M_S = 343.0

#: Confianca minima para a direcao valer alguma coisa. O valor e a "altura"
#: do pico da correlacao em relacao ao resto dela: com ruido difuso (sem
#: uma fonte clara) a correlacao fica achatada e esta razao cai para perto
#: de 1. Abaixo deste limiar devolvemos None em vez de um angulo inventado.
CONFIANCA_MINIMA = 3.0


@dataclass(frozen=True)
class DirecaoEstimada:
    """Resultado de uma estimativa de direcao.

    `angulo_graus`: 0 = bem a frente (ou bem atras - ver limitacao 1 no
    topo do modulo), negativo = lado do microfone ESQUERDO, positivo =
    lado do DIREITO.
    """

    angulo_graus: float
    atraso_amostras: float
    confianca: float

    @property
    def lado(self) -> str:
        """Rotulo grosseiro, que e o que de fato da para afirmar."""
        if self.angulo_graus < -15.0:
            return "esquerda"
        if self.angulo_graus > 15.0:
            return "direita"
        return "centro"


class LocalizadorDirecao:
    """Estima a direcao da fonte sonora a partir de dois canais.

    `separacao_m` e a distancia FISICA entre os dois microfones - ela nao e
    um detalhe de ajuste, e o que converte atraso em angulo. Medir com fita
    metrica e colocar o numero certo aqui; errar isso desloca todos os
    angulos.
    """

    def __init__(
        self,
        taxa_amostragem: int = 16000,
        separacao_m: float = 0.20,
        confianca_minima: float = CONFIANCA_MINIMA,
    ) -> None:
        if separacao_m <= 0:
            raise ValueError("separacao entre microfones precisa ser positiva")
        self._taxa = taxa_amostragem
        self._separacao_m = separacao_m
        self._confianca_minima = confianca_minima
        # Atraso maximo FISICAMENTE possivel, em amostras. Serve de coleira:
        # qualquer pico fora desta janela e eco ou ruido, nunca a fonte.
        self._atraso_max = (separacao_m / VELOCIDADE_SOM_M_S) * taxa_amostragem

    @property
    def atraso_maximo_amostras(self) -> float:
        return self._atraso_max

    def estimar(self, esquerda: np.ndarray, direita: np.ndarray) -> DirecaoEstimada | None:
        """Devolve a direcao, ou None quando nao da para afirmar nada.

        Convencao do sinal, MEDIDA pelos testes (nao deduzida - a primeira
        versao deste arquivo tinha o sinal invertido e so o teste pegou):
        quando o canal direito chega DEPOIS do esquerdo, o som veio primeiro
        pela esquerda, o `atraso_amostras` sai NEGATIVO e o angulo sai
        negativo. Negativo = lado esquerdo, positivo = lado direito.

        Isto e exatamente o tipo de coisa que se inverte sem ninguem
        perceber: o robo so viraria a cabeca para o lado errado e pareceria
        que "o algoritmo nao funciona". Por isso ha um teste espelhado para
        cada lado.
        """
        esquerda = np.asarray(esquerda, dtype=np.float64).flatten()
        direita = np.asarray(direita, dtype=np.float64).flatten()
        if esquerda.size != direita.size or esquerda.size < 32:
            return None
        # Silencio nao tem direcao. Sem isso, ruido de fundo produziria um
        # angulo aleatorio a cada bloco e a cabeca ficaria tremendo.
        if not np.any(esquerda) or not np.any(direita):
            return None

        atraso, confianca = self._atraso_gcc_phat(esquerda, direita)
        if atraso is None or confianca < self._confianca_minima:
            return None

        # atraso (amostras) -> segundos -> diferenca de caminho -> seno do
        # angulo. O clip existe porque eco pode empurrar o pico um pouco
        # alem do limite fisico; ai a resposta certa e "totalmente para o
        # lado", nao um arcsin invalido.
        segundos = atraso / self._taxa
        seno = (segundos * VELOCIDADE_SOM_M_S) / self._separacao_m
        seno = float(np.clip(seno, -1.0, 1.0))
        angulo = float(np.degrees(np.arcsin(seno)))
        return DirecaoEstimada(angulo_graus=angulo, atraso_amostras=float(atraso),
                               confianca=float(confianca))

    def _atraso_gcc_phat(self, x: np.ndarray, y: np.ndarray):
        """Correlacao cruzada generalizada com pesagem PHAT.

        PHAT = dividir o espectro cruzado pelo seu proprio modulo, ficando
        so com a FASE. E o que torna a estimativa robusta a eco e a timbres
        diferentes entre os dois microfones (que nunca sao identicos).
        """
        n = 1
        while n < x.size + y.size:
            n *= 2  # potencia de 2: FFT mais rapida e sem wrap-around

        X = np.fft.rfft(x, n=n)
        Y = np.fft.rfft(y, n=n)
        espectro = X * np.conj(Y)
        modulo = np.abs(espectro)
        # piso para nao dividir por zero em bins sem energia
        modulo[modulo < 1e-12] = 1e-12
        correlacao = np.fft.irfft(espectro / modulo, n=n)

        # so a janela fisicamente possivel de atrasos
        limite = int(np.ceil(self._atraso_max)) + 1
        if limite * 2 + 1 > n:
            return None, 0.0
        janela = np.concatenate((correlacao[-limite:], correlacao[: limite + 1]))

        indice = int(np.argmax(janela))
        pico = float(janela[indice])
        if pico <= 0:
            return None, 0.0

        # Confianca: quao destacado o pico esta do resto da janela. Pico
        # afiado = fonte clara; correlacao achatada = ruido sem direcao.
        media = float(np.mean(np.abs(janela)))
        confianca = pico / media if media > 0 else 0.0

        # Interpolacao parabolica: com so ~9 amostras cobrindo 180 graus,
        # o inteiro sozinho seria grosseiro demais.
        deslocamento = float(indice - limite)
        if 0 < indice < janela.size - 1:
            a, b, c = janela[indice - 1], janela[indice], janela[indice + 1]
            denominador = a - 2.0 * b + c
            if denominador != 0:
                ajuste = 0.5 * (a - c) / denominador
                if -1.0 < ajuste < 1.0:
                    deslocamento += ajuste

        deslocamento = float(np.clip(deslocamento, -self._atraso_max, self._atraso_max))
        return deslocamento, confianca
