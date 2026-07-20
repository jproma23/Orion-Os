#!/usr/bin/env python3
"""Gera o blog estatico do ORION OS a partir do diario de desenvolvimento.

Entrada:  docs/journal.md  (entradas comecando com "## <data> (assunto)")
Saida:    docs/index.html  + docs/posts/<slug>.html

Sem dependencia externa: o conversor de Markdown aqui cobre so o que o
journal realmente usa (titulos, listas, negrito, codigo inline e blocos).
Rodar:  python3 tools/build_blog.py
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
JOURNAL = RAIZ / "docs" / "journal.md"
SAIDA = RAIZ / "docs"

TITULO_SITE = "ORION OS"
SUBTITULO = "Diario de bordo da construcao de um robo autonomo"


@dataclass
class Post:
    data: str  # "2026-07-18"
    assunto: str  # texto entre parenteses no cabecalho, pode ser vazio
    corpo: str  # markdown cru da entrada
    slug: str

    @property
    def titulo(self) -> str:
        return self.assunto or f"Sessao de {self.data}"


def slugificar(texto: str) -> str:
    """Vira um nome de arquivo seguro: sem acento, sem simbolo, com hifens."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", sem_acento.lower())).strip("-")


def ler_posts() -> list[Post]:
    """Quebra o journal nas entradas '## data (assunto)'."""
    texto = JOURNAL.read_text(encoding="utf-8")
    # Cada entrada vai do seu '## ' ate o proximo '## ' (ou o fim do arquivo).
    partes = re.split(r"^## ", texto, flags=re.MULTILINE)[1:]

    posts: list[Post] = []
    vistos: set[str] = set()
    for parte in partes:
        cabecalho, _, corpo = parte.partition("\n")
        cabecalho = cabecalho.strip()
        # O assunto vem entre parenteses ou depois de um travessao; ambos aparecem.
        m = re.match(r"^(\d{4}-\d{2}-\d{2})\s*(?:\((.*)\)|[—-]\s*(.*))?$", cabecalho)
        if not m:
            continue
        data = m.group(1)
        assunto = (m.group(2) or m.group(3) or "").strip()

        slug = f"{data}-{slugificar(assunto)}" if assunto else data
        # Duas entradas podem ter data e assunto iguais; garante slug unico.
        base, n = slug, 2
        while slug in vistos:
            slug, n = f"{base}-{n}", n + 1
        vistos.add(slug)

        # A linha '---' que separa entradas no journal nao entra no post.
        corpo = corpo.strip().removesuffix("---").strip()
        posts.append(Post(data=data, assunto=assunto, corpo=corpo, slug=slug))

    posts.reverse()  # mais recente primeiro
    return posts


def inline(texto: str) -> str:
    """Negrito, codigo inline e links dentro de uma linha ja escapada."""
    texto = html.escape(texto)
    texto = re.sub(r"`([^`]+)`", r"<code>\1</code>", texto)
    texto = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", texto)
    texto = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', texto)
    return texto


def markdown_para_html(md: str) -> str:
    """Conversor minimo, suficiente para o formato do journal."""
    linhas = md.split("\n")
    saida: list[str] = []
    nivel_lista = 0  # quantos <ul> estao abertos
    em_codigo = False

    def fechar_listas(ate: int = 0) -> None:
        nonlocal nivel_lista
        while nivel_lista > ate:
            saida.append("</ul>")
            nivel_lista -= 1

    paragrafo: list[str] = []  # linhas de prosa ainda nao fechadas em <p>

    def fechar_paragrafo() -> None:
        if paragrafo:
            saida.append(f"<p>{inline(' '.join(paragrafo))}</p>")
            paragrafo.clear()

    def eh_tabela(i: int) -> bool:
        # Uma tabela e uma linha com | seguida da linha de tracos: |---|---|
        return (
            linhas[i].lstrip().startswith("|")
            and i + 1 < len(linhas)
            and re.match(r"^\s*\|[\s:|-]+\|\s*$", linhas[i + 1])
        )

    i = 0
    while i < len(linhas):
        linha = linhas[i]

        if linha.strip().startswith("```"):
            fechar_paragrafo()
            fechar_listas()
            saida.append("</pre>" if em_codigo else "<pre>")
            em_codigo = not em_codigo
            i += 1
            continue
        if em_codigo:
            saida.append(html.escape(linha))
            i += 1
            continue

        if not linha.strip():
            fechar_paragrafo()
            fechar_listas()
            i += 1
            continue

        if eh_tabela(i):
            fechar_paragrafo()
            fechar_listas()
            celulas = lambda ln: [c.strip() for c in ln.strip().strip("|").split("|")]
            cab = "".join(f"<th>{inline(c)}</th>" for c in celulas(linhas[i]))
            saida.append(f"<table><thead><tr>{cab}</tr></thead><tbody>")
            i += 2  # pula o cabecalho e a linha de tracos
            while i < len(linhas) and linhas[i].lstrip().startswith("|"):
                cols = "".join(f"<td>{inline(c)}</td>" for c in celulas(linhas[i]))
                saida.append(f"<tr>{cols}</tr>")
                i += 1
            saida.append("</tbody></table>")
            continue

        # ## vira h3: o h2 ja e o titulo do post, entao a hierarquia continua certa.
        if m := re.match(r"^(#{2,6})\s+(.*)$", linha):
            fechar_paragrafo()
            fechar_listas()
            n = min(len(m.group(1)) + 1, 6)
            saida.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
            i += 1
            continue

        if m := re.match(r"^(\s*)[-*]\s+(.*)$", linha):
            fechar_paragrafo()
            # Cada 2 espacos de indentacao = um nivel de aninhamento.
            alvo = len(m.group(1)) // 2 + 1
            while nivel_lista < alvo:
                saida.append("<ul>")
                nivel_lista += 1
            fechar_listas(alvo)
            saida.append(f"<li>{inline(m.group(2))}</li>")
            i += 1
            continue

        if nivel_lista:
            # Linha solta logo abaixo de um item: e continuacao dele.
            saida.append(f" {inline(linha.strip())}")
        else:
            # Prosa: acumula ate a linha em branco, para virar um paragrafo so.
            paragrafo.append(linha.strip())
        i += 1

    fechar_paragrafo()
    fechar_listas()
    if em_codigo:
        saida.append("</pre>")
    return "\n".join(saida)


CSS = """
:root{--bg:#fbfaf8;--fg:#1d1b19;--suave:#6b6560;--linha:#e3ded8;--realce:#b4501e}
@media(prefers-color-scheme:dark){
  :root{--bg:#16151a;--fg:#e8e4de;--suave:#9c948c;--linha:#2e2c33;--realce:#e08a4e}
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:16px/1.65 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:44rem;margin:0 auto;padding:3rem 1.25rem 5rem}
a{color:var(--realce)}
header.topo{border-bottom:1px solid var(--linha);padding-bottom:1.5rem;margin-bottom:2.5rem}
header.topo h1{margin:0;font-size:1.6rem;letter-spacing:-.02em}
header.topo p{margin:.4rem 0 0;color:var(--suave)}
.data{color:var(--suave);font-size:.85rem;text-transform:uppercase;letter-spacing:.06em}
ul.indice{list-style:none;padding:0;margin:0}
ul.indice li{padding:.9rem 0;border-bottom:1px solid var(--linha)}
ul.indice a{font-weight:600;text-decoration:none}
ul.indice a:hover{text-decoration:underline}
article h2{font-size:1.5rem;margin:.2rem 0 1.5rem;letter-spacing:-.02em}
article h3,article h4{margin:2rem 0 .5rem;font-size:1.05rem}
article li{margin:.35rem 0}
table{border-collapse:collapse;width:100%;margin:1.5rem 0;font-size:.92rem;
  display:block;overflow-x:auto}
th,td{border-bottom:1px solid var(--linha);padding:.55rem .7rem;text-align:left;
  vertical-align:top}
th{color:var(--suave);font-size:.8rem;text-transform:uppercase;letter-spacing:.05em}
code{background:var(--linha);padding:.12em .35em;border-radius:4px;font-size:.88em}
pre{background:var(--linha);padding:1rem;border-radius:8px;overflow-x:auto;font-size:.85rem}
pre code{background:none;padding:0}
.voltar{display:inline-block;margin-bottom:2rem;color:var(--suave);text-decoration:none}
.capa{margin:0 0 2.5rem;border-radius:10px;overflow:hidden;line-height:0}
.capa img{width:100%;height:auto;display:block}
.hero{margin:0 0 2.5rem}
.hero svg{width:100%;height:auto;color:var(--fg)}
.hero figcaption{color:var(--suave);font-size:.85rem;text-align:center;margin-top:.6rem}
.pecas{display:grid;grid-template-columns:repeat(auto-fit,minmax(9rem,1fr));
  gap:1.5rem;margin:0 0 3rem;padding:1.5rem 0;border-block:1px solid var(--linha)}
.peca{margin:0;text-align:center}
.peca svg{width:100%;max-width:11rem;height:auto;color:var(--fg);opacity:.9}
.peca figcaption{color:var(--suave);font-size:.8rem;margin-top:.5rem}
.peca b{display:block;color:var(--fg);font-size:.9rem}
.galeria{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));
  gap:1.25rem;margin:0 0 3rem}
.galeria figure{margin:0}
.galeria img{width:100%;height:15rem;object-fit:cover;border-radius:8px;
  background:var(--linha);display:block}
.galeria figcaption{color:var(--suave);font-size:.82rem;margin-top:.5rem}
.secao{font-size:.78rem;text-transform:uppercase;letter-spacing:.09em;
  color:var(--suave);margin:0 0 1rem}
footer{margin-top:4rem;padding-top:1.5rem;border-top:1px solid var(--linha);
  color:var(--suave);font-size:.85rem}
"""


def svg(nome: str) -> str:
    """Le um SVG de docs/assets e devolve pra ser embutido direto no HTML.

    Embutido (e nao via <img>) de proposito: assim o desenho herda a cor do
    texto pelo `currentColor` e acompanha o tema claro/escuro sozinho.
    """
    return (SAIDA / "assets" / f"{nome}.svg").read_text(encoding="utf-8")


def abertura() -> str:
    """Diagrama da corrente + as tres pecas, no topo do indice."""
    pecas = [
        ("notebook", "Notebook", "Mission Core — visao, voz, o rosto"),
        ("raspberry-pi", "Raspberry Pi 4B", "Motion Core — kernel e decisao"),
        ("arduino-mega", "Arduino Mega", "Firmware — motores e sensores"),
    ]
    cards = "\n".join(
        f'<figure class="peca">{svg(arq)}'
        f"<figcaption><b>{nome}</b>{papel}</figcaption></figure>"
        for arq, nome, papel in pecas
    )
    return (
        f'<div class="capa"><img src="assets/capa.webp" width="1600" height="638"'
        f' alt="Joao Paulo e o robo Sentinela, do projeto ORION OS"></div>\n'
        f'<figure class="hero">{svg("corrente")}'
        f"<figcaption>Tres computadores em corrente: cada um so fala com o "
        f"vizinho.</figcaption></figure>\n"
        f'<div class="pecas">{cards}</div>'
    )


def pagina_sobre() -> str:
    """Gera docs/sobre.html a partir de docs/sobre.md. Devolve o link do indice."""
    md = (SAIDA / "sobre.md").read_text(encoding="utf-8")
    titulo, _, corpo = md.partition("\n")
    miolo = (
        '<a class="voltar" href="index.html">&larr; todos os posts</a>\n'
        f'<article>\n<h2>{html.escape(titulo.lstrip("# "))}</h2>\n'
        f"{markdown_para_html(corpo)}\n</article>"
    )
    (SAIDA / "sobre.html").write_text(
        pagina(f"Sobre o {TITULO_SITE}", miolo), "utf-8"
    )
    return '<p class="secao"><a href="sobre.html">O que é o ORION OS →</a></p>'


def galeria() -> str:
    """Fotos do hardware real. So do projeto - nada de rosto de ninguem.

    As imagens em docs/assets/fotos passaram por tools/build_blog.py apenas
    depois de terem o EXIF removido (as originais tinham GPS).
    """
    fotos = [
        (
            "torre-camera-ultrassom",
            "A torre: webcam sobre o pan/tilt de dois servos e o HC-SR04 "
            "frontal logo abaixo.",
        ),
        (
            "chassi-mega-servos",
            "Por dentro: o Mega no chassi de acrilico, servos, ultrassom e "
            "o driver dos motores.",
        ),
        (
            "notebook-avatar-fofao",
            "O rosto do Fofao rodando no notebook - a mesma tela que vai "
            "montada no robo.",
        ),
    ]
    cards = "\n".join(
        f'<figure><img src="assets/fotos/{arq}.webp" alt="{html.escape(legenda)}"'
        f' loading="lazy" width="1400" height="1050">'
        f"<figcaption>{html.escape(legenda)}</figcaption></figure>"
        for arq, legenda in fotos
    )
    return f'<p class="secao">O bicho montado</p>\n<div class="galeria">{cards}</div>'


def pagina(titulo: str, miolo: str, prefixo: str = "") -> str:
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(titulo)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header class="topo">
  <h1><a href="{prefixo}index.html" style="color:inherit;text-decoration:none">{TITULO_SITE}</a></h1>
  <p>{SUBTITULO}</p>
</header>
{miolo}
<footer>Gerado a partir de <code>docs/journal.md</code> por
<code>tools/build_blog.py</code>.</footer>
</div>
</body>
</html>
"""


def main() -> None:
    posts = ler_posts()
    pasta_posts = SAIDA / "posts"
    pasta_posts.mkdir(parents=True, exist_ok=True)

    for post in posts:
        miolo = (
            f'<a class="voltar" href="../index.html">&larr; todos os posts</a>\n'
            f'<article>\n<p class="data">{post.data}</p>\n'
            f"<h2>{html.escape(post.titulo)}</h2>\n"
            f"{markdown_para_html(post.corpo)}\n</article>"
        )
        alvo = pasta_posts / f"{post.slug}.html"
        alvo.write_text(pagina(f"{post.titulo} — {TITULO_SITE}", miolo, "../"), "utf-8")

    itens = "\n".join(
        f'<li><span class="data">{p.data}</span><br>'
        f'<a href="posts/{p.slug}.html">{html.escape(p.titulo)}</a></li>'
        for p in posts
    )
    indice = (
        f'{abertura()}\n{pagina_sobre()}\n{galeria()}\n'
        f'<p class="secao">O diario, do mais novo ao mais antigo</p>\n'
        f'<ul class="indice">\n{itens}\n</ul>'
    )
    (SAIDA / "index.html").write_text(pagina(TITULO_SITE, indice), "utf-8")

    # Sem isso o GitHub Pages tenta processar tudo com Jekyll e ignora _pastas.
    (SAIDA / ".nojekyll").write_text("", "utf-8")

    print(f"{len(posts)} posts gerados em {SAIDA}")


if __name__ == "__main__":
    main()
