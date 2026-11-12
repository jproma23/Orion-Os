"""Diagnostico do YOLO no Notebook - QUAL elo esta quebrado (2026-11-12).

O dono: "o YOLO e importante, nao funciona, precisa funcionar". "Nao
reconhece nada" tem pelo menos cinco causas diferentes no codigo (ver
docs/ARQUITETURA_E_AUDITORIA.md, secao 6), e cada uma pede um conserto
diferente. Esta ferramenta testa os elos SEPARADOS, na ordem em que um
depende do outro, e para no primeiro que falha:

  1. MODELO  - o arquivo existe onde o servico procura? carrega?
  2. CAMERA  - o snapshot da API (:8081) vem? a imagem tem conteudo, ou e
               preta/lisa (lente tampada, teto, sem sinal)?
  3. YOLO    - na MESMA imagem, o modelo acha alguma coisa? com o limiar do
               servico (0,55) e com um limiar baixo (0,25), em 320 e em 640?
  4. CONTA   - o detector de "camera congelada" acusaria esta imagem?

NAO ABRE A CAMERA: o servico `orion-avatar` e o dono dela (uma camera USB
aceita um leitor so). A imagem vem do snapshot que o proprio servico serve.
Rode no Notebook, com o servico NO AR, do diretorio do repo:

    .venv/bin/python tools/diagnostico_yolo.py
    .venv/bin/python tools/diagnostico_yolo.py --salvar /tmp/quadro.jpg

Sem o servico no ar, use uma foto: `--arquivo foto.jpg`.
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

URL_PADRAO = "http://127.0.0.1:8081/camera/snapshot.jpg"


def _ok(texto: str) -> None:
    print(f"  [OK]    {texto}")


def _falha(texto: str, conserto: str) -> None:
    print(f"  [FALHA] {texto}")
    print(f"          -> {conserto}")


def _config_visao() -> dict:
    from orion.kernel.config import ConfigurationManager

    return ConfigurationManager(RAIZ / "config/orion.yaml").carregar().secao("vision")


def checar_modelo(conf: dict):
    print("1. MODELO")
    from orion.vision.modulo import PASTA_MODELOS, _nome_do_arquivo_do_modelo

    caminho = _nome_do_arquivo_do_modelo(conf.get("yolo_model", "yolov8n"))
    if Path(caminho).is_file():
        _ok(f"arquivo em {caminho}")
    else:
        _falha(
            f"{caminho} nao esta em {PASTA_MODELOS} nem e caminho absoluto",
            f"copiar o yolov8n.pt para {PASTA_MODELOS} (o Pi tem um em data/modelos/). "
            "Sem isso, sem internet, a visao inteira nao sobe.",
        )
    try:
        from ultralytics import YOLO

        inicio = time.monotonic()
        modelo = YOLO(caminho)
        _ok(f"carregou em {time.monotonic() - inicio:.1f} s ({len(modelo.names)} classes)")
        return modelo
    except Exception as erro:  # noqa: BLE001
        _falha(f"nao carregou: {erro!r}", "ver a mensagem acima (pacote ultralytics/torch?)")
        return None


def pegar_imagem(url: str, arquivo: str | None):
    print("2. CAMERA")
    import cv2
    import numpy as np

    if arquivo:
        imagem = cv2.imread(arquivo)
        origem = arquivo
    else:
        try:
            with urllib.request.urlopen(url, timeout=5) as resposta:
                dados = resposta.read()
        except Exception as erro:  # noqa: BLE001
            _falha(
                f"snapshot nao veio de {url}: {erro!r}",
                "o orion-avatar esta no ar? 503 = visao fora, pausada (RAM) ou camera "
                "recem-aberta: ver `journalctl --user -u orion-avatar | grep -i vis`",
            )
            return None
        imagem = cv2.imdecode(np.frombuffer(dados, np.uint8), cv2.IMREAD_COLOR)
        origem = url
    if imagem is None:
        _falha(f"imagem ilegivel ({origem})", "conferir o arquivo/URL")
        return None
    cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY)
    brilho, desvio = float(cinza.mean()), float(cinza.std())
    _ok(f"{imagem.shape[1]}x{imagem.shape[0]} de {origem}")
    if brilho < 20 and desvio < 8:
        _falha(
            f"imagem escura e lisa (brilho {brilho:.0f}, desvio {desvio:.0f})",
            "lente tampada, sala escura ou tela 'sem sinal' - o YOLO nao tem o que ver",
        )
    elif desvio < 12:
        _falha(
            f"imagem quase sem detalhe (desvio {desvio:.0f})",
            "camera apontada para teto/parede lisa? (ja aconteceu: o pan/tilt solto "
            "aponta para o teto) - salvar com --salvar e olhar",
        )
    else:
        _ok(f"imagem com conteudo (brilho {brilho:.0f}, desvio {desvio:.0f})")
    return imagem


def checar_yolo(modelo, imagem, conf: dict) -> None:
    print("3. YOLO na mesma imagem")
    limiar = float(conf.get("confidence_threshold", 0.55))
    limiar_desc = float(conf.get("confianca_para_descrever", 0.35))
    imgsz = int(conf.get("yolo_imgsz", 320))
    for tamanho in sorted({imgsz, 640}):
        inicio = time.monotonic()
        resultado = modelo(imagem, imgsz=tamanho, conf=0.25, verbose=False)[0]
        ms = (time.monotonic() - inicio) * 1000
        achados = sorted(
            ((modelo.names[int(c.cls[0])], float(c.conf[0])) for c in resultado.boxes),
            key=lambda x: -x[1],
        )
        print(f"   imgsz {tamanho} ({ms:.0f} ms): {len(achados)} achado(s) acima de 0,25")
        for nome, confianca in achados[:10]:
            marca = (
                "acao+descreve" if confianca >= limiar
                else "so descreve" if confianca >= limiar_desc
                else "ABAIXO dos dois limiares"
            )
            print(f"     - {nome:<14} {confianca:.2f}  ({marca})")
    print(
        f"   limiares do servico: acao {limiar:.2f}, descrever {limiar_desc:.2f}, imgsz {imgsz}"
    )
    print(
        "   Leitura: nada em 320 e algo em 640 = alvo pequeno/longe -> subir yolo_imgsz. "
        "Nada nem a 0,25 com a imagem boa = a cena nao tem o que o YOLO conhece."
    )


def checar_congelado(imagem) -> None:
    print("4. DETECTOR DE 'CAMERA CONGELADA'")
    import numpy as np

    from orion.vision.sem_sinal import DIFERENCA_MAXIMA_CONGELADO, DetectorSemSinal

    detector = DetectorSemSinal(quadros_seguidos=1)
    detector.avaliar(imagem)
    gerador = np.random.default_rng(0)
    ruido = np.clip(imagem.astype(np.int16) + gerador.integers(-1, 2, imagem.shape), 0, 255)
    avaliacao = detector.avaliar(ruido.astype(np.uint8))
    if DIFERENCA_MAXIMA_CONGELADO > 0.05:
        _falha(
            f"limiar de congelado {DIFERENCA_MAXIMA_CONGELADO} (codigo antigo)",
            "o merge da auditoria nao entrou: com cena parada a camera boa vira 'sem sinal' "
            "e o YOLO para de rodar (achado V1)",
        )
    elif avaliacao.sem_sinal:
        _falha("esta imagem seria tratada como sem sinal", "ver brilho/desvio no passo 2")
    else:
        _ok(f"limiar {DIFERENCA_MAXIMA_CONGELADO} - cena parada nao vira 'sem sinal'")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", default=URL_PADRAO)
    parser.add_argument("--arquivo", help="usar esta foto em vez do snapshot")
    parser.add_argument("--salvar", help="salvar a imagem testada neste caminho")
    args = parser.parse_args()

    conf = _config_visao()
    modelo = checar_modelo(conf)
    if modelo is None:
        return 1
    imagem = pegar_imagem(args.url, args.arquivo)
    if imagem is None:
        return 2
    if args.salvar:
        import cv2

        cv2.imwrite(args.salvar, imagem)
        print(f"   imagem salva em {args.salvar}")
    checar_yolo(modelo, imagem, conf)
    checar_congelado(imagem)
    return 0


if __name__ == "__main__":
    sys.exit(main())
