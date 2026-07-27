# EDR-0024 — Varredura de horizonte: mapa local ancorado no rumo magnético

**Data:** 2026-07-27
**Estado:** proposto (aguarda hardware — ver "Dependências bloqueantes")
**Origem:** ideia do mantenedor, sessão de 2026-07-27.

## Contexto

O Fofão não sabe onde está. A odometria de hoje conta os pulsos de STEP que o
próprio firmware gera, assinados pelo pino DIR (`motor_manager.h`) — é malha
aberta: conta o que **mandou**, não o que a roda **fez**. Roda patinando, motor
travado ou fio de driver trocado passam despercebidos, e o erro nunca é
corrigido porque nada externo confere.

Três números que sustentam a navegação nunca foram medidos:

| Constante | Onde | Valor hoje | Origem |
|---|---|---|---|
| `steps_per_meter` | `orion.yaml` | 4000 | chute de bancada |
| `PASSOS_POR_GRAU` | `motor_manager.h` | 20.0 | chute de bancada |
| `odometry_correction_factor` | `orion.yaml` | 1.0 | nunca calculado |

`autocalibrate_on_first_boot: true` está no `orion.yaml` desde o início e nunca
foi implementado.

Ao mesmo tempo, o robô tem três sensores que olham para o mundo de verdade e
hoje não conversam entre si: dois ultrassons fixos (frente 26/27, trás 22/23),
um servo de radar que varre 120° (30–150°, limitado pela colisão física com o
suporte da webcam) e uma bússola QMC6310 recém-identificada.

## Decisão

Criar a **varredura de horizonte**: o robô gira em torno do próprio eixo em
incrementos pequenos e, a cada parada, registra rumo magnético + distância
frontal + distância traseira. Disso saem dois produtos de uma vez:

1. **Calibração da rotação.** Graus reais girados (bússola) contra passos
   emitidos (odometria) = `PASSOS_POR_GRAU` medido, não chutado. Serve também
   como teste de sanidade: se o comando "gira à direita" faz o rumo andar para
   o lado errado, a fiação dos drivers está trocada.
2. **Mapa polar local**, uma lista de `(rumo_absoluto, distância, origem)` —
   um retrato de 360° do que cerca o robô, em coordenadas absolutas.

### Por que o rumo magnético é a âncora, e não a odometria

Se cada ponto do mapa fosse endereçado pelo ângulo *comandado*, todo erro de
passo entraria no mapa e se acumularia ao longo da volta — o fim da varredura
não fecharia com o começo. O rumo magnético não acumula: cada leitura nasce com
um endereço absoluto, independente da anterior. É o mesmo motivo pelo qual a
varredura serve para calibrar a odometria — só se pode corrigir contra uma
referência que não compartilha o erro.

### Frente + trás na mesma volta (ideia do mantenedor)

Os dois ultrassons apontam a 180° um do outro. Uma leitura do traseiro no rumo
θ é uma leitura do ambiente no rumo θ+180°. Logo, **girar 180° cobre os 360°**.
Metade do giro significa metade do tempo, metade do desgaste e metade do erro
de rotação acumulado.

### Gira, PARA, mede

Cada ponto é medido com os motores **desligados**, não em movimento contínuo.
Dois motivos independentes, ambos medidos neste projeto:

- **Magnético:** motor de passo perto distorce o campo mais que a própria Terra,
  e a distorção muda conforme a rotação (bancada, 2026-07-27). Com o motor
  parado a leitura é limpa.
- **Acústico:** o eco de um obstáculo a 30 cm dura 1,7 ms; a vibração do motor
  de passo já provocou ciclos de leitura ruins o bastante para prender o robô
  em `SAFE_MODE` (achado de 2026-07-26).

Custo: uma varredura de 180° em passos de 15° são 12 paradas — lento. Aceito,
porque é uma rotina de calibração/mapeamento, não o laço de navegação.

## O que NÃO se pode esperar disto

Registrado aqui porque a tentação de tratar o resultado como planta baixa é
grande, e leva a bater em parede:

- **Resolução angular ~30°.** O HC-SR04 devolve "há algo neste cone", não "há
  parede nesta direção". O mapa é um campo grosseiro de livre/ocupado.
- **Parede oblíqua desaparece** (reflexão especular): a onda bate e vai embora
  sem voltar. Por isso **ausência de eco = DESCONHECIDO, nunca LIVRE.** Tratar
  como livre seria mandar o robô contra a parede que ele não viu.
- **Vale para o instante da varredura.** Nada aqui detecta que alguém andou
  pela sala depois. É retrato, não vídeo.

## Fusão com a visão

A visão entra no MESMO referencial de rumo, sem estrutura nova: o YOLO dá o
ângulo do alvo (rumo do robô + ângulo do pan + deslocamento do pixel dentro do
campo de visão), e a distância vem do ultrassom naquele mesmo rumo. A câmera
sozinha não mede distância — monocular só estima com tamanho conhecido do
objeto. O casamento por rumo é o que transforma "vejo uma pessoa" em
"pessoa a 1,2 m no rumo 30°", que é coordenada utilizável.

Isto fica para uma segunda etapa: exige o `VisionCore` publicando de verdade
(hoje ele só é instanciado em teste — achado da vistoria de 2026-07-24).

## Onde cada parte roda

Sem exceção às regras 2, 3 e 4 do `ARQUITETURA.txt`:

- **Arduino:** nada de novo além de reportar. Já emite distâncias, rumo e passos
  na telemetria. Não decide girar, não monta mapa. A segurança reativa continua
  por cima e pode recusar o giro.
- **Raspberry (`motion_core/navigation/`):** orquestra a varredura, guarda o
  mapa, calcula a calibração. É navegação, é o lugar dela (Cap 12).
- **Notebook:** entra só na etapa de visão, publicando rumo do alvo no Event Bus.

Nada é escrito automaticamente no `orion.yaml`: a rotina **mede e reporta**, e a
troca das constantes é decisão explícita do mantenedor. Config é fonte de
verdade editada de propósito (regra 6), não efeito colateral de rotina.

## Dependências bloqueantes (2026-07-27)

Nenhuma delas é de software; todas foram medidas hoje.

1. **Barramento I2C mudo.** `imu_conectado: false` E `bussola_conectada: false`
   na mesma telemetria — os dois dispositivos dos pinos 20/21 sumiram juntos,
   logo após a bússola ser ligada nesse barramento. Sem bússola não há âncora.
   Suspeita principal: o QMC6310 é um componente de 3,3 V e o I2C do Mega
   oscila até 5 V (na WeMos onde ele foi testado, as linhas iam só a 3,3 V —
   a alimentação em 5 V está correta, o módulo tem regulador; o que não foi
   testado é a lógica em 5 V).
2. **Volta do loop em 53 ms** (`loop_max_us: 52968`), contra o teto de ~1,7 ms
   para não perder eco de obstáculo próximo. `frontal_sensor_ok` e
   `traseiro_sensor_ok` ambos `false` no mesmo quadro. Mapear com ultrassom
   exige leitura confiável — isto vem antes.

## Alternativas descartadas

- **Varrer só com o servo do radar.** O arco livre é de 120° (colisão física com
  o suporte da webcam, 2026-07-24) — não fecha 360°, e não gera calibração de
  rotação nenhuma, porque o corpo não gira.
- **Calibrar rotação pelo giroscópio da MPU6050 em vez da bússola.** Seria
  ótimo e não sofre distorção magnética, mas a telemetria de hoje só expõe
  `inclinacao_graus` e `impacto_detectado` — não há yaw nem giro bruto
  (limitação já documentada em `fusao_sensores.py`). Fica como caminho
  alternativo se o I2C da bússola não for resolvido: exigiria expor o eixo Z do
  giroscópio na telemetria, o que é menos trabalho do que parece.
- **Ancorar o mapa na odometria.** Descartado: o erro que se quer medir entraria
  no próprio instrumento de medida.
