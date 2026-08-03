"""A memoria de curto prazo da conversa falada.

## O que estava errado

Queixa do dono em 2026-08-02: *"a conversa ja muda de assunto, nao tem
memoria"*. Nao era memoria curta - era memoria NENHUMA. Toda pergunta ia ao
modelo exatamente assim:

    messages=[
        {"role": "system", "content": prompt_sistema},
        {"role": "user",   "content": texto_usuario},
    ]

Duas mensagens, sempre. Nenhum turno anterior. O robo nao mudava de assunto:
ele nunca soube que havia um assunto. Perguntar *"e ele?"* logo depois de
falar sobre alguem chegava ao modelo como a primeira frase que aquela pessoa
disse na vida.

Estava assim nos TRES caminhos que falam com o modelo (`_chamar_ollama`,
`_chamar_openai`, `_openai_em_frases`) - por isso o conserto mora aqui, numa
peca so, e nao remendado em cada um.

## Por que uma classe, e nao uma lista dentro do AiManager

Porque as duas decisoes dificeis nao sao "guardar": sao **quando esquecer** e
**o que nao guardar**. Isoladas aqui, elas sao testaveis sem subir modelo
nenhum.

## Esquecer por TEMPO, nao so por tamanho

Uma lista com limite de turnos guarda para sempre a ultima conversa. Se o dono
fala com o robo as 10h e volta as 18h, a pergunta das 18h chegaria colada numa
conversa de oito horas atras - e o modelo responderia sobre aquilo. Pior que
nao ter memoria, porque parece deliberado.

`validade_s` resolve: passado esse silencio, a conversa anterior acabou. Dez
minutos e generoso para uma pausa dentro do mesmo papo e curto para nao
atravessar assuntos.

## O que NAO entra aqui

Nada que nao seja conversa com uma pessoa. O `AiManager.decidir()` usa o mesmo
modelo para escolher acoes do robo, e se aquilo caisse aqui o historico
encheria de "escolha uma acao: avancar, virar_esquerda..." - a conversa
seguinte seria respondida no meio de um monte de deliberacao de movimento.
Quem chama controla isso pelo parametro `usar_historico`.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Callable


class HistoricoConversa:
    """Os ultimos turnos da conversa falada, prontos para ir ao modelo."""

    def __init__(
        self,
        max_turnos: int = 6,
        validade_s: float = 600.0,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        `max_turnos` conta PARES (o que a pessoa disse + o que o robo
        respondeu), nao mensagens. Seis pares = doze mensagens, que numa
        conversa falada e bastante: as frases sao curtas e o custo de token
        vira latencia audivel, que e o unico custo que o dono sente.

        `relogio` e injetavel para os testes controlarem a passagem do tempo
        sem dormir de verdade. `time.monotonic` e nao `time.time` porque
        ajuste de relogio do sistema nao pode fazer a conversa expirar (ou
        deixar de expirar) sozinha.
        """
        self._max_turnos = max_turnos
        self._validade_s = validade_s
        self._relogio = relogio
        self._turnos: deque[tuple[str, str]] = deque(maxlen=max_turnos)
        self._ultimo_instante: float | None = None

    def _expirou(self) -> bool:
        if self._ultimo_instante is None:
            return False
        return (self._relogio() - self._ultimo_instante) > self._validade_s

    def mensagens(self) -> list[dict[str, str]]:
        """Os turnos anteriores no formato de `messages`, prontos para entrar
        ENTRE o prompt de sistema e a fala atual.

        Se a conversa expirou, esvazia aqui mesmo em vez de so devolver vazio:
        assim o proximo `registrar` comeca de fato uma conversa nova, e nao
        uma que ressuscita porque o silencio foi mais curto na segunda vez.
        """
        if self._expirou():
            self.limpar()
        mensagens: list[dict[str, str]] = []
        for pergunta, resposta in self._turnos:
            mensagens.append({"role": "user", "content": pergunta})
            mensagens.append({"role": "assistant", "content": resposta})
        return mensagens

    def registrar(self, texto_usuario: str, resposta: str) -> None:
        """Guarda um turno completo.

        Turno pela metade nao entra: uma pergunta sem resposta (a IA falhou, o
        usuario interrompeu) deixaria o historico terminando em `user`, e o
        modelo trataria a fala seguinte como continuacao de algo que ele nunca
        respondeu. Melhor perder o turno do que guardar um torto.
        """
        if not texto_usuario or not texto_usuario.strip():
            return
        if not resposta or not resposta.strip():
            return
        if self._expirou():
            self.limpar()
        self._turnos.append((texto_usuario.strip(), resposta.strip()))
        self._ultimo_instante = self._relogio()

    def limpar(self) -> None:
        """Encerra a conversa. Chamar quando o assunto claramente virou outro
        - troca de pessoa na frente do robo, por exemplo."""
        self._turnos.clear()
        self._ultimo_instante = None

    def __len__(self) -> int:
        return len(self._turnos)
