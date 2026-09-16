"""Testes do LidarManager (motion_core/lidar/gerenciador.py) SEM hardware.

Cobre o comportamento que nao depende do sensor fisico: selecao do driver
pelo config, tolerancia a ausencia da serial (nao crasha, reporta
`serial_indisponivel` e nao trava o boot), envio do comando de start ao abrir
a porta, e - desde 16/09 - as duas regressoes que deixaram o LiDAR morto por
semanas parecendo defeito de hardware:

  1. `serial.Serial(..., dtr=..., rts=...)`, que o pyserial real REJEITA;
  2. `publish()` chamado sem `await`, que descarta o evento em silencio.

Os fakes daqui imitam o comportamento REAL das dependencias de proposito. Um
fake permissivo demais (o `lambda *a, **k` e o `publish` sincrono que existiam
antes) deixa o teste verde enquanto a producao esta morta - foi exatamente o
que aconteceu.
"""
import asyncio
import sys

import pytest
import serial

sys.path.insert(0, "/home/jproma23/orion-os")

from motion_core.lidar import coin_d6, gerenciador


class EventBusFake:
    """Imita o EventBus real, onde `publish` e uma CORROTINA.

    Se este fake fosse sincrono, `self._event_bus.publish(...)` sem `await`
    passaria despercebido no teste e falharia calado em producao (criando uma
    corrotina que ninguem executa). Manter `async` aqui e o que amarra o
    contrato.
    """

    def __init__(self):
        self.eventos = []

    async def publish(self, topico, dados, prioridade=None):
        self.eventos.append((topico, dados))


def config_lidar(driver="coin_d6"):
    return {
        "habilitado": True,
        "driver": driver,
        "port": "/dev/lidar_d6",
        "baud_rate": 230400,
        "setores": 12,
        "min_distancia_mm": 50,
        "max_distancia_mm": 12000,
        "confianca_minima": 0,
    }


class SerialFake:
    """Serial de mentira que nasce FECHADA e so abre no `open()`.

    O fake antigo ja nascia `is_open = True` e nao tinha `open()` - por isso
    nao percebia como o gerenciador realmente abre a porta.
    """

    def __init__(self):
        self.is_open = False
        self.escritos = []

    def open(self):
        self.is_open = True

    def write(self, dados):
        self.escritos.append(bytes(dados))
        return len(dados)

    def flush(self):
        pass

    def close(self):
        self.is_open = False


# ---------- selecao do driver ----------


def test_driver_coin_d6_selecionado_pelo_config():
    manager = gerenciador.LidarManager(EventBusFake(), config_lidar("coin_d6"))
    assert manager._driver is coin_d6
    assert manager._cabecalho == coin_d6.HEADER_INICIO
    assert manager._tam_quadro == coin_d6.TAMANHO_QUADRO


def test_driver_ld06_e_padrao_quando_config_omite():
    config_sem_driver = config_lidar()
    config_sem_driver.pop("driver")
    manager = gerenciador.LidarManager(EventBusFake(), config_sem_driver)
    import motion_core.lidar.ld06 as ld06

    assert manager._driver is ld06


def test_driver_stl50b2_selecionado_pelo_config():
    from motion_core.lidar import stl50b2

    manager = gerenciador.LidarManager(EventBusFake(), config_lidar("stl50b2"))
    assert manager._driver is stl50b2
    assert manager._cabecalho == stl50b2.HEADER_INICIO
    assert manager._tam_quadro == 85


def test_nome_do_driver_no_log_nao_mente():
    """Ate 16/09 um if/else sobre DOIS drivers reportava o nome errado para o
    terceiro - log de LiDAR mentindo o modelo manda quem procura defeito para
    o lado errado."""
    for nome in ("ld06", "coin_d6", "stl50b2"):
        manager = gerenciador.LidarManager(EventBusFake(), config_lidar(nome))
        assert manager._nome_driver == nome


# ---------- inicializacao sem sensor ----------


def test_iniciar_sem_sensor_nao_crasha_e_reporta_serial_indisponivel(monkeypatch):
    def serial_inexistente(*args, **kwargs):
        raise serial.SerialException("no such device")

    monkeypatch.setattr(gerenciador.serial, "Serial", serial_inexistente)

    bus = EventBusFake()
    manager = gerenciador.LidarManager(bus, config_lidar())

    asyncio.run(manager._tentar_abrir())

    assert manager._porta is None
    assert not manager.conectado

    # reporta UMA vez, na borda
    asyncio.run(manager._reportar_erro("serial_indisponivel"))
    erros = [t for t, _ in bus.eventos if t == "lidar.error"]
    assert erros == ["lidar.error"]


def test_abrir_auto_sem_porta_nao_quebra_loop(monkeypatch):
    def serial_inexistente(*args, **kwargs):
        raise serial.SerialException("no such device")

    monkeypatch.setattr(gerenciador.serial, "Serial", serial_inexistente)
    monkeypatch.setattr(gerenciador, "PORTAS_CANDIDATAS", ())

    bus = EventBusFake()
    manager = gerenciador.LidarManager(bus, config_lidar())
    manager._port = "auto"

    asyncio.run(manager._tentar_abrir())
    assert manager._porta is None


# ---------- comando de start ao conectar ----------


def test_envia_comando_iniciar_ao_abrir_porta(monkeypatch):
    fake = SerialFake()
    monkeypatch.setattr(gerenciador.serial, "Serial", lambda *a, **k: fake)

    bus = EventBusFake()
    manager = gerenciador.LidarManager(bus, config_lidar())
    asyncio.run(manager._tentar_abrir())

    assert manager.conectado
    assert fake.escritos and fake.escritos[0] == coin_d6.COMANDO_INICIAR
    assert any(t == "lidar.status" for t, _ in bus.eventos)


def test_ld06_nao_recebe_comando_de_start(monkeypatch):
    # LD06 e passivo (TX-only); o gerenciador nao deve escrever nada na serial
    fake = SerialFake()
    monkeypatch.setattr(gerenciador.serial, "Serial", lambda *a, **k: fake)

    manager = gerenciador.LidarManager(EventBusFake(), config_lidar("ld06"))
    asyncio.run(manager._tentar_abrir())

    assert manager.conectado
    assert fake.escritos == []


def test_stl50b2_nao_recebe_comando_de_start(monkeypatch):
    """Medido na bancada: este sensor gira sozinho ao energizar."""
    fake = SerialFake()
    monkeypatch.setattr(gerenciador.serial, "Serial", lambda *a, **k: fake)

    manager = gerenciador.LidarManager(EventBusFake(), config_lidar("stl50b2"))
    asyncio.run(manager._tentar_abrir())

    assert manager.conectado
    assert fake.escritos == []


# ---------- regressao 1: a serial precisa aceitar o que o gerenciador manda ----


def test_gerenciador_configura_a_serial_de_um_jeito_que_o_pyserial_aceita(monkeypatch):
    """O teste que faltava - e que custou semanas de LiDAR morto.

    Ate 2026-09-16 o gerenciador chamava
    `serial.Serial(..., dtr=False, rts=False)`. Isso levanta
    `ValueError: unexpected keyword arguments` no pyserial de verdade, porque
    `dtr`/`rts` sao PROPRIEDADES, nao parametros do construtor. O `except` do
    `_tentar_abrir` so pegava `SerialException`/`OSError`, entao a excecao
    subia pela task solta do asyncio e morria sem UMA linha de log.

    Os outros testes nao pegaram porque o fake era `lambda *a, **k` - aceitava
    qualquer argumento, inclusive os invalidos. Aqui a classe REAL do pyserial
    e usada (construtor, properties e validacao de verdade); so o `open()` e
    neutralizado, para nao tocar em hardware nenhum.
    """
    abriu = []

    class SerialQuaseReal(serial.Serial):
        def open(self):
            abriu.append(self.port)
            self.is_open = True

        def close(self):
            self.is_open = False

        def write(self, dados):
            return len(dados)

        def flush(self):
            pass

    monkeypatch.setattr(gerenciador.serial, "Serial", SerialQuaseReal)

    manager = gerenciador.LidarManager(EventBusFake(), config_lidar("ld06"))
    asyncio.run(manager._tentar_abrir())  # antes do conserto: ValueError aqui

    assert manager.conectado, "o gerenciador nao conseguiu abrir a serial"
    assert abriu == ["/dev/lidar_d6"]
    # as propriedades tem que ter CHEGADO na porta, nao so nao ter estourado
    assert manager._porta.baudrate == 230400
    assert manager._porta.dtr is False
    assert manager._porta.rts is False


# ---------- regressao 2: publicar de verdade, nao no vazio ----


def _quadro_stl50b2(ang_ini, ang_fim, distancia_mm=1000):
    from motion_core.lidar import stl50b2

    dados = bytearray(stl50b2.HEADER_INICIO)
    dados += (25).to_bytes(2, "big")
    dados += int(ang_ini * 100).to_bytes(2, "little")
    dados += int(ang_fim * 100).to_bytes(2, "little")
    dados += bytes([0x00, 0x00])
    for _ in range(25):
        dados += bytes([0x80]) + distancia_mm.to_bytes(2, "little")
    return bytes(dados)


def test_scan_e_realmente_publicado_no_barramento(monkeypatch):
    """Regressao do bug "publicar no vazio" (16/09).

    `EventBus.publish` e uma CORROTINA. O gerenciador chamava sem `await`, o
    que apenas cria um objeto coroutine e o descarta: nenhum assinante recebe
    nada, e o unico sinal e um `RuntimeWarning` perdido no log. O LiDAR lia a
    serial perfeitamente e a navegacao, a web e o Nav2 nao viam UM evento.

    Este teste alimenta uma volta inteira e exige o evento do outro lado - se
    alguem tirar o `await`, ele falha.
    """
    # sem isto, so o PRIMEIRO quadro tenta publicar (janela ainda com 1 quadro,
    # cobertura incompleta) e os demais batem no intervalo minimo de 0,4 s
    monkeypatch.setattr(gerenciador, "INTERVALO_MINIMO_PUBLICACAO_S", 0.0)

    bus = EventBusFake()
    manager = gerenciador.LidarManager(bus, config_lidar("stl50b2"))

    async def alimentar():
        buffer = bytearray()
        ang = 0.0
        while ang < 360.0:
            buffer.extend(_quadro_stl50b2(ang % 360, (ang + 27.0) % 360))
            ang += 27.0
        await manager._consumir_buffer(buffer)

    asyncio.run(alimentar())

    scans = [d for t, d in bus.eventos if t == "lidar.scan"]
    assert scans, "nenhum lidar.scan publicado - publish sem await?"
    assert scans[-1]["valido"] is True
    assert len(scans[-1]["setores"]) == 12
    assert manager.scan_mais_recente is not None


def test_status_de_conexao_e_realmente_publicado(monkeypatch):
    """Mesma armadilha do await, no caminho de `lidar.status`."""
    fake = SerialFake()
    monkeypatch.setattr(gerenciador.serial, "Serial", lambda *a, **k: fake)

    bus = EventBusFake()
    manager = gerenciador.LidarManager(bus, config_lidar("stl50b2"))
    asyncio.run(manager._tentar_abrir())

    status = [d for t, d in bus.eventos if t == "lidar.status"]
    assert status, "nenhum lidar.status publicado - publish sem await?"
    assert status[0]["conectado"] is True
    assert status[0]["porta"] == "/dev/lidar_d6"


def test_erro_e_realmente_publicado():
    """Mesma armadilha do await, no caminho de `lidar.error`."""
    bus = EventBusFake()
    manager = gerenciador.LidarManager(bus, config_lidar("stl50b2"))

    asyncio.run(manager._reportar_erro("lidar_sem_dados"))

    erros = [d for t, d in bus.eventos if t == "lidar.error"]
    assert erros, "nenhum lidar.error publicado - publish sem await?"
    assert erros[0]["motivo"] == "lidar_sem_dados"


def test_setor_sem_eco_nao_publica_mas_nao_estoura():
    """Cobertura incompleta continua NAO publicando (decisao de seguranca: a
    navegacao trataria "nao sei" como vazio), mas o caminho tem que ser
    exercitado - e desde 16/09 ele avisa no log em vez de silenciar."""
    bus = EventBusFake()
    manager = gerenciador.LidarManager(bus, config_lidar("stl50b2"))

    async def alimentar():
        # so um pedaco da volta: a maioria dos setores fica sem eco
        buffer = bytearray(_quadro_stl50b2(10.0, 37.0))
        manager._ultima_publicacao_s = 0.0
        await manager._consumir_buffer(buffer)

    asyncio.run(alimentar())

    assert [d for t, d in bus.eventos if t == "lidar.scan"] == []
    assert manager.scan_mais_recente is None
