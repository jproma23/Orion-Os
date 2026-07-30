# Removidos em 2026-07-30 (Portao 0 do roadmap - "5S no codigo")

Nada aqui esta em uso. Foram tirados da arvore porque **duplicata viva e
armadilha**: alguem edita a copia errada e o bug so aparece muito depois.
Ficam guardados aqui porque descartar e facil e recuperar tem que ser tambem.

Todos tambem estao no historico do git (`git show <commit>:<caminho>`), entao
esta pasta e conveniencia, nao a unica copia.

| arquivo | de onde saiu | por que saiu | quem faz o trabalho agora |
|---|---|---|---|
| `conversar_fofao.py` | raiz do projeto | **divergente** do de `tools/` (20 KB contra 34 KB) e desatualizado. Nenhum servico, script ou doc apontava para ele - conferido antes de remover | `tools/conversar_fofao.py`, que e o que o `orion-conversa.service` executa |
| `heartbeat.py` | `src/orion/` | **zero importadores** no projeto inteiro | `src/orion/communication/heartbeat.py` |
| `watchdog.py` | `src/orion/` | **zero importadores** no projeto inteiro | `src/orion/kernel/watchdog.py` |

## Como conferi que estavam mortos

```
grep -rl "from orion.heartbeat import|import orion.heartbeat"  -> vazio
grep -rl "from orion.watchdog import|import orion.watchdog"    -> vazio
grep -rn "/conversar_fofao.py" em *.sh *.service *.md          -> so o tools/
ExecStart dos servicos systemd                                 -> so o tools/
```

Suite completa depois da remocao: **608 testes passando**, os mesmos de antes.

## Se precisar de um de volta

Copie de volta para o caminho original e rode a suite. Mas antes pergunte por
que: se a resposta for "preciso de uma funcao que esta ai dentro", o certo e
mover a funcao para o modulo vivo, nao ressuscitar a duplicata - senao volta o
problema que a remocao resolveu.
