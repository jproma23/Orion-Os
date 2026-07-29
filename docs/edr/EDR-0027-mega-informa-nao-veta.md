# EDR-0027 — O Mega informa, não veta: "não tem freio, existem manobras"

**Status:** Aceito (2026-07-29) — decisão explícita e reafirmada do mantenedor
**Data:** 2026-07-29
**Altera:** `ARQUITETURA.txt` regra 7 (segurança em 3 camadas), Cap 18 (Segurança Operacional)
**Complementa:** EDR-0020 (Behavior Core), EDR-0026 (atribuições do Fofão)

## Contexto

O comportamento `vigilancia_obstaculo` (prioridade 100) tomava o controle
diante de um obstáculo e executava `while True: sleep(1.0)`. O robô parava e
ficava ali até alguém tirar o obstáculo do caminho. Decisão do mantenedor em
2026-07-28:

> "nenhum obstáculo tem q bloquear, o robô tem q desviar do obstáculo"

Ao investigar, a causa não estava no Python. Estava duas camadas abaixo:

- `command_executor.h` recusava **todo** comando de movimento enquanto
  `safety.pararAtivo()` fosse verdadeiro — incluindo `TURN_LEFT`/`TURN_RIGHT`.
- `safety_manager.h` chamava `motores.parar()` a **cada volta do loop**
  enquanto o perigo durasse.

Ou seja: **a única manobra capaz de tirar o obstáculo da frente do robô era
também a única coisa que ele não podia fazer.** Ele não congelava por falta de
plano; congelava porque a camada de baixo o proibia de se mexer.

A primeira correção abriu só o giro. O mantenedor rejeitou a meia-medida, em
três afirmações seguidas:

> "o mega nao pode vetar o mega tem informar os dados e procurar uma saida"
>
> "a inteligiencia recebi todos os dados e dai decide o q fazer"
>
> "nada tem q bloquear ou impedir de tomar acoes e atitudes"
>
> "nao tem freio, existem manobras"

## Decisão

**O Hardware Core mede, informa e executa. Não opina, não recusa, não freia.**

1. **Nenhum comando é recusado.** A guarda de `pararAtivo()` saiu do
   `CommandExecutor`. Qualquer comando vindo do Motion Core executa.

2. **Nenhum freio automático por perigo.** Obstáculo à frente, inclinação
   crítica e impacto deixaram de parar os motores por conta do firmware. Os
   três continuam sendo **detectados e relatados** com o mesmo rigor —
   `seguranca_motivo`, `distancia_frontal_cm`, `radar_angulo_graus`,
   `inclinacao_graus`, `impacto_detectado` — e cada um passa a ter uma
   **manobra** correspondente do lado da inteligência.

3. **Sobra uma parada: o homem-morto.** Se os motores estão girando e nenhum
   comando chega há `TIMEOUT_COMANDO_MS` (5 s), o Mega para.

   Isto não contradiz a regra. Uma manobra exige alguém manobrando; sem
   comandante — link caído, Pi travado, processo morto — não há decisão a
   preservar, só movimento sem dono. Parar aí é o que sobra na ausência da
   inteligência, não um veto contra ela.

## Consequências, ditas com todas as letras

**É possível mandar o robô contra um obstáculo e ele obedecer.** A proteção
mudou de lugar: mora inteiramente na camada de cima. Isto é a decisão, não um
efeito colateral.

Três dívidas nasceram junto e precisam ser pagas antes de o robô andar sozinho
com confiança:

- **`vigilancia_obstaculo` precisa manobrar de verdade.** A versão desta
  sessão gira em malha fechada (gira → mede → repete, invertendo o sentido a
  cada 3 tentativas sem sucesso). É o mínimo para não congelar; ainda não lê o
  radar para escolher o lado mais livre.
- **A missão não pode morrer no obstáculo.** `executar_patrol` ainda desiste
  da rota após `obstacle_retry_max` tentativas. Falta a pilha de missão com
  retomada.
- **O `sem_eco` tinha que parar de mentir** — feito nesta sessão, ver abaixo,
  e era pré-requisito: sem freio embaixo, um sensor que reporta "livre"
  quando quer dizer "não medi" é a receita da colisão.

## O que tornou isto seguro o bastante para aceitar

Na mesma sessão foi corrigido o fail-open do ultrassom (`frontal_sem_eco`
tratado como DESCONHECIDO em vez de "livre" no `NavigationCore` e no
`EstadoDoMundo`) e o erro de `PASSOS_POR_METRO` que fazia o robô andar 57%
mais do que o comandado. Sem essas duas, retirar o freio seria imprudente:
o robô andaria mais do que se pede, na direção de um obstáculo que ele
reportava como caminho livre.

## Alternativas consideradas

- **Manter o veto só para o avanço, liberando o giro.** Foi a primeira
  implementação. Resolve o congelamento e preserva a rede reativa. Rejeitada
  pelo mantenedor: mantém o Mega decidindo, que é justamente o que se quer
  tirar dele.
- **Freio de dano iminente (inclinação/impacto) em milissegundos.** Defendida
  aqui com o argumento de que o Pi não reage nessa escala. Rejeitada:
  *"não tem freio, existem manobras"* — inclinar e bater viram gatilhos de
  manobra (se reorientar, recuar), não de parada.

## Impacto na Rubrica

Todos os módulos de firmware tocados voltam para **🟡 em validação** pela
regra de rebaixamento 1 (código mudou desde a última verificação):
`safety_manager.h`, `command_executor.h`, `motor_manager.h`.

Nada disto foi validado no hardware: o firmware compila (RAM 47,7%, Flash
19,8%) e **ainda não foi gravado no Mega**.
