#!/usr/bin/env python3
"""Produz RASCUNHO de aula a partir da Wikipedia, para os topicos da ementa.

    .venv/bin/python tools/gerar_aula.py --topico mat.funcao-afim
    .venv/bin/python tools/gerar_aula.py --materia fisica --nivel basico --limite 3
    .venv/bin/python tools/gerar_aula.py --materia biologia --limite 24

Pedido do dono em 2026-08-07: *"vamos produzir os conteudos direto da
internet e adicionar"*. A ementa ja diz o que falta - 312 topicos, 4 com
aula - e este comando ataca essa lista.

## RASCUNHO, e nao aula publicada. A distincao e o coracao do arquivo.

A saida vai para `content/rascunhos/`, NUNCA direto para `content/aulas/`.
Publicar e um segundo comando, `--publicar`, depois de alguem ler.

O motivo nao e burocracia. Wikipedia entrega FATO, nao DIDATICA - e o
proprio dono ja reprovou o resultado de conteudo raso: *"a aula esta rapida
e muito mal explicado"*. Um artigo picotado em blocos e uma enciclopedia
lida em voz alta, que e pior que nao ter aula: parece ensino e nao ensina.
O rascunho serve para (a) ter o material reunido e com procedencia, e (b)
ser melhorado por quem sabe - a mao, ou por UMA passada de modelo, uma vez
por aula e nao a cada vez que alguem pede.

## Procedencia colada, sempre

Titulo do artigo, URL e data ficam no `fonte` da aula, e o robo FALA isso no
fechamento. Conteudo sem procedencia e a coisa que este projeto decidiu nao
produzir.

## A licenca importa e esta escrita aqui

O texto da Wikipedia e CC BY-SA. Citar a fonte nao e cortesia: e a condicao
de uso. Por isso `fonte` nao e campo opcional em aula gerada.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import re
import sys
import unicodedata
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from orion.ensino.ementa import carregar_pasta as carregar_ementas  # noqa: E402
from orion.mission.internet import _USER_AGENT, ConsultaInternet  # noqa: E402

PASTA_EMENTAS = RAIZ / "content" / "ementas"
PASTA_RASCUNHOS = RAIZ / "content" / "rascunhos"
PASTA_AULAS = RAIZ / "content" / "aulas"

#: Quantos trechos do artigo viram passo. Mais que isto vira leitura de
#: enciclopedia; menos nao da uma aula.
MAXIMO_PASSOS = 8

#: Tamanho de cada trecho falado. O Piper fala ~15 palavras em 6 segundos, e
#: um paragrafo longo demais no quadro nao e lido de longe.
MAXIMO_CARACTERES_PASSO = 420


def _frases(texto: str) -> list[str]:
    """Parte em frases pelo ponto final, poupando abreviacoes comuns."""
    protegido = re.sub(r"\b(Sr|Sra|Dr|Prof|etc|ex|séc|séculos?)\.", r"\1<PONTO>", texto)
    partes = re.split(r"(?<=[.!?])\s+", protegido)
    return [p.replace("<PONTO>", ".").strip() for p in partes if p.strip()]


def _blocos(texto: str) -> list[str]:
    """Junta frases em blocos do tamanho de um passo."""
    blocos, atual = [], ""
    for frase in _frases(texto):
        if len(atual) + len(frase) + 1 > MAXIMO_CARACTERES_PASSO and atual:
            blocos.append(atual.strip())
            atual = frase
        else:
            atual = f"{atual} {frase}".strip()
    if atual:
        blocos.append(atual.strip())
    return blocos


#: Secoes que nao viram aula. Sao as do fim de todo artigo da Wikipedia.
_SECOES_DESCARTAVEIS = re.compile(
    r"^(ver tambem|referencias|ligacoes externas|bibliografia|notas|"
    r"leitura adicional|galeria)$",
    re.IGNORECASE,
)


async def _artigo_completo(titulo: str) -> str:
    """O texto INTEIRO do artigo, e nao o resumo.

    O `ConsultaInternet.consultar` devolve o resumo curto - certo para
    responder uma pergunta falada, e curto demais para uma aula: o primeiro
    teste deste comando produziu uma aula de DOIS passos. Aula precisa do
    corpo do artigo, entao aqui se chama a api.php com `explaintext`.

    Nao mexi no `internet.py` de proposito: ele esta em producao respondendo
    perguntas, e resumo curto e exatamente o que ele deve devolver la.
    """
    import httpx

    parametros = {
        "action": "query", "prop": "extracts", "explaintext": "1",
        "format": "json", "titles": titulo, "redirects": "1",
    }
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as cliente:
        resposta = await cliente.get(
            "https://pt.wikipedia.org/w/api.php",
            params=parametros,
            # O MESMO User-Agent do internet.py, e nao um novo: a Wikimedia
            # devolve 403 sem contato dentro dele. Ja esta documentado la, e
            # esta versao inventou o proprio - e tomou 403 em todas as
            # chamadas do primeiro teste.
            headers={"User-Agent": _USER_AGENT},
        )
        resposta.raise_for_status()
        paginas = (resposta.json().get("query") or {}).get("pages") or {}
    for pagina in paginas.values():
        texto = pagina.get("extract") or ""
        if texto:
            # Corta o rodape do artigo: referencias e ligacoes externas nao
            # ensinam nada e enchem a aula de lixo.
            linhas = []
            for linha in texto.splitlines():
                titulo_secao = linha.strip().strip("= ").strip()
                if linha.startswith("==") and _SECOES_DESCARTAVEIS.match(titulo_secao):
                    break
                linhas.append(linha)
            return "\n".join(linhas)
    return ""


def _termos_alternativos(topico) -> list[str]:
    """Outros nomes para procurar quando o titulo nao e um artigo.

    Titulo de aula e pedagogico ("O que e estar vivo"), e a Wikipedia nao
    tem artigo com esse nome - as duas primeiras tentativas do primeiro teste
    falharam exatamente assim. As palavras do `conteudo` da ementa sao o
    plano B, porque la estao os TERMOS ("fotossintese", "celula").
    """
    termos = [topico.titulo]
    # O titulo sem a parte pedagogica: "Genetica - as leis de Mendel" -> os
    # dois lados; "Funcao afim (do primeiro grau)" -> sem o parenteses.
    limpo = re.sub(r"\(.*?\)", "", topico.titulo)
    for parte in re.split(r"\s*[-–—]\s*", limpo):
        parte = parte.strip()
        if parte and parte not in termos and len(parte) > 3:
            termos.append(parte)
    for palavra in re.findall(r"\b[A-Za-zÀ-ÿ]{6,}\b", topico.conteudo or ""):
        if palavra.lower() not in " ".join(termos).lower():
            termos.append(palavra)
    return termos[:6]


def _parecidos(termo: str, titulo: str) -> bool:
    """O titulo achado divide alguma palavra de peso com o que se procurou?"""
    def palavras(texto: str) -> set[str]:
        # SEM ACENTO nos dois lados: a primeira versao descartou "Célula"
        # por ter procurado "celula", que e como a ementa escreve.
        limpo = "".join(
            c for c in unicodedata.normalize("NFD", texto.lower())
            if unicodedata.category(c) != "Mn"
        )
        return {p for p in re.findall(r"\w+", limpo) if len(p) > 3}
    procuradas = palavras(termo)
    return bool(procuradas & palavras(titulo)) if procuradas else False


#: Palavras do `conteudo` da ementa que nao sao termo a ensinar.
_LIGACAO = frozenset({
    "e", "ou", "de", "do", "da", "dos", "das", "com", "sem", "para", "por",
    "que", "como", "quando", "onde", "no", "na", "nos", "nas", "um", "uma",
    "o", "a", "os", "as", "seus", "suas", "cada", "todo", "toda", "mais",
    "ja", "ao", "aos", "se", "sao", "ser", "tem", "entre", "sobre", "isso",
})


def _termos_do_conteudo(topico) -> list[str]:
    """Os TERMOS que a aula promete ensinar, tirados da ementa.

    ACHADO em 07/08, comparando o rascunho com o proprio objetivo: a aula de
    Cinematica prometia "velocidade, aceleracao, movimento uniforme e
    graficos" e o texto gerado falava de analise cinematica na engenharia e
    dos graus de liberdade de uma mosca - nunca chegava em velocidade.

    A causa e estrutural: artigo de enciclopedia e organizado por assunto, e
    aula e organizada por OBJETIVO. Buscar um artigo por termo do objetivo
    inverte isso - e os termos ja estao escritos no `conteudo` da ementa.
    """
    bruto = re.split(r"[;,.]", topico.conteudo or "")
    termos = []
    for pedaco in bruto:
        palavras = [p for p in pedaco.split() if p.lower() not in _LIGACAO]
        if not palavras:
            continue
        termo = " ".join(palavras[:4]).strip()
        if len(termo) > 4 and termo.lower() not in {t.lower() for t in termos}:
            termos.append(termo)
    return termos[:5]


async def _introducao(consulta: ConsultaInternet, termo: str, contexto: str = ""):
    """O artigo do termo, so a INTRODUCAO.

    A introducao e a parte didatica de um artigo da Wikipedia: ela define a
    coisa em duas ou tres frases. O corpo desce para historia, aplicacoes e
    casos particulares, que e onde a aula se perdia.
    """
    # O TERMO COM O CONTEXTO DA AULA PRIMEIRO. Termo generico sozinho pesca
    # o artigo errado: "graficos" trouxe GRAFICOS ISOMETRICOS EM VIDEOJOGOS
    # para uma aula de cinematica. Quem procura sabe do que esta falando, e
    # o titulo da aula e essa informacao.
    achado = None
    for tentativa in ([f"{termo} {contexto}"] if contexto else []) + [termo]:
        candidato = await consulta.consultar(tentativa)
        if candidato is not None and _parecidos(termo, candidato.titulo):
            achado = candidato
            break
    if achado is None:
        # Melhor a aula sem esta parte do que com a parte errada: uma secao
        # a menos e um buraco visivel; a secao errada passa por conteudo.
        return None, ""
    try:
        texto = await _artigo_completo(achado.titulo)
    except Exception:
        texto = achado.trecho
    # Antes do primeiro "== titulo ==" esta a introducao.
    introducao = re.split(r"\n=+ .+ =+", texto)[0].strip() or achado.trecho
    return achado, introducao


async def _buscar(consulta: ConsultaInternet, topico):
    """Acha o artigo e devolve (achado, texto completo)."""
    for termo in _termos_alternativos(topico):
        achado = await consulta.consultar(termo)
        if achado is None:
            continue
        # O artigo achado precisa ter A VER com o que se procurou. Sem esta
        # conferencia, "O que e estar vivo" trouxe "Organizacao das Nacoes
        # Unidas" - a busca por termo solto encontra qualquer coisa, e uma
        # aula de biologia sobre a ONU e pior que nenhuma aula.
        if not _parecidos(termo, achado.titulo):
            print(f"    (descartado - procurei {termo!r} e veio {achado.titulo!r})")
            continue
        try:
            texto = await _artigo_completo(achado.titulo)
        except Exception as erro:  # noqa: BLE001
            print(f"    (artigo completo falhou para {achado.titulo!r}- {erro})")
            texto = ""
        return achado, texto or achado.trecho
    return None, ""


def _montar_rascunho(topico, materia: str, nivel: str, achado, secoes) -> dict:
    """Monta o rascunho: abertura + uma parte por TERMO do objetivo.

    `secoes` e uma lista de (termo, texto, achado) - um artigo por termo que
    a ementa promete ensinar. E a diferenca entre uma aula e um artigo lido
    em voz alta: a ordem vem do objetivo, nao do indice da enciclopedia.
    """
    hoje = datetime.date.today().isoformat()
    passos = [
        {
            "fala": f"Hoje o assunto e {topico.titulo}.",
            "quadro": {"tipo": "titulo", "texto": topico.titulo},
        }
    ]
    fontes = []
    if achado is not None:
        abertura = _blocos(achado.trecho)[:1]
        for parte in abertura:
            passos.append({"fala": parte, "quadro": {"tipo": "texto", "texto": parte}})
        fontes.append(achado.titulo)

    for termo, texto, achado_termo in secoes:
        passos.append(
            {
                "fala": f"Vamos ao proximo ponto - {termo}.",
                "quadro": {"tipo": "titulo", "texto": termo},
            }
        )
        for parte in _blocos(texto)[:2]:
            passos.append({"fala": parte, "quadro": {"tipo": "texto", "texto": parte}})
        if achado_termo is not None and achado_termo.titulo not in fontes:
            fontes.append(achado_termo.titulo)

    perguntas = [f"Com as suas palavras, o que e {s[0]}?" for s in secoes[:2]]
    perguntas.append("O que ficou confuso e voce quer que eu repita?")

    return {
        "id": f"{materia}.{topico.id.split('.', 1)[-1]}.{nivel}",
        "materia": materia,
        "titulo": topico.titulo,
        "nivel": nivel,
        "resumo": topico.conteudo or f"Aula sobre {topico.titulo}.",
        "fonte": (
            "Wikipedia, artigos "
            + ", ".join(fontes)
            + f", consultados em {hoje}"
        ),
        "plano": {
            "objetivo": topico.conteudo or f"Entender o que e {topico.titulo}.",
            "pre_requisitos": [],
            "duracao_min": max(5, len(passos) * 2),
            "fixacao": perguntas,
            "fechamento": (
                f"Isso e o basico de {topico.titulo}. "
                "Se quiser, eu repito a parte que ficou confusa."
            ),
        },
        "passos": passos,
    }


async def principal_async(argumentos) -> int:
    ementas = carregar_ementas(PASTA_EMENTAS)
    alvos = []
    for ementa in ementas:
        if argumentos.materia and ementa.materia != argumentos.materia:
            continue
        for nivel, topicos in ementa.topicos.items():
            if argumentos.nivel and nivel != argumentos.nivel:
                continue
            for topico in topicos:
                if argumentos.topico and topico.id != argumentos.topico:
                    continue
                # Topico que ja tem aula escrita nao e regerado: o rascunho
                # sobrescreveria trabalho humano por texto de enciclopedia.
                if topico.aula and not argumentos.forcar:
                    continue
                alvos.append((ementa.materia, nivel, topico))

    if not alvos:
        print("nenhum topico a gerar (todos ja tem aula? confira --materia e --nivel)")
        return 1

    alvos = alvos[: argumentos.limite]
    PASTA_RASCUNHOS.mkdir(parents=True, exist_ok=True)
    consulta = ConsultaInternet()
    feitos = falhas = 0

    for materia, nivel, topico in alvos:
        achado, _ = await _buscar(consulta, topico)
        # Uma busca por TERMO do objetivo, e nao um dump do artigo guarda-chuva.
        secoes = []
        for termo in _termos_do_conteudo(topico):
            achado_termo, introducao = await _introducao(consulta, termo, topico.titulo)
            if introducao:
                secoes.append((termo, introducao, achado_termo))
        if achado is None and not secoes:
            print(f"  SEM FONTE  {topico.id:28} {topico.titulo}")
            falhas += 1
            continue
        rascunho = _montar_rascunho(topico, materia, nivel, achado, secoes)
        destino = PASTA_RASCUNHOS / f"{rascunho['id']}.yaml"
        destino.write_text(
            yaml.safe_dump(rascunho, allow_unicode=True, sort_keys=False, width=88),
            encoding="utf-8",
        )
        print(f"  rascunho   {topico.id:28} {len(rascunho['passos'])} passos -> {destino.name}")
        feitos += 1

    print(f"\n{feitos} rascunhos em content/rascunhos, {falhas} sem fonte na Wikipedia")
    if feitos:
        print(
            "LEIA ANTES DE PUBLICAR. Wikipedia entrega fato, nao didatica - "
            "publicar sem ler produz a aula rasa que o dono ja reprovou.\n"
            "Depois de revisar: tools/gerar_aula.py --publicar <id>"
        )
    return 0


def publicar(id_aula: str) -> int:
    origem = PASTA_RASCUNHOS / f"{id_aula}.yaml"
    if not origem.exists():
        print(f"nao existe rascunho: {origem}", file=sys.stderr)
        return 1
    destino = PASTA_AULAS / f"{id_aula.replace('.', '-')}.yaml"
    destino.write_text(origem.read_text(encoding="utf-8"), encoding="utf-8")
    origem.unlink()
    print(f"publicada em {destino.relative_to(RAIZ)}")
    print("Agora rode: tools/aulas.py construir  (e aponte o topico da ementa para esta aula)")
    return 0


def principal() -> int:
    analisador = argparse.ArgumentParser(description=__doc__)
    analisador.add_argument("--topico", help="id do topico na ementa")
    analisador.add_argument("--materia")
    analisador.add_argument("--nivel")
    analisador.add_argument("--limite", type=int, default=5)
    analisador.add_argument(
        "--forcar", action="store_true", help="regera mesmo se o topico ja tiver aula"
    )
    analisador.add_argument("--publicar", metavar="ID", help="move um rascunho revisado")
    argumentos = analisador.parse_args()

    if argumentos.publicar:
        return publicar(argumentos.publicar)
    return asyncio.run(principal_async(argumentos))


if __name__ == "__main__":
    raise SystemExit(principal())
