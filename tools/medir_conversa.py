"""T0 - instrumento de medida da naturalidade da fala (missao de 2026-07-30).

Roda N turnos de conversa com ENTRADA DE TEXTO FIXA, de proposito: o canal de
audio tem razao rms 0,97x e faz o robo responder a transcrito parcial. Medir
com o mic dentro misturaria duas frentes, e nenhuma melhora seria atribuivel.
Ver a ressalva da missao.

Uso:
    python tools/medir_conversa.py                 # so texto (rapido)
    python tools/medir_conversa.py --com-audio     # mede tambem a sintese
    python tools/medir_conversa.py --turnos 20 --saida data/baseline.json

Metricas (as cinco pedidas em T0):
    aberturas distintas ... 3 primeiras palavras normalizadas, unicas/N
    palavras .............. media e p90
    saudacao .............. % de respostas que contem cumprimento
    latencia 1o audio ..... do fim da entrada ao primeiro sample
    latencia fim da fala .. do fim da entrada ao ultimo sample
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import subprocess
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, "src")

from orion.mission.ai_manager import AiManager  # noqa: E402

#: As falas nao sao aleatorias: sao o tipo de coisa que o dono realmente diz ao
#: robo, misturando pergunta sobre o mundo (que exige o diario), conversa
#: social (que NAO exige registro nenhum) e pedido de acao. Ficam fixas para
#: que duas rodadas sejam comparaveis - baseline e "depois" tem que responder a
#: mesma coisa.
FALAS = [
    "oi Fofão",
    "tudo bem?",
    "como você está hoje?",
    "o que você viu hoje?",
    "você viu alguém passar aí?",
    "que horas são?",
    "o que você tá fazendo?",
    "você consegue andar?",
    "vai dar uma volta na sala",
    "tá tudo tranquilo por aí?",
    "você me reconhece?",
    "quem mora aqui com a gente?",
    "você gosta de conversar?",
    "me conta uma coisa",
    "você tá cansado?",
    "o que você acha da casa?",
    "tem alguém na cozinha?",
    "você viu o Kamal hoje?",
    "obrigado, Fofão",
    "tchau",
]

#: Cumprimentos que contam como saudacao. Fronteira de palavra para "oi" nao
#: casar dentro de "oito" nem "ola" dentro de "olamos".
SAUDACOES = re.compile(
    r"\b(oi|ola|olá|opa|eae|e aí|e ai|bom dia|boa tarde|boa noite|salve|"
    r"tudo bem|tudo bom|como vai|prazer)\b",
    re.IGNORECASE,
)


def normalizar(texto: str) -> str:
    """Minusculas, sem acento e sem pontuacao - para comparar abertura."""
    texto = unicodedata.normalize("NFD", texto.lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w\s]", " ", texto)


def assinatura_abertura(resposta: str, nomes: list[str], palavras: int = 3) -> str:
    """As `palavras` primeiras palavras, sem o nome proprio.

    O nome sai porque "Oi, João, tudo bem?" e "Oi, tudo bem?" sao a MESMA
    abertura - foi por nao remover o nome que o dedup por frase inteira de
    eda6828 deixou a abertura passar batido.
    """
    limpo = normalizar(resposta)
    fora = {normalizar(n).strip() for n in nomes}
    fora = {p for n in fora for p in n.split()}
    tokens = [t for t in limpo.split() if t and t not in fora]
    return " ".join(tokens[:palavras])


def percentil(valores: list[float], p: float) -> float:
    if not valores:
        return 0.0
    ordenado = sorted(valores)
    k = (len(ordenado) - 1) * p
    baixo, alto = int(k), min(int(k) + 1, len(ordenado) - 1)
    return ordenado[baixo] + (ordenado[alto] - ordenado[baixo]) * (k - baixo)


async def principal() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turnos", type=int, default=20)
    ap.add_argument("--com-audio", action="store_true", help="mede tambem a sintese Piper")
    ap.add_argument("--saida", default="")
    ap.add_argument("--rotulo", default="baseline")
    args = ap.parse_args()

    conf = yaml.safe_load(open("config/orion.yaml", encoding="utf-8"))
    ia_conf = conf["ai"] if "ai" in conf else conf["ia"]
    voice_conf = conf["voice"]

    ia = AiManager(
        temperatura=ia_conf["temperature"],
        caminho_prompt_sistema=ia_conf["system_prompt_file"],
        max_tokens_resposta=ia_conf["resposta_max_tokens"],
        openai_model=ia_conf.get("openai_model", "gemini-flash-lite-latest"),
        openai_base_url=ia_conf.get("openai_base_url"),
    )

    sintetizador = None
    if args.com_audio:
        from piper import PiperVoice

        sintetizador = PiperVoice.load(voice_conf["piper_voice_path"])

    #: Contexto representativo e ESTAVEL. A pessoa esta presente nos 20 turnos:
    #: e exatamente o cenario em que a saudacao repetida incomoda, e o criterio
    #: de saida de T1 fala dele ("20 turnos com a mesma pessoa presente").
    nomes = ["João Paulo", "Kamal", "Marah"]
    contexto = {
        "pessoa": {"nome": "João Paulo"},
        "familia": nomes,
        # retrato e observacoes seguem o contrato de grounding.py: dict com as
        # chaves que bloco_corpo/bloco_observacoes leem. Passar string aqui
        # levanta AttributeError - foi o que aconteceu na primeira execucao.
        "retrato": {
            "obstaculo_frente_cm": 120.0,
            "obstaculo_tras_cm": 98.0,
        },
        "observacoes": [
            {"quando": "12:05", "o_que": "vi João Paulo"},
            {"quando": "12:06", "o_que": "vi um objeto da classe bottle pela câmera"},
        ],
    }

    falas = (FALAS * ((args.turnos // len(FALAS)) + 1))[: args.turnos]
    turnos = []

    print(f"rodando {args.turnos} turnos (modelo={ia_conf.get('openai_model')})...\n")
    for i, fala in enumerate(falas, 1):
        inicio = time.monotonic()
        try:
            resposta = await ia.responder(fala, contexto)
        except Exception as erro:
            print(f"  {i:2d}. FALHA: {erro}")
            continue
        t_texto = time.monotonic() - inicio

        t_primeiro_audio = t_fim_fala = None
        if sintetizador is not None:
            import io
            import wave

            t_sint = time.monotonic()
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav:
                sintetizador.synthesize_wav(resposta, wav)
            custo_sintese = time.monotonic() - t_sint
            with wave.open(io.BytesIO(buffer.getvalue())) as w:
                duracao = w.getnframes() / w.getframerate()
            # Sem streaming, o primeiro sample so sai depois de sintetizar TUDO.
            t_primeiro_audio = t_texto + custo_sintese
            t_fim_fala = t_primeiro_audio + duracao

        n_palavras = len(resposta.split())
        tem_saudacao = bool(SAUDACOES.search(resposta))
        turnos.append(
            {
                "fala": fala,
                "resposta": resposta,
                "palavras": n_palavras,
                "abertura": assinatura_abertura(resposta, nomes),
                "saudacao": tem_saudacao,
                "s_texto": round(t_texto, 3),
                "s_primeiro_audio": round(t_primeiro_audio, 3) if t_primeiro_audio else None,
                "s_fim_fala": round(t_fim_fala, 3) if t_fim_fala else None,
            }
        )
        marca = "S" if tem_saudacao else " "
        print(f"  {i:2d}. [{marca}] {n_palavras:3d}p {t_texto:5.2f}s | {resposta[:70]}")

    if not turnos:
        print("\nnenhum turno completou - nada a medir")
        return

    palavras = [t["palavras"] for t in turnos]
    aberturas = [t["abertura"] for t in turnos]
    com_saudacao = sum(1 for t in turnos if t["saudacao"])
    lat_texto = [t["s_texto"] for t in turnos]
    lat_audio = [t["s_primeiro_audio"] for t in turnos if t["s_primeiro_audio"]]
    lat_fim = [t["s_fim_fala"] for t in turnos if t["s_fim_fala"]]

    resumo = {
        "rotulo": args.rotulo,
        "data": datetime.now().isoformat(timespec="seconds"),
        "commit": subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
        ).stdout.strip(),
        "turnos": len(turnos),
        "modelo": ia_conf.get("openai_model"),
        "aberturas_distintas": len(set(aberturas)),
        "aberturas_total": len(aberturas),
        "palavras_media": round(statistics.mean(palavras), 1),
        "palavras_p90": round(percentil(palavras, 0.9), 1),
        "respostas_com_saudacao": com_saudacao,
        "saudacao_pct": round(100 * com_saudacao / len(turnos), 1),
        "s_texto_media": round(statistics.mean(lat_texto), 2),
        "s_primeiro_audio_media": round(statistics.mean(lat_audio), 2) if lat_audio else None,
        "s_fim_fala_media": round(statistics.mean(lat_fim), 2) if lat_fim else None,
    }

    print("\n" + "=" * 62)
    print(f"  aberturas distintas ..... {resumo['aberturas_distintas']}/{resumo['aberturas_total']}")
    print(f"  palavras (media/p90) .... {resumo['palavras_media']} / {resumo['palavras_p90']}")
    print(f"  com saudacao ............ {com_saudacao}/{len(turnos)} ({resumo['saudacao_pct']}%)")
    print(f"  latencia texto .......... {resumo['s_texto_media']}s")
    if lat_audio:
        print(f"  latencia 1o audio ....... {resumo['s_primeiro_audio_media']}s")
        print(f"  latencia fim da fala .... {resumo['s_fim_fala_media']}s")
    print("=" * 62)

    print("\n  aberturas mais repetidas:")
    from collections import Counter

    for abertura, n in Counter(aberturas).most_common(5):
        if n > 1:
            print(f"    {n}x  {abertura!r}")

    if args.saida:
        destino = Path(args.saida)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps({"resumo": resumo, "turnos": turnos}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n  gravado em {destino}")


asyncio.run(principal())
