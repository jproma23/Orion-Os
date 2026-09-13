# EDR-0034 — ControleManual × PAUSED_MANUAL

**Data:** 13/09/2026
**Status:** ⚠️ **PENDENTE — decisão do desenvolvedor**
**Resolve:** conflito aberto do EDR-0026
**Bloqueia:** Fase 0 (gate), Fase 7 (ensino por condução)

---

## Contexto

Existe um conflito arquitetural em aberto no projeto: `ControleManual` está modelado como um
comportamento da escada de subsunção, enquanto o EDR-0026 define `PAUSED_MANUAL` como um
estado do modelo de estados. As duas modelagens descrevem a mesma situação física de formas
incompatíveis.

Enquanto isso for ambíguo, não há resposta para "quem manda no robô quando o operador está
com o controle na mão".

## Por que virou bloqueante agora

O ensino por condução da Fase 7 depende disso. Para ensinar "isto é a cozinha", o robô
precisa estar em modo manual **e ao mesmo tempo**:

- com o AMCL rodando e convergido
- publicando TF `map → odom → base_link`
- com pose válida e covariância consultável

Ou seja: o modo manual precisa ser um **estado de primeira classe**, não uma suspensão do
sistema. Um comportamento que simplesmente toma a escada e silencia o resto não entrega
pose confiável.

Somando a isso a chegada do Nav2, surge uma terceira questão que o EDR-0026 não previa:
**quem é dono do `/cmd_vel` durante o manual** — o Nav2 precisa saber que não deve publicar,
ou haverá dois emissores disputando o mesmo tópico.

## Questões a responder na decisão

1. `ControleManual` permanece na escada, vira estado, ou os dois com papéis distintos?
2. Durante o manual, o AMCL continua rodando? (Para a F7, precisa continuar.)
3. Quem publica `/cmd_vel` no manual, e como o Nav2 é informado de que deve se calar?
4. A transição manual → autônomo exige revalidação de pose?
5. O que acontece com o modelo emocional durante o manual?

## Recomendação

Não decidir isto sozinho no meio da implementação. É um EDR que merece ser escrito com calma
antes da F1, porque muda a forma da ponte ROS2↔EventBus (EDR-0028).

## Decisão

> **A preencher.**
>
> Modelagem escolhida:
> Data:
> Justificativa:
> Impacto no EDR-0026 (revisão / substituição / complemento):
> Impacto no EDR-0028:
