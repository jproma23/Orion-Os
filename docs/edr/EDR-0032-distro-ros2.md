# EDR-0032 — Distribuição ROS2

**Data:** 13/09/2026
**Status:** ⚠️ **PENDENTE — decisão do desenvolvedor**
**Bloqueia:** F3 (base ROS2) e tudo acima

---

## Contexto

A migração anterior foi de ROS1 Noetic para ROS2 Humble. A consolidação no Pi é, na prática,
um recomeço da stack de navegação — o que reabre a escolha de distro a um custo que não
existirá de novo depois.

Este EDR está deliberadamente **sem decisão preenchida**. A escolha depende de fatores que
só você tem: o que já está instalado e funcionando, o SO do Pi, e o horizonte do TCC.

## Fatores a pesar

**Sistema operacional do Pi.** Humble tem como alvo Ubuntu 22.04; Jazzy, Ubuntu 24.04. Se o
Pi roda Raspberry Pi OS (Debian), qualquer uma das duas exige Docker ou build a partir do
fonte. Esse custo operacional entra na conta.

**Horizonte de suporte.** Humble entra em fim de vida em maio de 2027. Se o robô seguir vivo
depois do TCC, isso importa; se o horizonte é a defesa, importa menos.

**SDK do LiDAR.** O SDK do COIN-D6 vem com o produto e foi validado num ambiente específico.
Confirmar em qual distro ele compila antes de decidir — este é o fator mais concreto dos três.

**O que já existe.** Se a stack Humble atual já roda e tem configuração investida, migrar de
distro no meio de uma migração de arquitetura empilha dois riscos ao mesmo tempo.

## Verificação recomendada antes de decidir

Confirme na documentação atual do ROS2 quais distros estão em suporte hoje e quais são LTS —
este documento foi escrito com informação que pode ter envelhecido.

## Decisão

> **A preencher.**
>
> Distro escolhida:
> Data:
> Justificativa:
> Forma de instalação no Pi (nativa / Docker / fonte):
