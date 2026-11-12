"""Testes do parser do LiDAR STL-50B2 - rodam SEM hardware nenhum.

Monta quadros sinteticos byte a byte e confere se o parser os decodifica como
esperado. O alvo principal e a virada de 360 para 0 graus, que e onde um erro
passaria despercebido no teste de bancada (a varredura sai "certa" no olho e
os pontos do cruzamento do zero vao parar do outro lado da volta).

Os testes de interface no fim existem porque `gerenciador.py` troca de driver
por configuracao: se o STL-50B2 nao expuser exatamente o que o gerenciador
procura, a falha aparece so com o hardware na mao, em runtime.
"""

import pytest

from motion_core.lidar import stl50b2


def montar_quadro(
    ang_ini_graus: float,
    ang_fim_graus: float,
    distancia_mm: int = 1000,
    intensidade: int = 0x80,
    pontos: int = stl50b2.PONTOS_POR_QUADRO,
) -> bytes:
    """Monta um quadro valido de 85 bytes, como o sensor mandaria.

    `distancia_mm` e a distancia REAL; no fio ela vai em quartos de milimetro
    (ESCALA_DISTANCIA), como o sensor manda - com teto de 16 bits.
    """
    dados = bytearray()
    dados += stl50b2.HEADER_INICIO
    dados += pontos.to_bytes(2, "big")  # contagem e BIG endian
    dados += int(ang_ini_graus * 100).to_bytes(2, "little")
    dados += int(ang_fim_graus * 100).to_bytes(2, "little")
    dados += bytes([0x00, 0x00])  # campo nao decifrado
    for _ in range(stl50b2.PONTOS_POR_QUADRO):
        dados += bytes([intensidade])
        bruto = min(distancia_mm * stl50b2.ESCALA_DISTANCIA, 0xFFFF)
        dados += bruto.to_bytes(2, "little")
    assert len(dados) == stl50b2.TAMANHO_QUADRO, len(dados)
    return bytes(dados)


# ---------------------------------------------------------------------------
# Decodificacao
# ---------------------------------------------------------------------------


def test_quadro_valido_decodifica_25_pontos():
    quadro = stl50b2.decodificar_quadro(montar_quadro(10.0, 37.0, 1234))
    assert quadro is not None, "quadro valido foi rejeitado"
    assert len(quadro.pontos) == 25
    assert quadro.pontos[0].angulo_graus == pytest.approx(10.0)
    assert quadro.pontos[-1].angulo_graus == pytest.approx(37.0, abs=0.01)
    assert quadro.pontos[0].distancia_mm == 1234
    assert quadro.pontos[0].confianca == 0x80


def test_distancia_vem_em_quartos_de_milimetro():
    """REGRESSAO 2026-10-31: o campo era lido como milimetro e tudo saia 4x
    mais longe - o robo bateu em parede com a trava de 50 cm ligada. Medido
    com o encoder: re de 20 cm afastando da parede mudou a leitura em ~90."""
    dados = bytearray(montar_quadro(10.0, 37.0))
    base = stl50b2.DESLOCAMENTO_PONTOS
    dados[base + 1 : base + 3] = (4000).to_bytes(2, "little")  # 4000 quartos de mm
    quadro = stl50b2.decodificar_quadro(bytes(dados))
    assert quadro.pontos[0].distancia_mm == 1000


def test_angulos_interpolados_com_wrap_de_360():
    """O caso critico: quadro que cruza o zero.

    Sem o tratamento do wrap, os 25 pontos seriam espalhados para TRAS pela
    volta inteira (350 -> 17 vira um passo negativo gigante).
    """
    quadro = stl50b2.decodificar_quadro(montar_quadro(350.0, 17.0))
    assert quadro is not None
    angulos = [p.angulo_graus for p in quadro.pontos]

    assert all(0.0 <= a < 360.0 for a in angulos), angulos
    # nenhum ponto pode cair no meio da volta (ex.: 180 graus)
    espalhados = [a for a in angulos if 30 < a < 340]
    assert not espalhados, f"pontos espalhados pela volta: {espalhados}"
    assert any(a >= 350 for a in angulos), "sumiram os pontos antes do zero"
    assert any(a <= 17 for a in angulos), "sumiram os pontos depois do zero"


def test_header_errado_devolve_none():
    quadro = bytearray(montar_quadro(10.0, 37.0))
    quadro[0] = 0xAB
    assert stl50b2.decodificar_quadro(bytes(quadro)) is None


def test_tamanho_errado_devolve_none():
    assert stl50b2.decodificar_quadro(montar_quadro(10.0, 37.0)[:-1]) is None
    assert stl50b2.decodificar_quadro(b"") is None
    assert stl50b2.decodificar_quadro(None) is None


def test_contagem_de_pontos_errada_devolve_none():
    """Sem checksum no protocolo, a contagem e a principal defesa contra um
    `AA 55` que caiu por acaso dentro dos dados de distancia."""
    quadro = bytearray(montar_quadro(10.0, 37.0))
    quadro[2:4] = (24).to_bytes(2, "big")  # diz 24 pontos, deveria ser 25
    assert stl50b2.decodificar_quadro(bytes(quadro)) is None


def test_angulo_fora_da_faixa_devolve_none():
    """Angulo >= 360 depois da escala significa quadro desalinhado."""
    quadro = bytearray(montar_quadro(10.0, 37.0))
    quadro[4:6] = (36500).to_bytes(2, "little")  # 365 graus
    assert stl50b2.decodificar_quadro(bytes(quadro)) is None


def test_distancia_e_lida_em_little_endian():
    """Regressao da armadilha que custou um ciclo na bancada: 16 bits lidos
    como byte truncam no byte baixo e todo valor cai na faixa 0-255."""
    quadro = stl50b2.decodificar_quadro(montar_quadro(10.0, 37.0, distancia_mm=2183))
    assert quadro is not None
    assert quadro.pontos[0].distancia_mm == 2183, "valor de 16 bits foi truncado"


# ---------------------------------------------------------------------------
# Filtro de "sem retorno"
# ---------------------------------------------------------------------------


def test_sem_retorno_nao_vira_parede_falsa():
    """1 mm e o codigo de 'nao recebi eco', nao objeto colado na lente."""
    quadro = stl50b2.decodificar_quadro(montar_quadro(10.0, 37.0, distancia_mm=1))
    assert quadro is not None
    assert len(quadro.pontos) == 25, "os pontos crus continuam la"
    assert stl50b2.pontos_validos(quadro) == (), "1 mm devia ser descartado"


def test_distancia_absurda_e_descartada():
    quadro = stl50b2.decodificar_quadro(montar_quadro(10.0, 37.0, distancia_mm=60000))
    assert quadro is not None
    assert stl50b2.pontos_validos(quadro) == ()


# ---------------------------------------------------------------------------
# acumular_scan
# ---------------------------------------------------------------------------


def test_acumular_scan_obstaculo_que_chegou_perto_aparece():
    """Chegando perto, o quadro mais novo e o mais perto - e e o que vale."""
    longe = stl50b2.decodificar_quadro(montar_quadro(10.0, 37.0, distancia_mm=3000))
    perto = stl50b2.decodificar_quadro(montar_quadro(10.0, 37.0, distancia_mm=500))
    scan = stl50b2.acumular_scan([longe, perto])
    assert scan, "scan vazio"
    assert all(p.distancia_mm == 500 for p in scan.values())


def test_obstaculo_que_foi_embora_nao_vira_fantasma():
    """C2-06 (auditoria 12/11): a janela guarda ~3,4 voltas. Com "menor vence"
    entre voltas, o obstaculo que saiu daquele angulo (o robo girou) seguia
    aparecendo por ~350 ms. Entre quadros, vale o mais novo."""
    antes = stl50b2.decodificar_quadro(montar_quadro(10.0, 37.0, distancia_mm=400))
    depois = stl50b2.decodificar_quadro(montar_quadro(10.0, 37.0, distancia_mm=2500))
    scan = stl50b2.acumular_scan([antes, depois])
    assert scan
    assert all(p.distancia_mm == 2500 for p in scan.values())


def test_acumular_scan_ignora_pontos_sem_retorno():
    """Se o 1 mm passasse, o menor-vence transformaria a direcao em parede."""
    real = stl50b2.decodificar_quadro(montar_quadro(10.0, 37.0, distancia_mm=800))
    sem_eco = stl50b2.decodificar_quadro(montar_quadro(10.0, 37.0, distancia_mm=1))
    scan = stl50b2.acumular_scan([real, sem_eco])
    assert scan
    assert all(p.distancia_mm == 800 for p in scan.values())


def test_volta_completa_cobre_os_36_setores():
    """Uma volta inteira em passos de 27 graus nao pode deixar buraco."""
    quadros = []
    ang = 0.0
    while ang < 360.0:
        fim = ang + 27.0
        quadro = stl50b2.decodificar_quadro(montar_quadro(ang % 360, fim % 360))
        assert quadro is not None, f"quadro invalido em {ang}"
        quadros.append(quadro)
        ang = fim

    scan = stl50b2.acumular_scan(quadros)
    faltando = [s for s in range(0, 360, 10) if not any(s <= a < s + 10 for a in scan)]
    assert not faltando, f"setores sem nenhum ponto: {faltando}"


def test_acumular_scan_vazio_nao_explode():
    assert stl50b2.acumular_scan([]) == {}


# ---------------------------------------------------------------------------
# Contrato com o gerenciador (trocavel por configuracao)
# ---------------------------------------------------------------------------


def test_expoe_o_que_o_gerenciador_procura():
    """`gerenciador.py` le estes nomes do modulo do driver ativo."""
    assert stl50b2.HEADER_INICIO == b"\xaa\x55"
    assert stl50b2.TAMANHO_QUADRO == 85
    assert callable(stl50b2.decodificar_quadro)
    assert callable(stl50b2.acumular_scan)


def test_nao_tem_comando_de_start():
    """Este LiDAR gira sozinho ao energizar - foi MEDIDO, o anuncio mentia.

    O gerenciador procura `COMANDO_INICIAR` com getattr; a ausencia aqui e o
    que faz ele nao mandar byte nenhum. Se alguem adicionar o atributo 'por
    simetria' com o COIN-D6, o gerenciador passa a escrever na serial de um
    sensor que nao espera comando.
    """
    assert not hasattr(stl50b2, "COMANDO_INICIAR")


def test_baud_e_230400_e_nao_115200():
    """Medido: em 115200 nao sai um byte sequer."""
    assert stl50b2.BAUD_PADRAO == 230400


def test_velocidade_e_zero_porque_nao_foi_decifrada():
    """O campo +8..9 do quadro nunca foi decifrado.

    O gerenciador usa `velocidade_graus_s` para publicar `rpm` em
    `lidar.scan`. Com 0 aqui o rpm sai 0.0 - um "nao sei" honesto. Este teste
    trava a decisao: se alguem preencher com o campo cru achando que e
    velocidade, o rpm publicado vira numero inventado.
    """
    quadro = stl50b2.decodificar_quadro(montar_quadro(10.0, 37.0))
    assert quadro is not None
    assert quadro.velocidade_graus_s == 0.0


def test_campo_nao_decifrado_e_preservado_cru():
    """Guardado sem interpretar - e a unica pista para decifrar depois."""
    dados = bytearray(montar_quadro(10.0, 37.0))
    dados[8:10] = (0x1234).to_bytes(2, "little")
    quadro = stl50b2.decodificar_quadro(bytes(dados))
    assert quadro is not None
    assert quadro.campo_nao_decifrado == 0x1234
