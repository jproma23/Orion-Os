"""Recados: guardar uma mensagem para alguem e entregar quando essa pessoa vier.

## A falha que criou este arquivo (2026-08-03)

O dono pediu ao robo: *"avise a Mara quando a vir que eu quero falar com ela"*.
O robo respondeu que sim. Minutos depois a Mara chegou, ele a **cumprimentou** -
e nao deu o recado.

Nada estava quebrado. A promessa era do MODELO, e so dele: nao havia tabela,
nao havia acao, nao havia entrega. O robo disse "combinado" porque um modelo de
linguagem que ouve um pedido gentil responde que sim; nao porque alguma coisa
no sistema tivesse sido combinada.

E a mesma familia de falha que este projeto ja catalogou (publicar num topico
sem assinante): **a promessa nao falha, entao nada denuncia**. So que aqui a
testemunha e uma pessoa esperando por um recado que nunca chega.

## As tres partes, e por que elas moram longe uma da outra

    quem PEDE      -> a conversa, no Notebook  (acoes_faladas: deixar_recado)
    quem GUARDA    -> o orion.db, no Pi        (tabela `recados`)
    quem ENTREGA   -> a saudacao, no Notebook  (na CHEGADA da pessoa)

Guardar no banco e o que faz o recado sobreviver ao que importa: ao fim da
conversa, ao esquecimento do modelo (que lembra 6 turnos por 10 minutos) e ao
robo ser desligado. Um recado que so vive na memoria da conversa nao e um
recado - e uma frase educada.

## Casamento por NOME, e nao por id

Quem deixa o recado diz um nome ("a Mara"), e quem chega e reconhecido por nome
(`vision.person_recognized` carrega `nome`). Exigir id obrigaria a resolver a
pessoa no instante do pedido, e um recado para alguem ainda nao cadastrado
simplesmente nao poderia existir.

A comparacao e pelo PRIMEIRO nome, sem acento e sem caixa - "Marah" no
orion.yaml, "Mara" na fala do dono e "mara" no banco tem que ser a mesma
pessoa. E o mesmo criterio que o diario ja usa para "voce viu o Joao?"; o robo
nao pode ser mais exigente com um recado do que e com uma pergunta.
"""
from __future__ import annotations

import difflib
import logging
import unicodedata
from datetime import datetime

logger = logging.getLogger("orion.mission.recados")

CATEGORIA = "recados"

#: Teto de recados entregues de uma vez. Quem chega em casa e recebido por uma
#: pessoa que fala, nao por uma secretaria eletronica despejando a fila.
MAXIMO_POR_CHEGADA = 3


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFD", texto.lower().strip())
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn")


#: apelido (sem acento, primeiro nome) -> nome oficial da pessoa.
#:
#: POR QUE PREFIXO NAO BASTA (2026-08-04). O casamento abaixo cobre o apelido
#: que e o nome CORTADO - "Marah" -> "Mara". Mas a Marah tambem atende por
#: **Marisane**, e "marisane" nao comeca com "marah" nem o contrario. Um recado
#: deixado para a Marisane sumia: guardado com um nome, procurado com outro.
#:
#: Nao da para adivinhar isso por regra de string - Marisane e Marah so sao a
#: mesma pessoa porque alguem DIZ que sao. Entao vem do orion.yaml, via
#: `definir_apelidos`, e nao de heuristica nova.
_APELIDOS: dict[str, str] = {}


def definir_apelidos(familia) -> None:
    """Carrega os apelidos da secao `familia` do orion.yaml.

    Chamada uma vez no boot de quem monta os recados. Sem ela o modulo segue
    funcionando exatamente como antes (so o casamento por prefixo), o que
    mantem validos os testes e as ferramentas de bancada que nao tem config.
    """
    _APELIDOS.clear()
    for pessoa in familia or []:
        oficial = str((pessoa or {}).get("nome", "")).strip()
        if not oficial:
            continue
        for apelido in (pessoa or {}).get("apelidos") or []:
            chave = _sem_acento(str(apelido)).split()[:1]
            if chave:
                _APELIDOS[chave[0]] = oficial


def _oficial(primeiro: str) -> str:
    """Nome oficial de quem atende por `primeiro`, ou ele mesmo."""
    return _sem_acento(_APELIDOS.get(primeiro, primeiro)).split()[:1][0] if primeiro else ""


def mesmo_nome(a: str, b: str) -> bool:
    """"Marah", "Mara" e "mara" sao a mesma pessoa; "Mario" nao e.

    Compara o PRIMEIRO nome e aceita que um seja comeco do outro. O apelido
    costuma ser o nome cortado ("Marah" -> "Mara"), quase nunca o contrario de
    outra letra - e errar para o lado de entregar um recado a mais e melhor que
    guardar para sempre um recado que era para aquela pessoa.

    Antes disso, os dois nomes passam pela tabela de apelidos do orion.yaml
    (ver `_APELIDOS`): e ela que faz "Marisane" e "Marah" serem a mesma pessoa,
    coisa que nenhuma regra de string descobriria sozinha.
    """
    primeiro_a = _sem_acento(a).split()[:1]
    primeiro_b = _sem_acento(b).split()[:1]
    if not primeiro_a or not primeiro_b:
        return False
    x, y = primeiro_a[0], primeiro_b[0]
    # Apelido declarado vale mesmo quando as letras nao se parecem.
    if _oficial(x) == _oficial(y):
        return True
    if len(x) < 3 or len(y) < 3:
        # Nome de duas letras casaria com meio mundo por prefixo.
        return x == y
    return x.startswith(y) or y.startswith(x)


def destinatario_conhecido(para: str, familia) -> tuple[bool, str | None]:
    """O destinatario casa com alguem da familia? (conhecido, sugestao).

    POR QUE (achado de 06/08, consertado na auditoria de 12/11): o Whisper
    ouviu "Marisani", o apelido cadastrado e "Marisane", e o robo disse
    "combinado, eu aviso" para um recado que a entrega nunca ia achar - preso
    para sempre, parecendo pendente. Corrigir a tabela de apelidos a cada
    variante nova nao escala: o nome vem da transcricao, que erra.

    So a CRIACAO tem alguem na frente para corrigir. `sugestao` e o nome (ou
    apelido) mais parecido, para o robo perguntar "voce quis dizer...?".
    Sem familia configurada, nao ha com o que conferir: conta como conhecido.
    """
    nomes: list[str] = []
    for pessoa in familia or []:
        oficial = str((pessoa or {}).get("nome", "")).strip()
        if oficial:
            nomes.append(oficial)
        nomes.extend(str(a).strip() for a in (pessoa or {}).get("apelidos") or [] if str(a).strip())
    if not nomes:
        return True, None
    if any(mesmo_nome(para, nome) for nome in nomes):
        return True, None
    primeiro = (_sem_acento(para).split() or [""])[0]
    por_chave = {(_sem_acento(n).split() or [""])[0]: n for n in nomes}
    parecidos = difflib.get_close_matches(primeiro, list(por_chave), n=1, cutoff=0.6)
    return False, (por_chave[parecidos[0]] if parecidos else None)


class Recados:
    """Guarda e entrega recados. Conversa com o banco pelo `memory_client`.

    Nenhum metodo levanta: um recado perdido nao pode derrubar a saudacao nem
    a conversa (Cap 6 s.8). Mas TODOS logam a falha - e justamente o silencio
    que criou este modulo.
    """

    def __init__(self, memory_client, agora=None) -> None:
        self._memoria = memory_client
        #: injetavel para os testes nao dependerem do relogio da maquina
        self._agora = agora or (lambda: datetime.now().isoformat(timespec="seconds"))

    async def deixar(self, para: str, texto: str, de: str = "") -> bool:
        """Guarda um recado. Devolve se conseguiu - quem chama tem que SABER.

        Se isto devolver False e o robo disser "combinado" assim mesmo, esta
        repetido o defeito original com mais codigo em volta.
        """
        if not para or not texto:
            return False
        try:
            await self._memoria.remember(
                CATEGORIA,
                {
                    "para_nome": para,
                    "de_nome": de or None,
                    "texto": texto,
                    "criado_em": self._agora(),
                    "entregue_em": None,
                },
            )
        except Exception:
            logger.exception("nao consegui GUARDAR o recado para %s", para)
            return False
        logger.info("recado guardado para %s: %r", para, texto)
        return True

    async def pendentes(self, nome: str) -> list[dict]:
        """Recados ainda nao entregues a quem acabou de chegar.

        O filtro por nome e feito AQUI, e nao na consulta, porque o banco
        casaria por igualdade exata e "Marah" nunca acharia o recado deixado
        para "Mara". A consulta traz os pendentes (que sao poucos por
        definicao - recado entregue sai da fila) e o casamento e por primeiro
        nome.
        """
        if not nome:
            return []
        try:
            abertos = await self._memoria.recall(CATEGORIA, {"entregue_em": None}, limite=50)
        except Exception:
            logger.exception("nao consegui LER os recados de %s", nome)
            return []
        meus = [r for r in abertos if mesmo_nome(str(r.get("para_nome", "")), nome)]
        # Mais antigo primeiro: quem esperou mais ouve primeiro.
        meus.sort(key=lambda r: str(r.get("criado_em", "")))
        return meus[:MAXIMO_POR_CHEGADA]

    async def marcar_entregue(self, recado_id: int) -> bool:
        """Fecha o recado. NAO apaga: o registro e a prova de que ele cumpriu.

        Se isto falhar, o recado continua pendente e sera repetido na proxima
        chegada. Repetir e chato; perder e quebrar a promessa. Entre os dois, a
        escolha e obvia - e por isso a falha so vira log.
        """
        try:
            return await self._memoria.update(
                CATEGORIA, recado_id, {"entregue_em": self._agora()}
            )
        except Exception:
            logger.exception("entreguei o recado %s e nao consegui marcar", recado_id)
            return False
