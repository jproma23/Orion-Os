"""Mission Brain: dado um sintoma em texto, que MODULO costuma ser?

Complementa a `mission_memory` (busca) com a pergunta que a busca textual
nao responde: generalizar a partir de COMO o problema e descrito, mesmo com
palavras que nunca apareceram juntas antes.

DE ONDE VEM O ROTULO, de graca: cada commit ja diz o que tocou.
`git log --name-only` da o par (mensagem -> arquivos), e o arquivo mapeia
para uma area. Ninguem rotulou nada a mao.

POR QUE PYTHON PURO, sem numpy/sklearn/torch: sao ~300 exemplos e ~2 mil
palavras. Uma rede desse tamanho treina em segundos com aritmetica de lista,
e assim a ferramenta roda em qualquer maquina do projeto sem instalar nada -
inclusive no Pi. O projeto ja pagou caro por dependencia pesada que so
servia de vez em quando (o Ollama, removido em 05/08).

A REDE, de verdade: TF-IDF esparso -> camada escondida (tanh) -> softmax,
treinada com descida de gradiente e entropia cruzada. A camada escondida e
o que a diferencia de um modelo linear; se ela nao ganhar do linear no
`avaliar`, ela nao esta ganhando nada e nao deve ser usada.

CEIFA HONESTA - leia antes de confiar na saida. ~300 exemplos e mais de dez
areas e POUCO. O `avaliar` existe para dizer o quanto: compara a rede com
dois adversarios - chutar sempre a classe mais comum, e a mesma rede SEM a
camada escondida. **Se nao ganhar dos dois, nao entra em nada.** O projeto ja
recusou o intent classifier por motivo parecido: busca errando com "nao
achei" e melhor que rede errando com confianca.

USO:

    python tools/mission_brain.py dados
    python tools/mission_brain.py avaliar
    python tools/mission_brain.py treinar
    python tools/mission_brain.py prever "o robo parou de falar"
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

CAMINHO_MODELO_PADRAO = Path("/mnt/ssd/orion/mission_brain.json")

#: Caminho -> area. A ordem importa: o primeiro que casa vence, entao os
#: mais especificos vem antes (motion_core/behavior antes de motion_core).
AREAS: tuple[tuple[str, str], ...] = (
    ("firmware/", "firmware"),
    ("motion_core/behavior/", "comportamento"),
    ("motion_core/navigation/", "navegacao"),
    ("motion_core/memory/", "memoria"),
    ("motion_core/webui/", "webui"),
    ("motion_core/", "motion_core"),
    ("src/orion/voice/", "voz"),
    ("src/orion/vision/", "visao"),
    ("src/orion/mission/", "missao"),
    ("src/orion/communication/", "comunicacao"),
    ("src/orion/kernel/", "kernel"),
    ("src/orion/", "orion_outros"),
    ("config/", "config"),
    ("tools/", "tools"),
)

#: Teste e doc acompanham a mudanca, nao sao o assunto dela. So viram
#: rotulo quando o commit nao tocou codigo nenhum.
ACOMPANHAM = ("tests/", "docs/")

_REGISTRO = "\x1e"
_CAMPO = "\x1f"


# ---------------------------------------------------------------- dados


@dataclass
class Exemplo:
    ref: str
    data: str
    texto: str
    area: str


def _sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )


def fatiar(texto: str) -> list[str]:
    """Palavras e bigramas, sem acento e em minusculas.

    Bigrama entra porque "sem eco" e "codigo orfao" dizem mais juntos do
    que separados - e neste projeto o vocabulario e bem consistente.
    """
    palavras = re.findall(r"[a-z0-9_]+", _sem_acento(texto.lower()))
    palavras = [p for p in palavras if len(p) > 2]
    # strict=False de proposito: as duas listas tem tamanhos diferentes
    # por construcao (a segunda e a primeira deslocada em um).
    bigramas = [f"{a}_{b}" for a, b in zip(palavras, palavras[1:], strict=False)]
    return palavras + bigramas


def _area_de(caminho: str) -> str | None:
    for prefixo, nome in AREAS:
        if caminho.startswith(prefixo):
            return nome
    return None


def _rotulo(arquivos: list[str]) -> str | None:
    codigo = [a for a in arquivos if not a.startswith(ACOMPANHAM)]
    alvo = codigo or arquivos
    areas = [a for a in (_area_de(c) for c in alvo) if a]
    if areas:
        return Counter(areas).most_common(1)[0][0]
    if any(a.startswith("docs/") for a in arquivos):
        return "docs"
    return None


def carregar(raiz: Path) -> list[Exemplo]:
    """Le o git uma vez e devolve (mensagem -> area).

    O separador de registro vai no INICIO do formato: com `--name-only` o
    git despeja os arquivos DEPOIS do formato, entao abrir o registro e o
    unico jeito de saber onde um acaba e o outro comeca.
    """
    formato = _REGISTRO + _CAMPO.join(["%h", "%ad", "%s", "%b"]) + _CAMPO
    bruto = subprocess.run(
        ["git", "log", f"--pretty=format:{formato}", "--name-only", "--date=short"],
        cwd=raiz,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout

    exemplos: list[Exemplo] = []
    for registro in bruto.split(_REGISTRO):
        if not registro.strip():
            continue
        campos = registro.split(_CAMPO)
        if len(campos) < 5:
            continue
        curto, data, assunto, corpo, cauda = campos[0], campos[1], campos[2], campos[3], campos[4]
        arquivos = [linha.strip() for linha in cauda.splitlines() if linha.strip()]
        area = _rotulo(arquivos)
        if area is None:
            continue
        texto = (assunto + "\n" + corpo).strip()
        if len(texto) < 15:
            continue
        exemplos.append(Exemplo(ref=curto, data=data, texto=texto, area=area))
    return exemplos


# ------------------------------------------------------------- features


class Vetorizador:
    """TF-IDF esparso. Guarda o vocabulario e o idf do TREINO.

    O idf tem de vir do treino e ser reaplicado no teste - recalcular no
    teste vazaria informacao do futuro e inflaria o acerto.
    """

    def __init__(self, min_df: int = 2) -> None:
        self.min_df = min_df
        self.indice: dict[str, int] = {}
        self.idf: list[float] = []

    def ajustar(self, textos: list[str]) -> None:
        documentos = [set(fatiar(t)) for t in textos]
        contagem = Counter(termo for doc in documentos for termo in doc)
        vocabulario = sorted(t for t, n in contagem.items() if n >= self.min_df)
        self.indice = {t: i for i, t in enumerate(vocabulario)}
        total = len(textos)
        self.idf = [
            math.log((1 + total) / (1 + contagem[t])) + 1.0 for t in vocabulario
        ]

    def transformar(self, texto: str) -> dict[int, float]:
        termos = [t for t in fatiar(texto) if t in self.indice]
        if not termos:
            return {}
        tf = Counter(termos)
        vetor = {
            self.indice[t]: (1 + math.log(n)) * self.idf[self.indice[t]]
            for t, n in tf.items()
        }
        norma = math.sqrt(sum(v * v for v in vetor.values())) or 1.0
        return {i: v / norma for i, v in vetor.items()}


# ---------------------------------------------------------------- rede


class Rede:
    """Uma camada escondida com tanh e saida softmax.

    Entrada esparsa: so os pesos das palavras PRESENTES sao lidos e
    atualizados. E isso que deixa Python puro viavel - um commit ativa ~100
    das ~2000 entradas, entao cada passo custa 100xH e nao 2000xH.
    """

    def __init__(self, n_entradas: int, n_escondidas: int, classes: list[str], semente: int = 0):
        self.classes = classes
        self.n_escondidas = n_escondidas
        rng = random.Random(semente)
        # Xavier: manter a variancia estavel entre camadas
        limite1 = math.sqrt(6.0 / (n_entradas + n_escondidas)) if n_escondidas else 0.0
        limite2 = math.sqrt(6.0 / (n_escondidas + len(classes)))
        self.w1 = [
            [rng.uniform(-limite1, limite1) for _ in range(n_escondidas)]
            for _ in range(n_entradas)
        ]
        self.b1 = [0.0] * n_escondidas
        largura = n_escondidas if n_escondidas else n_entradas
        self.w2 = [
            [rng.uniform(-limite2, limite2) for _ in range(len(classes))]
            for _ in range(largura)
        ]
        self.b2 = [0.0] * len(classes)

    # -- frente

    def _escondida(self, x: dict[int, float]) -> list[float]:
        if not self.n_escondidas:
            return []
        h = list(self.b1)
        for indice, valor in x.items():
            linha = self.w1[indice]
            for j in range(self.n_escondidas):
                h[j] += valor * linha[j]
        return [math.tanh(v) for v in h]

    def _saida(self, x: dict[int, float], h: list[float]) -> list[float]:
        o = list(self.b2)
        if self.n_escondidas:
            for j, hj in enumerate(h):
                if hj == 0.0:
                    continue
                linha = self.w2[j]
                for k in range(len(o)):
                    o[k] += hj * linha[k]
        else:
            # sem camada escondida = regressao softmax (o adversario linear)
            for indice, valor in x.items():
                linha = self.w2[indice]
                for k in range(len(o)):
                    o[k] += valor * linha[k]
        return o

    @staticmethod
    def _softmax(o: list[float]) -> list[float]:
        maior = max(o)
        exp = [math.exp(v - maior) for v in o]
        soma = sum(exp)
        return [v / soma for v in exp]

    def prever(self, x: dict[int, float]) -> list[float]:
        return self._softmax(self._saida(x, self._escondida(x)))

    # -- treino

    def treinar(
        self,
        amostras: list[tuple[dict[int, float], int]],
        epocas: int = 40,
        taxa: float = 0.5,
        semente: int = 0,
    ) -> None:
        rng = random.Random(semente)
        n_classes = len(self.classes)
        ordem = list(range(len(amostras)))
        for epoca in range(epocas):
            rng.shuffle(ordem)
            # taxa decrescente: passos grandes no comeco, finos no fim
            passo = taxa * (1.0 - epoca / (epocas + 1))
            for indice_amostra in ordem:
                x, alvo = amostras[indice_amostra]
                if not x:
                    continue
                h = self._escondida(x)
                p = self._softmax(self._saida(x, h))

                # d(entropia cruzada)/d(saida) = p - y
                do = p[:]
                do[alvo] -= 1.0

                if self.n_escondidas:
                    dh = [0.0] * self.n_escondidas
                    for j in range(self.n_escondidas):
                        linha = self.w2[j]
                        hj = h[j]
                        for k in range(n_classes):
                            dh[j] += linha[k] * do[k]
                            linha[k] -= passo * hj * do[k]
                    for k in range(n_classes):
                        self.b2[k] -= passo * do[k]

                    # derivada do tanh
                    dpre = [dh[j] * (1.0 - h[j] * h[j]) for j in range(self.n_escondidas)]
                    for indice, valor in x.items():
                        linha = self.w1[indice]
                        for j in range(self.n_escondidas):
                            linha[j] -= passo * valor * dpre[j]
                    for j in range(self.n_escondidas):
                        self.b1[j] -= passo * dpre[j]
                else:
                    for indice, valor in x.items():
                        linha = self.w2[indice]
                        for k in range(n_classes):
                            linha[k] -= passo * valor * do[k]
                    for k in range(n_classes):
                        self.b2[k] -= passo * do[k]


# ------------------------------------------------------------ pipeline


class Modelo:
    def __init__(self, n_escondidas: int = 32, semente: int = 0) -> None:
        self.vetorizador = Vetorizador()
        self.n_escondidas = n_escondidas
        self.semente = semente
        self.rede: Rede | None = None

    def ajustar(self, textos: list[str], areas: list[str], epocas: int = 40) -> None:
        self.vetorizador.ajustar(textos)
        classes = sorted(set(areas))
        posicao = {c: i for i, c in enumerate(classes)}
        self.rede = Rede(
            len(self.vetorizador.indice), self.n_escondidas, classes, semente=self.semente
        )
        amostras = [
            (self.vetorizador.transformar(t), posicao[a])
            for t, a in zip(textos, areas, strict=True)
        ]
        self.rede.treinar(amostras, epocas=epocas, semente=self.semente)

    def probabilidades(self, texto: str) -> list[tuple[str, float]]:
        assert self.rede is not None, "modelo nao treinado"
        p = self.rede.prever(self.vetorizador.transformar(texto))
        return sorted(zip(self.rede.classes, p, strict=True), key=lambda par: -par[1])

    def prever(self, texto: str) -> str:
        return self.probabilidades(texto)[0][0]

    # -- persistencia em JSON: legivel, e sem pickle (que executa codigo ao ler)

    def para_json(self) -> dict:
        assert self.rede is not None
        return {
            "vocabulario": self.vetorizador.indice,
            "idf": self.vetorizador.idf,
            "n_escondidas": self.n_escondidas,
            "classes": self.rede.classes,
            "w1": self.rede.w1,
            "b1": self.rede.b1,
            "w2": self.rede.w2,
            "b2": self.rede.b2,
        }

    @classmethod
    def de_json(cls, dados: dict) -> Modelo:
        modelo = cls(n_escondidas=dados["n_escondidas"])
        modelo.vetorizador.indice = dados["vocabulario"]
        modelo.vetorizador.idf = dados["idf"]
        rede = Rede.__new__(Rede)
        rede.classes = dados["classes"]
        rede.n_escondidas = dados["n_escondidas"]
        rede.w1, rede.b1 = dados["w1"], dados["b1"]
        rede.w2, rede.b2 = dados["w2"], dados["b2"]
        modelo.rede = rede
        return modelo


# ------------------------------------------------------------- comandos


def mostrar_dados(raiz: Path) -> int:
    exemplos = carregar(raiz)
    contagem = Counter(e.area for e in exemplos)
    total = len(exemplos)
    if not total:
        print("nenhum commit com area identificada")
        return 1
    print(f"{total} commits com area identificada\n")
    maior = max(contagem.values())
    for area, quantos in contagem.most_common():
        barra = "#" * round(quantos / maior * 30)
        print(f"  {area:16s} {quantos:4d}  {quantos / total:5.1%}  {barra}")
    print(f"\n  classe mais comum sozinha = {maior / total:.1%}")
    print("  esse e o numero a bater - abaixo dele a rede nao vale nada")
    return 0


def avaliar(raiz: Path, epocas: int = 40) -> int:
    """Divide por TEMPO: treina no passado, testa no futuro.

    Divisao aleatoria inflaria o numero - commits do mesmo dia falam do
    mesmo assunto e metade do par cairia em cada lado. O uso real e sempre
    "aprendi ate hoje, me pergunta amanha".
    """
    exemplos = carregar(raiz)
    if len(exemplos) < 40:
        print(f"exemplos de menos ({len(exemplos)})")
        return 1

    exemplos.sort(key=lambda e: e.data)
    corte = int(len(exemplos) * 0.8)
    treino, teste = exemplos[:corte], exemplos[corte:]
    vistas = {e.area for e in treino}
    impossiveis = sum(1 for e in teste if e.area not in vistas)

    x_treino = [e.texto for e in treino]
    y_treino = [e.area for e in treino]

    print(f"treino {len(treino)}   teste {len(teste)}   classes {len(vistas)}")
    if impossiveis:
        print(f"  {impossiveis} do teste sao de classe que o treino nunca viu - o teto nao e 100%")
    comum = Counter(y_treino).most_common(1)[0][0]
    acerto_burro = sum(1 for e in teste if e.area == comum) / len(teste)
    print(f"\n  {'chutar a mais comum':24s} {acerto_burro:6.1%}")

    for nome, escondidas in (("linear (sem escondida)", 0), ("REDE (32 escondidas)", 32)):
        modelo = Modelo(n_escondidas=escondidas)
        modelo.ajustar(x_treino, y_treino, epocas=epocas)
        acertos = top3 = 0
        for e in teste:
            ordenado = modelo.probabilidades(e.texto)
            if ordenado[0][0] == e.area:
                acertos += 1
            if e.area in [c for c, _ in ordenado[:3]]:
                top3 += 1
        print(
            f"  {nome:24s} {acertos / len(teste):6.1%}   top-3 {top3 / len(teste):5.1%}"
        )
    return 0


def treinar(raiz: Path, caminho: Path, epocas: int = 40) -> int:
    exemplos = carregar(raiz)
    modelo = Modelo()
    modelo.ajustar([e.texto for e in exemplos], [e.area for e in exemplos], epocas=epocas)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(modelo.para_json()), encoding="utf-8")
    print(f"treinado em {len(exemplos)} commits -> {caminho}")
    tamanho = caminho.stat().st_size / 1024
    print(f"  vocabulario {len(modelo.vetorizador.indice)}   {tamanho:.0f} KB")
    return 0


def prever(caminho: Path, sintoma: str, quantos: int = 3) -> int:
    if not caminho.exists():
        print(f"sem modelo em {caminho} - rode `treinar`", file=sys.stderr)
        return 1
    modelo = Modelo.de_json(json.loads(caminho.read_text(encoding="utf-8")))
    print(f"  {sintoma!r}")
    for area, prob in modelo.probabilidades(sintoma)[:quantos]:
        print(f"    {area:16s} {prob:5.1%}")
    return 0


def main(argv: list[str] | None = None) -> int:
    raiz = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--modelo", type=Path, default=CAMINHO_MODELO_PADRAO)
    parser.add_argument("--epocas", type=int, default=40)
    sub = parser.add_subparsers(dest="acao", required=True)
    sub.add_parser("dados")
    sub.add_parser("avaliar")
    sub.add_parser("treinar")
    p = sub.add_parser("prever")
    p.add_argument("sintoma", nargs="+")

    args = parser.parse_args(argv)
    if args.acao == "dados":
        return mostrar_dados(raiz)
    if args.acao == "avaliar":
        return avaliar(raiz, epocas=args.epocas)
    if args.acao == "treinar":
        return treinar(raiz, args.modelo, epocas=args.epocas)
    return prever(args.modelo, " ".join(args.sintoma))


if __name__ == "__main__":
    raise SystemExit(main())
