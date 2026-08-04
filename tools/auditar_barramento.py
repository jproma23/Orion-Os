"""Quem publica no Event Bus sem ninguem escutar - e o contrario.

    ./.venv/bin/python tools/auditar_barramento.py .

POR QUE ISTO EXISTE (2026-08-04). Publicar NUNCA falha: `publish` num topico
sem assinante devolve normalmente, nao loga, nao levanta. Nenhum teste pega, e
o codigo parece certo lendo de perto - o defeito so aparece quando alguem nota
que uma tela nunca mostrou aquilo. Ja mordeu este projeto quatro vezes:

  - a posicao da cabeca, que nao chegava a lugar nenhum (28/07);
  - as decisoes do maestro, invisiveis na interface (25/07);
  - a saudacao espontanea, cuja legenda nunca apareceu no avatar (04/08);
  - camera caida e audio quebrado, que so existiam no log (04/08).

POR QUE NAO E UM GREP. Metade dos `subscribe` deste projeto nao tem o topico
escrito ao lado: a webui e o avatar assinam com
`for topico in TOPICOS_REPASSADOS`, e o `comm.publish` monta o nome com
f-string. Um grep ve zero assinantes onde ha vinte. Este script le a AST e
resolve constantes de modulo, variaveis de laco e prefixos dinamicos.

COMO LER A SAIDA. Nem todo achado e defeito - o que atravessa o link para a
outra maquina aparece como orfao das duas pontas (o `behavior.pedir_conselho`
e o caso classico: o Pi publica, o `__main__` repassa pelo comm, e quem
responde e o Notebook). A pergunta certa para cada linha e: *alguem deste lado
deveria estar ouvindo isso?*
"""
import ast
import collections
import pathlib
import sys

RAIZ = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
PASTAS = ["src", "motion_core", "tools", "tests"]

pubs = collections.defaultdict(list)
subs = collections.defaultdict(list)
pub_prefixos, sub_prefixos = [], []

def const_str(no):
    return no.value if isinstance(no, ast.Constant) and isinstance(no.value, str) else None

def itens_literais(val):
    if isinstance(val, ast.Call) and isinstance(val.func, ast.Name) and val.args:
        val = val.args[0]
    if isinstance(val, (ast.Tuple, ast.List, ast.Set)):
        itens = [const_str(e) for e in val.elts]
        if all(i is not None for i in itens):
            return itens
    if isinstance(val, ast.Dict):
        chaves = [const_str(k) for k in val.keys]
        if chaves and all(c is not None for c in chaves):
            return chaves
    s = const_str(val)
    return [s] if s is not None else None

def coleta(arvore):
    """Todas as ligacoes nome -> strings: constantes de modulo, atribuicoes
    em qualquer nivel, e variaveis de loop `for x in CONST`."""
    tabela = {}
    # duas passadas: constantes primeiro, depois loops que as usam
    for no in ast.walk(arvore):
        if (
            isinstance(no, ast.Assign)
            and len(no.targets) == 1
            and isinstance(no.targets[0], ast.Name)
        ):
            itens = itens_literais(no.value)
            if itens:
                tabela.setdefault(no.targets[0].id, []).extend(itens)
    for _ in range(2):
        for no in ast.walk(arvore):
            if isinstance(no, ast.For) and isinstance(no.target, ast.Name):
                itens = itens_literais(no.iter)
                if itens is None and isinstance(no.iter, ast.Name):
                    itens = tabela.get(no.iter.id)
                if itens is None and isinstance(no.iter, ast.Call):
                    # for t in ALGO.keys() / ALGO.items()
                    f = no.iter.func
                    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                        itens = tabela.get(f.value.id)
                if itens:
                    tabela.setdefault(no.target.id, []).extend(itens)
            # for (a, b) in DICT.items()
            if isinstance(no, ast.For) and isinstance(no.target, ast.Tuple):
                f = no.iter.func if isinstance(no.iter, ast.Call) else None
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                    itens = tabela.get(f.value.id)
                    if itens and isinstance(no.target.elts[0], ast.Name):
                        tabela.setdefault(no.target.elts[0].id, []).extend(itens)
    return tabela

def resolve(no, tabela):
    s = const_str(no)
    if s is not None:
        return [s], []
    if isinstance(no, ast.Name):
        return tabela.get(no.id, []), []
    if isinstance(no, ast.JoinedStr):
        pref = ""
        for v in no.values:
            if isinstance(v, ast.Constant):
                pref += str(v.value)
            else:
                break
        return [], ([pref] if pref else [])
    if isinstance(no, ast.BinOp) and isinstance(no.op, ast.Add):
        esq = const_str(no.left)
        if esq is not None:
            return [], [esq]
    if isinstance(no, ast.Attribute):
        return [], []
    return [], []

for pasta in PASTAS:
    base = RAIZ / pasta
    if not base.exists():
        continue
    for arq in base.rglob("*.py"):
        if ".venv" in str(arq) or "__pycache__" in str(arq):
            continue
        try:
            arvore = ast.parse(arq.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        tabela = coleta(arvore)
        rel = str(arq.relative_to(RAIZ)).replace("\\", "/")
        for no in ast.walk(arvore):
            if not isinstance(no, ast.Call) or not isinstance(no.func, ast.Attribute):
                continue
            if no.func.attr not in ("publish", "subscribe") or not no.args:
                continue
            exatos, prefixos = resolve(no.args[0], tabela)
            loc = f"{rel}:{no.lineno}"
            eh_publish = no.func.attr == "publish"
            destino = pubs if eh_publish else subs
            lista_prefixos = pub_prefixos if eh_publish else sub_prefixos
            for topico in exatos:
                destino[topico].append(loc)
            for prefixo in prefixos:
                lista_prefixos.append((prefixo, loc))

def prod(locais):
    """So o que roda de verdade - teste que publica nao prova consumidor."""
    return [x for x in locais if not x.startswith("tests/")]


def por_prefixo(topico, prefixos):
    return [loc for (prefixo, loc) in prefixos if topico.startswith(prefixo)]

print("=== PUBLICADOS SEM CONSUMIDOR (fora de teste) ===")
n = 0
for t in sorted(pubs):
    p = prod(pubs[t])
    if not p or prod(subs.get(t, [])) or por_prefixo(t, sub_prefixos):
        continue
    n += 1
    print(f"  X {t}")
    for local in p:
        print(f"        pub: {local}")
print(f"  -> {n}")
print()
print("=== ASSINADOS SEM PRODUTOR (fora de teste) ===")
n = 0
for t in sorted(subs):
    s = prod(subs[t])
    if not s or prod(pubs.get(t, [])) or por_prefixo(t, pub_prefixos):
        continue
    n += 1
    print(f"  X {t}")
    for local in s:
        print(f"        sub: {local}")
print(f"  -> {n}")
