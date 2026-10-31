"""Vault de conhecimento em Obsidian (Cap 11, EDR-0021).

Memoria de longo prazo em formato livre - fatos, reflexoes, resumos - que
nao cabem bem na tabela `conhecimento` (so pares chave/valor simples). Cada
nota e um arquivo `.md` num vault Obsidian de verdade (pasta com wikilinks
`[[assim]]`), dentro do SSD do Raspberry - mesmo disco do `orion.db` (Cap
15). Unico ponto de acesso ao diretorio (regra 5 do ARQUITETURA.txt: nenhum
modulo abre arquivo/banco direto - so via esta classe, exposta ao Notebook
pelos comandos `memory.nota_escrever`/`memory.nota_buscar`, ver bridge.py).

Busca por enquanto e texto simples (titulo + conteudo) - embeddings/RAG
ficam para depois, so se a qualidade nao bastar (EDR-0021).
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

logger = logging.getLogger("motion_core.memory.vault")

_CARACTERES_INVALIDOS_ARQUIVO = re.compile(r'[\\/:*?"<>|]')

#: Palavra com menos que isto casa dentro de outras por acidente ("sol" dentro
#: de "resolver") e traz nota nenhuma a ver com a pergunta.
_MINIMO_LETRAS_TERMO = 4

#: Palavras que aparecem em QUALQUER nota - buscar por elas e o mesmo que nao
#: buscar. So entram aqui as que passam do tamanho minimo acima; "de", "o" e
#: "que" ja caem pelo tamanho.
#:
#: A lista e curta de proposito. Uma lista grande de palavras vazias comeca a
#: derrubar palavra util ("casa", "hora", "gente") e o robo passa a nao achar o
#: que ele mesmo guardou - que e o defeito que esta funcao acabou de sair.
_PALAVRAS_VAZIAS = frozenset(
    {
        "para", "pelo", "pela", "como", "quando", "onde", "isso", "isto",
        "aquilo", "esse", "essa", "este", "esta", "voce", "vocie", "jarvis",
        "meu", "minha", "seu", "sua", "nosso", "nossa", "sobre", "entao",
        "mais", "menos", "muito", "pouco", "aqui", "sabe", "tem", "esta",
        "estao", "sera", "foi", "era", "sao", "com", "sem", "dos", "das",
        "nos", "nas", "num", "numa", "pra", "por", "que", "mas", "porque",
    }
)


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn")


def _termos_de_busca(consulta: str) -> list[str]:
    """A fala da pessoa vira as palavras que valem procurar.

    Devolve lista VAZIA quando nao sobra nada - e quem chama tem que tratar
    isso como "nao ha o que buscar", e nao como "buscar tudo". Uma pergunta
    feita so de palavras de ligacao ("e o que voce acha?") nao deve trazer
    todas as notas do vault.
    """
    palavras = re.findall(r"[a-z0-9]+", _sem_acento(consulta))
    vistas: list[str] = []
    for palavra in palavras:
        if len(palavra) < _MINIMO_LETRAS_TERMO or palavra in _PALAVRAS_VAZIAS:
            continue
        if palavra not in vistas:
            vistas.append(palavra)
    return vistas


def _nome_arquivo(titulo: str) -> str:
    """Converte um titulo em nome de arquivo seguro - Obsidian usa o nome
    do arquivo como titulo da nota, entao mantem o texto legivel."""
    seguro = _CARACTERES_INVALIDOS_ARQUIVO.sub("", titulo).strip()
    if not seguro:
        raise ValueError("Titulo da nota nao pode ficar vazio apos sanitizar")
    return f"{seguro}.md"


class VaultConhecimento:
    """Le/escreve notas de um vault Obsidian dentro do SSD."""

    def __init__(self, diretorio: str | Path) -> None:
        self._diretorio = Path(diretorio)
        self._diretorio.mkdir(parents=True, exist_ok=True)

    def escrever_nota(self, titulo: str, conteudo: str, links: list[str] | None = None) -> str:
        """Cria ou sobrescreve uma nota. `links` vira wikilinks Obsidian
        (`[[outra nota]]`) numa secao "Relacionado" no fim do arquivo.
        Retorna o nome do arquivo criado."""
        caminho = self._diretorio / _nome_arquivo(titulo)
        texto = conteudo.strip()
        if links:
            relacionados = "\n".join(f"- [[{link}]]" for link in links)
            texto += f"\n\n## Relacionado\n{relacionados}\n"
        caminho.write_text(texto, encoding="utf-8")
        logger.info("Nota gravada: %s", caminho.name)
        return caminho.name

    def ler_nota(self, titulo: str) -> str | None:
        caminho = self._diretorio / _nome_arquivo(titulo)
        if not caminho.exists():
            return None
        return caminho.read_text(encoding="utf-8")

    def buscar(self, consulta: str, limite: int = 5) -> list[dict[str, str]]:
        """Notas relevantes para uma PERGUNTA, ordenadas por quantas palavras batem.

        ## O defeito que isto conserta (achado em 2026-08-04)

        A versao anterior procurava a consulta INTEIRA como substring:

            if termo in caminho.stem.lower() or termo in conteudo.lower():

        E quem chama e o `mission_planner`, passando a fala inteira da pessoa
        (`nota_buscar(texto_usuario)`). Ou seja: *"Jarvis, o portao dos fundos
        ta travando?"* so acharia uma nota que contivesse essa frase exata,
        letra por letra. **Nunca achava nada.**

        Os testes nao pegaram porque todos buscavam UMA palavra ("kamal",
        "pizza") - a consulta de teste nunca se parecia com a consulta real.
        Este e o mesmo defeito de ter um vault com caminho de leitura ligado e
        nenhuma nota dentro: parece funcionar porque o vazio nao reclama.

        ## Como funciona agora

        Casa PALAVRA a palavra e ordena por quantas bateram. Palavra curta e
        palavra de ligacao ficam de fora - "o", "de", "que" estao em toda nota
        e casariam com tudo, o que da no mesmo que nao buscar.

        Sem acento dos dois lados: "portao" tem que achar "portão". E o mesmo
        criterio que o resto do robo ja usa para nomes de pessoas.

        Ainda e busca por texto, nao por significado (embeddings/RAG ficam para
        depois, EDR-0021) - mas agora ela pelo menos ACHA.
        """
        termos = _termos_de_busca(consulta)
        if not termos:
            return []

        pontuados: list[tuple[int, float, Path, str]] = []
        for caminho in self._diretorio.glob("*.md"):
            conteudo = caminho.read_text(encoding="utf-8")
            alvo = _sem_acento(f"{caminho.stem} {conteudo}")
            acertos = sum(1 for termo in termos if termo in alvo)
            if acertos:
                pontuados.append((acertos, caminho.stat().st_mtime, caminho, conteudo))

        # Mais palavras batidas primeiro; empate desempata pela nota mais
        # recente, que era o unico criterio antes.
        pontuados.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [
            {"titulo": caminho.stem, "trecho": conteudo[:200]}
            for _, _, caminho, conteudo in pontuados[:limite]
        ]
