"""O CORPO AGORA: o que o LiDAR ve em volta e o que o ultimo movimento fez.

Pedidos do dono em 2026-10-31: *"o robo precisa entender seu corpo e seus
comandos"*, *"usando os dados do sensor lidar para usar o mapa"* e *"onde o
movimento pode ser conferido"*.

## Por que existe

Ate aqui o modelo decidia `#acao:andar_para_frente` sem saber se havia uma
parede a 20 cm, e depois de mandar a acao ninguem conferia se o robo andou -
o firmware recebia o comando, o ACK voltava, e "Indo para frente" era dito
com as rodas no ar ou travadas do mesmo jeito. E o defeito da odometria
mentirosa (a pose vinha de passos COMANDADOS) repetido na conversa.

Duas coisas, entao:

1. `texto()` - o mapa em volta (frente, direita, tras, esquerda) e a bussola,
   para entrar no prompt junto do catalogo de acoes;
2. `conferir()` - mede com o LiDAR ANTES e DEPOIS de um movimento e guarda o
   resultado. Na conversa seguinte o robo sabe o que fez de verdade: "pedi
   30 cm, o LiDAR mediu 27" ou "nao sai do lugar".

## De onde vem

Do `/estado` da webui do Motion Core (mesmo Pi, 127.0.0.1). E o mesmo JSON
que a tela do mapa ja usa - uma fonte so. HTTP local e nao o enlace TCP
porque o Mission Core nao assina a telemetria, e assinar so para isto seria
um segundo caminho para o mesmo dado.

Convencao de angulo do LiDAR (a do `estado_do_mundo.py` e do `mapa.js`):
0 graus = esquerda, 90 = FRENTE, 180 = direita, 270 = tras.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from typing import Any

from orion.kernel.tarefas import disparar

logger = logging.getLogger("orion.mission.corpo_agora")

URL_ESTADO_PADRAO = "http://127.0.0.1:8080/estado"

#: Centro de cada lado, na convencao do LiDAR (ver docstring do modulo).
LADOS = {"frente": 90.0, "direita": 180.0, "tras": 270.0, "esquerda": 0.0}
#: Meia-janela de cada lado: a menor distancia em +-30 graus do centro.
_MEIA_JANELA = 30.0

#: Abaixo disto, a diferenca do LiDAR e ruido de leitura, nao movimento.
_RUIDO_CM = 4.0
#: O resultado do ultimo movimento so vale como "o que acabei de fazer" por
#: este tempo; depois disso ele vira passado e sai do prompt.
_VALIDADE_ULTIMO_S = 600.0
#: Teto da espera pelo fim do movimento. Com a roda de 60 mm (medida em
#: 2026-10-31) o robo anda ~5 cm/s a 40%: os 200 cm do teto levam ~40 s.
_ESPERA_MAX_S = 60.0
_INTERVALO_S = 0.3

_MOVIMENTOS = frozenset({"MOVE_DISTANCE", "TURN_LEFT", "TURN_RIGHT", "CALIBRATE_COMPASS"})


def distancia_no_lado(setores: list[dict], centro: float) -> float | None:
    """Menor distancia medida na janela do lado, ou None se nada mediu."""
    menor = None
    for setor in setores or []:
        cm = setor.get("distancia_cm")
        if cm is None:
            continue
        meio = (float(setor.get("inicio_graus", 0.0)) + float(setor.get("fim_graus", 0.0))) / 2
        if abs((meio - centro + 180.0) % 360.0 - 180.0) <= _MEIA_JANELA:
            menor = cm if menor is None else min(menor, cm)
    return menor


def _setores(estado: dict) -> list[dict]:
    return (estado.get("lidar") or {}).get("setores") or []


def _diferenca_angular(depois: float, antes: float) -> float:
    """Quanto o rumo andou, -180..180 (positivo = horario, para a direita)."""
    return (depois - antes + 540.0) % 360.0 - 180.0


class CorpoAgora:
    def __init__(self, url_estado: str = URL_ESTADO_PADRAO, ler=None) -> None:
        self._url = url_estado
        #: Injetavel para os testes: devolve o dict `estado` ou None.
        self._ler = ler or self._ler_http
        self._ultimo: str | None = None
        self._ultimo_em = 0.0

    def _ler_http(self) -> dict | None:
        try:
            with urllib.request.urlopen(self._url, timeout=1.5) as resposta:
                return json.load(resposta).get("estado")
        except Exception:  # noqa: BLE001 - sem estado, so sem informacao
            logger.debug("estado do corpo indisponivel em %s", self._url, exc_info=True)
            return None

    async def _estado(self) -> dict | None:
        return await asyncio.to_thread(self._ler)

    async def texto(self) -> str | None:
        """O bloco que o modelo le: mapa em volta, bussola, ultimo movimento."""
        estado = await self._estado()
        linhas: list[str] = []
        if estado:
            lidar = estado.get("lidar") or {}
            if lidar.get("valido"):
                partes = []
                for lado, centro in LADOS.items():
                    cm = distancia_no_lado(lidar.get("setores") or [], centro)
                    partes.append(f"{lado} {cm:.0f} cm" if cm is not None else f"{lado} sem eco")
                linhas.append(
                    "Espaco livre em volta, medido agora pelo LiDAR: " + ", ".join(partes) + "."
                )
            else:
                linhas.append("O LiDAR nao esta medindo agora: voce NAO sabe o que ha em volta.")
            hw = estado.get("hardware") or {}
            if not hw.get("bussola_conectada"):
                linhas.append("Bussola: desconectada.")
            elif hw.get("rumo_valido"):
                rumo = float(hw.get("rumo_graus", 0))
                linhas.append(f"Bussola calibrada, rumo {rumo:.0f} graus.")
            else:
                linhas.append(
                    "Bussola SEM calibracao: voce anda reto so pelos encoders. "
                    "Se for andar bastante, ofereca calibrar_bussola antes."
                )
        if self._ultimo and time.monotonic() - self._ultimo_em < _VALIDADE_ULTIMO_S:
            linhas.append("Seu ultimo movimento, conferido: " + self._ultimo)
        if not linhas:
            return None
        return (
            "SEU CORPO AGORA (medido, nao suposto):\n- " + "\n- ".join(linhas) + "\n"
            "Antes de andar, confira o espaco livre do lado para onde vai. Se "
            "o espaco for menor que a distancia pedida, ande menos e diga por que."
        )

    def e_movimento(self, comando: str) -> bool:
        return comando in _MOVIMENTOS

    async def conferir(self, comando: str, parametros: dict[str, Any], enviar) -> None:
        """Mede antes, manda o comando (`enviar()`), espera parar, mede depois.

        `enviar` e chamado AQUI, entre as duas medidas, para a primeira
        medida ser de antes de a roda girar. Erro ao enviar sobe para quem
        chamou - conferir um movimento que nao foi mandado nao faz sentido.
        """
        antes = await self._estado()
        await enviar()
        disparar(self._terminar(comando, dict(parametros), antes), f"conferir_{comando}")

    async def _terminar(self, comando: str, parametros: dict, antes: dict | None) -> None:
        try:
            depois = await self._esperar_parar(comando)
            resultado = self._comparar(comando, parametros, antes, depois)
        except Exception:  # noqa: BLE001 - a conferencia nunca derruba nada
            logger.exception("falha ao conferir o movimento %s", comando)
            return
        self._ultimo = resultado
        self._ultimo_em = time.monotonic()
        logger.info("movimento conferido: %s %s -> %s", comando, parametros, resultado)

    async def _esperar_parar(self, comando: str) -> dict | None:
        inicio = time.monotonic()
        # O primeiro meio segundo e de graca: a telemetria sai a cada 500 ms,
        # e logo depois do comando ela ainda pode dizer "parado".
        await asyncio.sleep(0.6)
        estado = None
        while time.monotonic() - inicio < _ESPERA_MAX_S:
            estado = await self._estado()
            hw = (estado or {}).get("hardware") or {}
            ocupado = hw.get("em_movimento") or (
                comando == "CALIBRATE_COMPASS" and hw.get("bussola_calibrando")
            )
            if estado is not None and not ocupado:
                # Uma leitura a mais: o LiDAR gira a ~6 Hz e o setor da frente
                # pode ser de antes de o robo parar.
                await asyncio.sleep(0.5)
                return await self._estado() or estado
            await asyncio.sleep(_INTERVALO_S)
        return estado

    @staticmethod
    def _comparar(comando: str, parametros: dict, antes: dict | None, depois: dict | None) -> str:
        if antes is None or depois is None:
            return "nao consegui ler o corpo antes e depois, entao nao sei se aconteceu."
        hw_antes = antes.get("hardware") or {}
        hw_depois = depois.get("hardware") or {}

        if comando == "CALIBRATE_COMPASS":
            if hw_depois.get("bussola_calibrada"):
                return "calibrei a bussola, e ela ficou valida."
            return "tentei calibrar a bussola e ela NAO ficou valida - talvez eu nao tenha girado."

        if comando == "MOVE_DISTANCE":
            pedido = float(parametros.get("distancia_cm", 0.0))
            lado = "frente" if pedido >= 0 else "tras"
            d_antes = distancia_no_lado(_setores(antes), LADOS[lado])
            d_depois = distancia_no_lado(_setores(depois), LADOS[lado])
            if d_antes is None or d_depois is None:
                return (
                    f"pedi {abs(pedido):.0f} cm para {lado}, mas o LiDAR nao tinha eco "
                    f"desse lado, entao nao da para conferir."
                )
            andou = d_antes - d_depois
            if abs(andou) < _RUIDO_CM:
                return (
                    f"pedi {abs(pedido):.0f} cm para {lado} e o LiDAR NAO viu eu sair do "
                    f"lugar ({d_antes:.0f} cm antes, {d_depois:.0f} depois). As rodas "
                    f"podem estar no ar, travadas ou sem energia."
                )
            return (
                f"pedi {abs(pedido):.0f} cm para {lado} e o LiDAR mediu {andou:.0f} cm "
                f"({d_antes:.0f} cm livres antes, {d_depois:.0f} depois)."
            )

        # TURN_LEFT / TURN_RIGHT: so a bussola sabe quanto girou de verdade.
        pedido = float(parametros.get("graus", 0.0))
        sentido = "direita" if comando == "TURN_RIGHT" else "esquerda"
        if not (hw_antes.get("rumo_valido") and hw_depois.get("rumo_valido")):
            return (
                f"pedi {pedido:.0f} graus para a {sentido}, mas a bussola nao esta "
                f"calibrada, entao nao sei quanto girei de verdade."
            )
        girou = _diferenca_angular(float(hw_depois["rumo_graus"]), float(hw_antes["rumo_graus"]))
        if abs(girou) < 3.0:
            return f"pedi {pedido:.0f} graus para a {sentido} e a bussola NAO viu eu girar."
        lado_real = "direita" if girou > 0 else "esquerda"
        return (
            f"pedi {pedido:.0f} graus para a {sentido} e a bussola mediu "
            f"{abs(girou):.0f} graus para a {lado_real}."
        )
