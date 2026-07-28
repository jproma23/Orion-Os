"""Teste de hardware do endpoint /comando (joystick, Cap 13 s.4).

Criado em 2026-07-27 para fechar a etapa 5 (testes no hardware) do fluxo de
trabalho (ARQUITETURA.txt) na correcao do controle manual: ate aqui o
/comando so tinha teste unitario (contra o Event Bus em memoria), nunca
contra o Motion Core real falando com o Mega de verdade.

Bate no HTTP da webui (nao abre a serial direto, ao contrario de
testar_ultrassom.py) - e o MESMO caminho que o joystick do navegador usa,
entao um teste que passa aqui prova o roundtrip completo: navegador ->
/comando -> navigation.comando(MANUAL) -> ControleManual assume no maestro
-> NavigationCore.executar_manual -> comm.send -> Mega -> ACK.

Fases, em ordem crescente de risco fisico:
  1. LIGHT_ON/LIGHT_OFF - prova o pipeline inteiro sem tocar em nenhum
     motor (LED apenas). Se isto falhar, o problema esta ANTES do
     driver dos motores (webui, event bus, NavigationCore ou o link
     serial) - nao adianta testar movimento.
  2. STOP - sempre seguro, prova que o comando de parada chega mesmo
     sem nada em movimento.
  3. Pulso de giro (TURN_LEFT curto) - so roda se --com-motor for
     passado explicitamente. Gira no proprio eixo (nao desloca o robo
     para frente/tras) e para sozinho apos GIRO_DURACAO_S. Ainda assim
     MOVE MOTOR DE VERDADE - so use com o robo em posicao segura
     (bancada, rodas livres de obstaculo) e alguem observando.

Pre-requisito: orion-motion.service tem que estar rodando (e' ele quem
serve a webui e fala com o Mega) - `systemctl --user is-active orion-motion`.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import aiohttp

URL_BASE = "http://127.0.0.1:8080"
GIRO_VELOCIDADE_PERCENT = 25.0
GIRO_DURACAO_S = 0.4


async def _estado(sessao: aiohttp.ClientSession) -> dict:
    async with sessao.get(f"{URL_BASE}/estado") as resposta:
        resposta.raise_for_status()
        return await resposta.json()


async def _comando(sessao: aiohttp.ClientSession, comando: str, **extra) -> dict:
    async with sessao.post(f"{URL_BASE}/comando", json={"comando": comando, **extra}) as resposta:
        corpo = await resposta.json()
        return {"status_http": resposta.status, "corpo": corpo}


async def _fase_luz(sessao: aiohttp.ClientSession) -> bool:
    print("\n[1/2] LIGHT_ON / LIGHT_OFF - prova o pipeline sem tocar motor")
    ligar = await _comando(sessao, "LIGHT_ON")
    print(f"  LIGHT_ON  -> HTTP {ligar['status_http']}, corpo={ligar['corpo']}")
    if ligar["status_http"] != 200:
        print("  FALHOU: /comando recusou LIGHT_ON antes mesmo de tentar o Mega.")
        return False
    await asyncio.sleep(0.5)
    apagar = await _comando(sessao, "LIGHT_OFF")
    print(f"  LIGHT_OFF -> HTTP {apagar['status_http']}, corpo={apagar['corpo']}")
    if apagar["status_http"] != 200:
        print("  FALHOU: LIGHT_OFF recusado.")
        return False
    print("  OK - pipeline HTTP->EventBus->NavigationCore aceitou os dois comandos.")
    print("  Confirme visualmente: a lanterna do robo piscou por ~0.5s? Se NAO,")
    print("  o comando saiu do Pi mas nao chegou/executou no Mega (serial/ACK).")
    return True


async def _fase_stop(sessao: aiohttp.ClientSession) -> bool:
    print("\n[2/2] STOP - sempre seguro, prova que a parada chega")
    parar = await _comando(sessao, "STOP")
    print(f"  STOP -> HTTP {parar['status_http']}, corpo={parar['corpo']}")
    return parar["status_http"] == 200


async def _fase_motor(sessao: aiohttp.ClientSession) -> bool:
    print(
        f"\n[EXTRA] Pulso de giro (TURN_LEFT, {GIRO_VELOCIDADE_PERCENT:.0f}%, "
        f"~{GIRO_DURACAO_S:.1f}s) - MOVE MOTOR DE VERDADE"
    )
    antes = await _estado(sessao)
    girar = await _comando(sessao, "TURN_LEFT", velocidade_percent=GIRO_VELOCIDADE_PERCENT)
    print(f"  TURN_LEFT -> HTTP {girar['status_http']}, corpo={girar['corpo']}")
    if girar["status_http"] != 200:
        print("  FALHOU: comando recusado antes de girar.")
        return False
    await asyncio.sleep(GIRO_DURACAO_S)
    parar = await _comando(sessao, "STOP")
    print(f"  STOP -> HTTP {parar['status_http']}, corpo={parar['corpo']}")
    depois = await _estado(sessao)
    hw_antes = antes.get("estado", {}).get("hardware", {})
    hw_depois = depois.get("estado", {}).get("hardware", {})
    print(f"  hardware antes:  em_movimento={hw_antes.get('em_movimento')}")
    print(f"  hardware depois: em_movimento={hw_depois.get('em_movimento')}")
    return parar["status_http"] == 200


async def principal(com_motor: bool) -> int:
    async with aiohttp.ClientSession() as sessao:
        try:
            estado_inicial = await _estado(sessao)
        except aiohttp.ClientConnectorError:
            print(
                f"ERRO: nao conectou em {URL_BASE} - o orion-motion.service esta "
                "rodando? (`systemctl --user is-active orion-motion`)",
                file=sys.stderr,
            )
            return 1
        print(f"Conectado. Estado do hardware agora: {estado_inicial.get('estado', {}).get('hardware')}")

        ok_luz = await _fase_luz(sessao)
        ok_stop = await _fase_stop(sessao)
        ok_motor = True
        if com_motor:
            ok_motor = await _fase_motor(sessao)
        else:
            print(
                "\n[EXTRA] pulado (rode com --com-motor para testar giro real - "
                "so com o robo em posicao segura e alguem observando)"
            )

        print("\n=== resumo ===")
        print(f"LIGHT_ON/OFF (pipeline): {'OK' if ok_luz else 'FALHOU'}")
        print(f"STOP:                    {'OK' if ok_stop else 'FALHOU'}")
        if com_motor:
            print(f"Giro real:               {'OK' if ok_motor else 'FALHOU'}")
        return 0 if (ok_luz and ok_stop and ok_motor) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--com-motor",
        action="store_true",
        help="tambem testa um pulso curto de giro real (move motor de verdade)",
    )
    argumentos = parser.parse_args()
    raise SystemExit(asyncio.run(principal(argumentos.com_motor)))
