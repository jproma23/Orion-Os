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
from pathlib import Path

logger = logging.getLogger("motion_core.memory.vault")

_CARACTERES_INVALIDOS_ARQUIVO = re.compile(r'[\\/:*?"<>|]')


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
        """Busca simples por texto (titulo e conteudo, case-insensitive).
        Retorna [{"titulo": ..., "trecho": ...}], mais recentes primeiro."""
        termo = consulta.lower()
        candidatos = sorted(
            self._diretorio.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        resultados: list[dict[str, str]] = []
        for caminho in candidatos:
            if len(resultados) >= limite:
                break
            conteudo = caminho.read_text(encoding="utf-8")
            if termo in caminho.stem.lower() or termo in conteudo.lower():
                resultados.append({"titulo": caminho.stem, "trecho": conteudo[:200]})
        return resultados
