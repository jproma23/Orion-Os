"""Testes do transporte WebSocket do corpo.

O protocolo testado aqui NAO foi inventado: saiu da flash do ESP32 (o
firmware nao tem fonte) e o `ping` foi confirmado ao vivo. Ver o cabecalho de
ponte_corpo_ws.py.

Nenhum teste abre socket nem move robo - o `_enviar_bruto` vira um espiao.
"""
import json

import pytest

from motion_core.navigation.ponte_corpo_ws import ESCALA_DRIVE, TransporteCorpoWS
from orion.communication.protocol import Mensagem, TipoMensagem


def _transporte(**kw):
    t = TransporteCorpoWS(
        url="ws://teste:81",
        pulsos_por_metro=kw.get("pulsos_por_metro", 1000.0),
        pulsos_por_grau=kw.get("pulsos_por_grau", 10.0),
    )
    enviados = []

    async def bruto_falso(payload):
        enviados.append(payload)
        return True

    t._enviar_bruto = bruto_falso
    t._conectado = True
    return t, enviados


async def _comandar(t, payload):
    """Manda um COMMAND como o ComunicacaoService mandaria: bytes."""
    m = Mensagem.nova(TipoMensagem.COMMAND, "motion_core", "hardware_core", payload)
    await t.enviar(m.to_bytes())
    return m


def _drenar(t):
    quadros = []
    while not t._saida.empty():
        quadros.append(Mensagem.from_bytes(t._saida.get_nowait()))
    return quadros


@pytest.mark.asyncio
async def test_stop_vira_cmd_stop():
    t, enviados = _transporte()
    await _comandar(t, {"comando": "STOP"})
    assert enviados == [{"cmd": "stop"}]


@pytest.mark.asyncio
async def test_todo_comando_recebe_ACK():
    """O firmware nao manda ACK e o ComunicacaoService retransmite 3x sem ele
    - e o que enchia o log de 'ACK nao recebido'. A ponte forja o ACK, e ele
    tem de referenciar a mensagem certa."""
    t, _ = _transporte()
    m = await _comandar(t, {"comando": "STOP"})
    quadros = _drenar(t)
    acks = [q for q in quadros if q.tipo is TipoMensagem.ACK]
    assert len(acks) == 1
    assert acks[0].id_referencia == m.id, "o ACK tem que apontar para o comando"


@pytest.mark.asyncio
async def test_comando_sem_traducao_vira_NACK_e_nao_ACK():
    """Fingir sucesso e pior que falhar: o NavigationCore precisa saber que o
    corpo nao vai executar aquilo."""
    t, enviados = _transporte()
    await _comandar(t, {"comando": "DANCAR_FORRO"})
    quadros = _drenar(t)
    assert enviados == [], "sem traducao, nao se inventa comando para o corpo"
    assert [q.tipo for q in quadros] == [TipoMensagem.NACK]


@pytest.mark.asyncio
async def test_frente_vira_drive_nos_dois_lados():
    t, enviados = _transporte()
    await _comandar(t, {"comando": "MOVE_FORWARD", "velocidade_percent": 50})
    assert enviados == [
        {"cmd": "drive", "left": round(0.5 * ESCALA_DRIVE), "right": round(0.5 * ESCALA_DRIVE)}
    ]


@pytest.mark.asyncio
async def test_giro_poe_as_rodas_em_sentidos_opostos():
    """O firmware nao tem comando de giro - girar e drive assimetrico."""
    t, enviados = _transporte()
    await _comandar(t, {"comando": "TURN_LEFT", "graus": 90, "velocidade_percent": 40})
    assert enviados[0]["left"] == -enviados[0]["right"]
    assert enviados[0]["left"] < 0, "TURN_LEFT: roda esquerda para tras"


@pytest.mark.asyncio
async def test_move_distance_converte_cm_em_passos_e_freia_sozinho():
    t, enviados = _transporte(pulsos_por_metro=1000.0)
    t._telemetria = {"passos_esq": 0, "passos_dir": 0}

    await _comandar(t, {"comando": "MOVE_DISTANCE", "distancia_cm": 50,
                        "velocidade_percent": 60})
    assert enviados[0]["cmd"] == "drive"
    assert t._alvo_passos == 500, "50 cm a 1000 pulsos/m = 500 pulsos"

    await t._conferir_movimento_finito({"passos_esq": 400, "passos_dir": 400})
    assert len(enviados) == 1, "freou cedo demais"

    await t._conferir_movimento_finito({"passos_esq": 500, "passos_dir": 500})
    assert enviados[-1] == {"cmd": "stop"}
    assert t._alvo_passos is None


@pytest.mark.asyncio
async def test_telemetria_sai_com_os_nomes_QUE_O_MOTION_CORE_LE():
    """Os nomes nao batem: o firmware diz `passos_esq`, o Motion Core le
    `passos_esquerda` - e sao cinco consumidores. Traduzir aqui e o que evita
    cada um aprender o dialeto novo."""
    t, _ = _transporte()
    await t._ao_receber(json.dumps(
        {"tof": [-1, -1, -1, -1], "obstacle_distance": -1, "passos_esq": 3,
         "passos_dir": 4, "rssi": -61, "pan_us": 1500, "tilt_us": 1500}
    ))
    quadros = _drenar(t)
    assert len(quadros) == 1 and quadros[0].tipo is TipoMensagem.TELEMETRY
    p = quadros[0].payload
    assert p["passos_esquerda"] == 3
    assert p["passos_direita"] == 4
    assert p["estado"] == "IDLE" and p["em_movimento"] is False


@pytest.mark.asyncio
async def test_obstaculo_desconhecido_vira_None_e_nao_zero():
    """-1 e o 'nao sei' do firmware. Virar 0 seria 'obstaculo colado' - a
    leitura mais perigosa que existe para quem decide se pode andar."""
    t, _ = _transporte()
    await t._ao_receber(json.dumps({"passos_esq": 0, "passos_dir": 0,
                                    "obstacle_distance": -1}))
    p = _drenar(t)[0].payload
    assert p["distancia_frontal_cm"] is None
    assert p["frontal_sensor_ok"] is False

    await t._ao_receber(json.dumps({"passos_esq": 0, "passos_dir": 0,
                                    "obstacle_distance": 42}))
    p = _drenar(t)[0].payload
    assert p["distancia_frontal_cm"] == 42
    assert p["frontal_sensor_ok"] is True


@pytest.mark.asyncio
async def test_telemetria_nao_inventa_bussola():
    """Este firmware nao tem bussola. Publicar rumo_graus=0 seria pior que
    nao publicar: 0 grau e um rumo valido, e ninguem saberia que e mentira."""
    t, _ = _transporte()
    await t._ao_receber(json.dumps({"passos_esq": 0, "passos_dir": 0}))
    p = _drenar(t)[0].payload
    assert "rumo_graus" not in p
    assert "bussola_calibrada" not in p


@pytest.mark.asyncio
async def test_pong_e_lixo_nao_viram_telemetria():
    t, _ = _transporte()
    await t._ao_receber('{"pong":true}')
    await t._ao_receber("isto nao e json")
    assert _drenar(t) == []


@pytest.mark.asyncio
async def test_heartbeat_e_respondido():
    """O firmware nao conhece heartbeat. Sem resposta, o MonitorHeartbeat
    declararia o corpo perdido a cada ciclo com o link perfeito."""
    t, enviados = _transporte()
    m = Mensagem.nova(TipoMensagem.HEARTBEAT, "motion_core", "hardware_core", {})
    await t.enviar(m.to_bytes())
    quadros = _drenar(t)
    assert [q.tipo for q in quadros] == [TipoMensagem.HEARTBEAT]
    assert enviados == [], "heartbeat nao vira comando para o corpo"


@pytest.mark.asyncio
async def test_scan_front_responde_vazio_em_vez_de_pendurar():
    """O radar de ultrassom saiu do robo em 19/09. Sem esta resposta, cada
    segmento de ronda esperaria 2,1 s por um evento que nunca vem."""
    t, enviados = _transporte()
    await _comandar(t, {"comando": "SCAN_FRONT"})
    quadros = _drenar(t)
    eventos = [q for q in quadros if q.tipo is TipoMensagem.EVENT]
    assert eventos and eventos[0].payload["topico"] == "motion.scan_complete"
    assert eventos[0].payload["leituras"] == []
    assert enviados == []


@pytest.mark.asyncio
async def test_quadro_corrompido_nao_derruba_o_transporte():
    t, enviados = _transporte()
    await t.enviar(b"\x00\x01lixo nao e mensagem")
    assert enviados == []


@pytest.mark.asyncio
async def test_socket_aberto_sem_telemetria_nao_e_saudavel():
    t, _ = _transporte()
    assert t.esta_saudavel() is False
    await t._ao_receber(json.dumps({"passos_esq": 0, "passos_dir": 0}))
    assert t.esta_saudavel() is True


@pytest.mark.asyncio
async def test_receber_entrega_os_quadros_na_ordem():
    t, _ = _transporte()
    await t._ao_receber(json.dumps({"passos_esq": 1, "passos_dir": 1}))
    await t._ao_receber(json.dumps({"passos_esq": 2, "passos_dir": 2}))

    vistos = []
    async for bruto in t.receber():
        vistos.append(Mensagem.from_bytes(bruto).payload["passos_esquerda"])
        if len(vistos) == 2:
            break
    assert vistos == [1, 2]


@pytest.mark.asyncio
async def test_fechar_freia_antes_de_soltar_o_link():
    """O firmware nao para sozinho: soltar o link com o robo andando o
    deixaria andando."""
    t, enviados = _transporte()
    await t.fechar()
    assert enviados == [{"cmd": "stop"}]
    assert t.conectado is False


@pytest.mark.asyncio
async def test_fila_cheia_nao_estoura():
    t, _ = _transporte()
    for _ in range(t._saida.maxsize + 5):
        await t._ao_receber(json.dumps({"passos_esq": 0, "passos_dir": 0}))
    assert t._saida.full()


@pytest.mark.asyncio
async def test_set_pan_tilt_traduz_os_nomes_do_motion_core():
    """O teste REAL achou isto: o Motion Core manda SET_PAN_TILT com
    `pan_graus`/`tilt_graus`, e a primeira versao so conhecia `pan`/`tilt` -
    o log virou 'comando nao tem traducao' com o corpo conectado."""
    t, enviados = _transporte()
    await _comandar(t, {"comando": "SET_PAN_TILT", "pan_graus": 30, "tilt_graus": -15})
    assert enviados == [{"cmd": "gimbal", "pan": 30.0, "tilt": -15.0}]


@pytest.mark.asyncio
async def test_gimbal_limita_o_angulo():
    """A unidade que o firmware espera nao e conhecida. O limite impede que
    um valor em microssegundos (1500) chegue inteiro a um firmware que le
    graus e jogue o servo no batente - estol ja derrubou este ESP32 antes."""
    t, enviados = _transporte()
    await _comandar(t, {"comando": "SET_PAN_TILT", "pan_graus": 1500, "tilt_graus": -999})
    assert enviados == [{"cmd": "gimbal", "pan": 90.0, "tilt": -90.0}]


@pytest.mark.asyncio
async def test_gimbal_sem_eixo_nao_manda_comando_vazio():
    t, enviados = _transporte()
    await _comandar(t, {"comando": "SET_PAN_TILT"})
    assert enviados == []


# --- Convivencia com o firmware NOVO (firmware/corpo_ws) -------------------
#
# O firmware antigo nao tem fonte e nao sabe andar distancia; o novo sabe. A
# ponte serve aos dois e decide pela TELEMETRIA, nao por config - esquecer de
# virar uma chave depois de gravar daria um robo que anda e nao para.


def _telemetria_nova(**extra):
    d = {"passos_esq": 0, "passos_dir": 0, "estado": "IDLE",
         "em_movimento": False}
    d.update(extra)
    return json.dumps(d)


@pytest.mark.asyncio
async def test_detecta_firmware_novo_pela_telemetria():
    t, _ = _transporte()
    assert t._firmware_sabe_distancia is False
    await t._ao_receber(json.dumps({"passos_esq": 0, "passos_dir": 0}))
    assert t._firmware_sabe_distancia is False, "firmware antigo nao tem `estado`"

    await t._ao_receber(_telemetria_nova())
    assert t._firmware_sabe_distancia is True


@pytest.mark.asyncio
async def test_com_firmware_novo_a_distancia_vai_para_o_ESP32():
    """O ganho principal: o laco de distancia sai de cima do WiFi."""
    t, enviados = _transporte()
    await t._ao_receber(_telemetria_nova())

    await _comandar(t, {"comando": "MOVE_DISTANCE", "distancia_cm": 50,
                        "velocidade_percent": 60})
    assert enviados == [{"cmd": "move", "cm": 50.0, "vel": 60.0}]
    assert t._alvo_passos is None, "quem conta pulsos agora e o firmware"


@pytest.mark.asyncio
async def test_com_firmware_novo_o_giro_leva_o_sinal_do_sentido():
    t, enviados = _transporte()
    await t._ao_receber(_telemetria_nova())

    await _comandar(t, {"comando": "TURN_LEFT", "graus": 90,
                        "velocidade_percent": 40})
    assert enviados[0]["cmd"] == "turn"
    assert enviados[0]["graus"] == -90.0, "esquerda = anti-horario = negativo"

    await _comandar(t, {"comando": "TURN_RIGHT", "graus": 90,
                        "velocidade_percent": 40})
    assert enviados[1]["graus"] == 90.0


@pytest.mark.asyncio
async def test_fim_de_movimento_do_firmware_vira_evento():
    """Sem repassar isto, _aguardar_ocioso esperaria o timeout a cada
    segmento de ronda."""
    t, _ = _transporte()
    await t._ao_receber(json.dumps(
        {"evento": "movimento_concluido", "passos_esq": 500, "passos_dir": 498}
    ))
    quadros = _drenar(t)
    assert len(quadros) == 1 and quadros[0].tipo is TipoMensagem.EVENT
    assert quadros[0].payload["topico"] == "motion.movimento_concluido"
    assert quadros[0].payload["passos_esq"] == 500


@pytest.mark.asyncio
async def test_em_movimento_vem_do_ROBO_e_nao_do_palpite_da_ponte():
    """A ponte so sabe o que COMANDOU. Quando o corpo para sozinho (fim de
    move, watchdog, obstaculo) o palpite erra - e o firmware novo sabe."""
    t, _ = _transporte()
    await _comandar(t, {"comando": "MOVE_FORWARD", "velocidade_percent": 50})
    assert t._em_movimento is True, "a ponte acha que mandou andar"

    # o corpo diz que ja parou
    await t._ao_receber(_telemetria_nova(em_movimento=False, estado="IDLE"))
    p = _drenar(t)[-1].payload
    assert p["em_movimento"] is False
    assert p["estado"] == "IDLE"


@pytest.mark.asyncio
async def test_bussola_e_dht_passam_quando_existem():
    t, _ = _transporte()
    await t._ao_receber(_telemetria_nova(
        rumo_graus=137.5, bussola_calibrada=True,
        temperatura_c=24.3, umidade_percent=61.0))
    p = _drenar(t)[0].payload
    assert p["rumo_graus"] == 137.5
    assert p["bussola_calibrada"] is True
    assert p["temperatura_c"] == 24.3


@pytest.mark.asyncio
async def test_firmware_que_omite_a_bussola_nao_ganha_rumo_inventado():
    """O firmware novo OMITE o campo quando a leitura nao vale, em vez de
    mandar zero. A ponte nao pode preencher o buraco."""
    t, _ = _transporte()
    await t._ao_receber(_telemetria_nova(bussola_calibrada=False))
    p = _drenar(t)[0].payload
    assert "rumo_graus" not in p
    assert p["bussola_calibrada"] is False
