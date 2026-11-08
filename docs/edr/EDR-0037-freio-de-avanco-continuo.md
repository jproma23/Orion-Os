# EDR-0037 — Freio de avanço contínuo: o LiDAR para as rodas no meio do movimento

**Data:** 01/11/2026 (tarde)
**Status:** **Implementado em 01/11 (noite)**. Ver "Como ficou", no fim. Falta o teste de frenagem medido com trena.
**Módulos:** Motion Core (`trava_lidar.py`, `navigation_core.py`, `__main__.py`, `orion.yaml`)
**Revê:**
- os comentários de **18/08 e 20/08** no `navigation_core.py`, que contam com "ultrassom + parada do Mega". Nenhum dos dois existe mais: o ultrassom saiu em 19/09 e o ESP32 não freia (EDR-0027);
- o commit **`1dd1e89`** (01/11 de manhã, "LiDAR informa, não bloqueia"). Ele desligou a trava que o `572efb6` tinha acabado de criar.

**Relacionado:** EDR-0027 (o corpo informa e não veta). A EDR da navegação pelo Nav2 (escrita no backup como 0037) passa a ser **0038**.

---

## O que aconteceu (01/11, 16:20, medido no log do Pi)

A IA escolheu `explorar` ("o caminho está livre à frente"). O EXPLORE andou em pedaços de 30 cm e o LiDAR **mediu a cômoda chegando**:

| Hora | Frente medida | O que aconteceu |
|---|---|---|
| 16:20:14 | 77,6 cm | andou 30 cm |
| 16:20:47 | 41,7 cm | andou 30 cm |
| 16:20:52 | 34 cm | `trava_lidar`: "LiDAR avisa **(sem bloquear)**", e andou 30 cm |
| 16:20:56 | **25,7 cm** | **andou mais 30 cm → bateu** |
| 16:21:04 | 15,2 cm | o EXPLORE viu "obstáculo à frente" e parou |

Foram três defeitos juntos:
1. **A trava está em "só avisa".** O código que para as rodas no meio do movimento existe (`TravaLidar._ao_receber_scan`), mas só age com `bloquear=True`, e o padrão é `False`.
2. **O EXPLORE anda 30 cm sem conferir se cabem 30 cm.** Com 25,7 cm livres, ele mandou 30.
3. **O EXPLORE ainda chama o radar que foi tirado do robô** (`SCAN_FRONT`). Cada passo espera 3 s, desiste ("scan_complete não chegou") e anda. É o "anda e para várias vezes".

## Decisão

**O LiDAR volta a frear, sempre, venha o comando de quem vier.** A IA continua decidindo para onde ir. O freio só existe para quando ela, o EXPLORE, o Nav2 ou o joystick errarem.

### 1. Trava ligada por padrão
`motion.trava_lidar.bloquear: true` no `orion.yaml`, e o padrão do código também muda para `True`. Com isso, um yaml sem a chave fica no lado seguro.

### 2. Só corta o movimento que vai em direção ao obstáculo
- **Avanço** com obstáculo no corredor da frente a menos do limite: **STOP imediato** + `navigation.obstacle_avoided`.
- **Ré** com obstáculo no corredor de trás a menos do limite: STOP. *(O texto do pedido diz "ré segue livre", mas as distâncias que o dono passou hoje incluem **trás 50 cm**. Ver a pergunta 1 abaixo.)*
- **Giro no lugar segue livre.** Hoje há um bug: o "movimento em curso" (`_sentido_em_curso`) só é apagado por `STOP` ou por `CMD_VEL` zero. Depois de um `MOVE_DISTANCE` para a frente, um `TURN_LEFT` perto da parede seria **parado** pela trava. Correção: apagar o sentido quando chega um comando de giro e quando o corpo volta a `IDLE` (telemetria `em_movimento: false`).

### 3. Watchdog: andando sem LiDAR, para
Se há movimento em curso e nenhum `lidar.scan` chega há mais que `idade_max_s`, manda STOP. Hoje isso só acontece com o `lidar.error`, que leva 3 s.

Atenção a um número: o LiDAR publica no máximo **a cada 0,4 s** (`INTERVALO_MINIMO_PUBLICACAO_S`). Um watchdog de 0,5 s pararia o robô a cada varredura atrasada. Proposta: publicar a cada **0,2 s** e usar watchdog de **0,6 s**, ou seja, três varreduras perdidas seguidas.

### 4. Limites
| Situação | Limite | Origem |
|---|---|---|
| Autônomo (IA, EXPLORE, Nav2): frente | **50 cm** | dono, 01/11 |
| Autônomo: trás | **50 cm** | dono, 01/11 |
| Autônomo: laterais | **20 cm**, cortando só o giro para aquele lado | dono, 01/11. **Fica para a etapa seguinte**, porque precisa do contorno medido do robô |
| **Manual** (joystick, operador no volante) | **piso duro de 15 cm** na frente: obedece, mas não encosta | pedido de 01/11 tarde |

As distâncias são medidas **ao longo do movimento, dentro do corredor da largura do robô**, e não num cone. Esse cálculo já existe (`_mais_perto_no_corredor`). Parede ao lado num corredor não trava.

### 5. Velocidade limitada até passar nos testes
`max_speed_percent: 60 → 30`. Volta a subir só depois do teste de frenagem, medido.

### 6. EXPLORE (os defeitos 2 e 3)
- O pedaço de avanço passa a ser `min(30 cm, frente medida − limite)`. Se não sobrar nada, ele não anda e devolve "obstáculo" para a IA.
- O EXPLORE para de chamar o `SCAN_FRONT` e usa a varredura do LiDAR que já está na memória. Somem as esperas de 3 s.

## Testes (unitários, antes de qualquer coisa no chão)
1. Varredura com obstáculo a 20 cm durante `MOVE_CONTINUOUS` → **STOP**.
2. Varredura com obstáculo a 20 cm durante um **giro** → **sem STOP**.
3. **Giro depois de um `MOVE_DISTANCE`** que já terminou, com parede a 20 cm → sem STOP (prende o bug do item 2).
4. Avançando e 0,7 s sem varredura → **STOP**.
5. Manual a 10 cm → **STOP**; manual a 20 cm → passa.
6. Yaml sem `trava_lidar` → a trava **bloqueia** (padrão seguro).
7. EXPLORE com a frente a 60 cm → manda no máximo 10 cm (com limite de 50); com a frente a 45 cm → não manda avanço.

## Teste real (com o dono ao lado e o botão MANUAL à mão)
1. **Rodas no ar**, com uma caixa diante do LiDAR: `MOVE_CONTINUOUS` → as rodas param. Tirar a caixa → anda. Tirar o cabo do LiDAR → para em menos de 1 s.
2. **No chão a 30%**, de frente para uma parede: ele para entre 50 cm e a distância de frenagem. **Medir com trena onde ele parou**, porque esse é o número que permite subir a velocidade depois.
3. Girar no lugar com a parede a 30 cm → gira normalmente.

## Perguntas para o dono antes de codar
1. **Ré:** frear a 50 cm atrás, como nas distâncias de hoje, ou ré livre, como no texto do pedido? A recomendação é frear, porque a ré é o movimento menos protegido do robô.
2. **Limite da frente:** 50 cm, como você pediu hoje, ou `lidar_min_obstaculo_cm + lidar_recuo` (25 + 8 = 33 cm), como no texto do pedido? Com 50 cm ele fica mais "medroso" em corredor estreito. Com 33 cm sobra pouca margem a 30%.
3. **O piso de 15 cm do manual vale só para a frente, ou para a ré também?**

---

## Como ficou (01/11, noite)

**Respostas do dono:** ré 50 cm, frente 50 cm, piso de 15 cm no manual **nos dois sentidos**.

**O que foi implementado:**
- **Trava (`trava_lidar.py`):**
  - **bloqueia por padrão**: 50 cm na frente e atrás no autônomo, 15 cm no manual;
  - manda STOP se o obstáculo aparece no meio do avanço;
  - **watchdog**: andando e 0,6 s sem varredura → STOP;
  - o avanço em curso acaba quando chega um giro, um STOP, ou quando o corpo para (era o bug do giro);
  - **frente cega = obstáculo**: mais de 60% dos raios sem eco em ±20° da frente impede o avanço.
- **LiDAR (`gerenciador.py`):** publica a cada **0,2 s** (era 0,4).
- **Desvio local (`desvio_local.py`) no EXPLORE:**
  - avança o que cabe: `min(30, livre − 50)`;
  - se não cabe, **gira para a direção mais aberta**, preferindo o menor giro que já permite um passo inteiro;
  - só quando nenhuma direção tem espaço ele para e avisa a IA;
  - o EXPLORE parou de chamar o `SCAN_FRONT`, o radar que saiu do robô e custava 3 s por passo.
- **Velocidade:** `max_speed_percent: 30`.
- **A IA escolhe só intenções** (`behavior.ia_estrategica.acoes_permitidas`). Saíram `andar_mm`, `girar_*`, `desviar_*`, `avancar`, `virar_*`, `manobrar` e `calibrar_bussola`. Motivo: na primeira volta com o freio, ela escolheu `desviar_esquerda_mm`, girou 90° para uma parede a 47 cm e a trava recusou o avanço. O freio funcionou, mas a manobra não deve ser dela.

**Achado no caminho: a frente do LiDAR estava 34° errada.**
- `angulo_frente_graus` mudou de 345 para **19**. Foi medido com uma reta ajustada na parede de frente (perpendicular em 19 no ângulo bruto) e **confirmado pelo dono**: a parede encostada à direita aparece em 180°.
- O 345 de 31/10 era a borda da faixa onde o sensor perde a parede perto e de frente. Com uma parede a ~30 cm, perpendicular, o STL-50B2 devolve "sem eco" em ~3/4 das varreduras (30 s de leitura bruta: 31 de 118 com eco).
- Nenhuma peça do robô aparece na visão do LiDAR (nada a menos de 12 cm em 360°). Por isso `min_distancia_mm` desceu de 100 para 50.

**O que fica em aberto:**
- **"Sem eco" é igual para parede perto e para vão aberto longe.** Com a porta aberta, a frente mediu 11 m e depois nada. Hoje a regra trata os dois como obstáculo, e o robô não atravessa porta aberta. Proposta: estimar a distância pela borda medida mais próxima da faixa sem eco.
- **O corredor de 25 cm de meia-largura bloqueia andar encostado na parede** (parede a 26 cm do LiDAR). Proposta: 22 cm. Falta o dono confirmar se o LiDAR fica no meio do robô.
- **Teste de frenagem medido com trena.** Só depois dele a velocidade sobe de 30%.
- **`NavigationCore.desviar` não gira de volta** quando o avanço é recusado.
