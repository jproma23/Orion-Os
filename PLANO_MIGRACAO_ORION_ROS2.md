# PLANO DE MIGRAÇÃO — ORION OS → ROS2 / Nav2 / IA local de decisão

**Documento de briefing para sessão de Claude Code.**
Entregue este arquivo inteiro no início da sessão e peça que ele seja seguido fase a fase.

---

## 0. Instruções ao assistente (ler primeiro)

Você vai conduzir a migração do ORION OS. Regras de condução, sem exceção:

1. **Português** em todo código, comentário, documentação e explicação.
2. **EDR antes de código.** Toda decisão arquitetural desta migração vira um EDR numerado
   antes de qualquer implementação. Se você se pegar implementando algo que não tem EDR,
   pare e escreva o EDR primeiro.
3. **Uma fase por sessão.** Não avance de fase sem que o gate da fase anterior tenha passado
   com evidência registrada. "Deve funcionar" não é evidência; saída de teste é.
4. **Nunca pule um gate** porque a próxima fase parece mais interessante.
5. **Não reescreva o que funciona.** O Protocol SX, o EventBus e o firmware existente do ESP32
   de sensores ficam. Esta migração adiciona uma camada; não substitui a espinha do projeto.
6. **Atualize `RETOMADA.md`** ao fim de cada sessão com o bloco "Estado atual"
   (fase, último trabalho, status dos testes, próximo passo).
7. **Toda afirmação precisa de evidência.** Se você não testou, diga que não testou.
8. Ao fim de cada fase, faça commit com tag `migracao-ros2-fN`.

---

## 1. Regras de segurança física (inegociáveis)

Estas valem em todas as fases e não são negociáveis por conveniência de teste.

- **Rodas no ar** em todo primeiro acionamento de motor após qualquer mudança de firmware,
  de PID, de driver ou de fiação. Sem exceção, nem para "só conferir uma coisinha".
- **Watchdog de 200 ms no ESP32 de motor.** Sem comando novo em 200 ms, motores param.
  Isto é implementado na Fase 1 e nunca é desabilitado, nem temporariamente para debug.
- **Corte físico do rail de motor** acessível com a mão durante qualquer teste em solo —
  chave ou conector que mate a alimentação da ponte H sem depender de software.
- **Nenhum teste de Nav2 em solo** com criança, animal ou escada no ambiente. Primeiro teste
  autônomo acontece em área delimitada, com o operador junto do corte físico.
- **Rail de motor separado do rail lógico.** Buck dedicado. Pi e ESP32s em rails próprios.
  (Já houve um incidente de sobretensão neste projeto; a arquitetura de energia não regride.)

---

## 2. Arquitetura alvo

Três unidades, cada uma com dono único e explícito:

| Unidade | Papel | Interface |
|---|---|---|
| **Raspberry Pi 4** | ROS2 + Nav2 + EventBus + IA local de decisão + ponte API | — |
| **ESP32-S3 (tela)** | Rosto, voz, alto-falante, microfone | UART com o Pi |
| **ESP32 (motor)** | PID de velocidade, encoders, ponte H, sensores ToF | **UART com o Pi** |
| **COIN-D6** | LiDAR 2D | USB (CH340) no Pi |

### Fronteira de autoridade (vira EDR-A)

```
IA local de decisão     → escolhe OBJETIVO      (escala de segundos)
        ↓ NavigateToPose
Nav2 (ROS2)             → caminho e desvio      (20–50 ms, sempre local)
        ↓ /cmd_vel
ESP32 de motor          → PID de velocidade     (malha fechada, watchdog)
        ↓ PWM
Ponte H → motores JGB37-520
```

**Regra dura:** reflexo e desvio de obstáculo nunca atravessam a nuvem, e nunca atravessam
a IA de decisão. Se a internet cair ou a IA travar, o robô continua navegando e desviando —
só fica sem iniciativa.

**Regra dura 2:** ROS2 é dono de tudo abaixo de `cmd_vel` / `odom` / `scan` / `map` / TF.
O EventBus é dono de comportamento, emoção, voz, visão, missão. **Um único nó-ponte** liga
os dois, com direção de autoridade única. Nenhum outro ponto de contato.

Como tudo roda num Pi só: **`ROS_LOCALHOST_ONLY=1`**. Isso elimina a maior dor do ROS2
(descoberta DDS em WiFi) de graça.

---

## 3. EDRs a escrever antes de qualquer código

Continuam a numeração existente do projeto (último registrado: EDR-0027).

| EDR | Assunto | Status |
|---|---|---|
| **EDR-0028** | Fronteira ROS2 × EventBus | Decidido |
| **EDR-0029** | Papel da IA local de decisão | Decidido |
| **EDR-0030** | Mapa persistente como frame canônico | Decidido |
| **EDR-0031** | Contrato do ESP32 de motor | Decidido |
| **EDR-0032** | Distro ROS2 (Humble vs Jazzy) | **Pendente — decisão sua** |
| **EDR-0033** | Provisionamento de rede | Decidido |
| **EDR-0034** | ControleManual × PAUSED_MANUAL | **Pendente — bloqueia a F7** |

---

## FASE 0 — Reconciliação de dívida e baseline

O roadmap 3.0 já exige isto antes de fases novas. Débito técnico não some debaixo de escopo novo.

**Trabalho:**
- Git tag `pre-migracao-ros2` no estado atual. Backup completo do SSD do Pi.
- Rodar a suíte de testes; registrar o que passa e o que falha *hoje*.
- **Resolver o conflito do EDR-0026** (ControleManual como comportamento da escada vs. estado
  `PAUSED_MANUAL`) registrando o **EDR-0034**. Isto é bloqueante — a Fase 7 (ensino por
  condução) exige que o modo manual seja estado de primeira classe, com pose válida e
  publicando TF. Não dá pra construir a camada de coordenadas em cima de um conflito
  não resolvido.
- Decidir e registrar o **EDR-0032** (distro).

**Gate 0:** testes passando no nível registrado, EDR-0034 e EDR-0032 fechados,
EDR-0028 a EDR-0034 todos escritos.
**Rollback:** a tag `pre-migracao-ros2`.

---

## FASE 1 — ESP32 de motor (a fundação real)

Esta é a fase que decide o sucesso de tudo. Nav2 e AMCL só são tão bons quanto o `/odom`.
Odometria ruim faz o Nav2 *parecer* quebrado e o debug vai pra camada errada.

**Hardware:**
- Encoders quadratura nas **duas** rodas. Um encoder num GPIO só não fecha.
- **Encoder alimentado em 3,3 V**, nunca 5 V — GPIO do ESP32 não é 5 V tolerante.
  O encoder tem Vcc separado do Vcc do motor; use o 3,3 V do ESP32.
- Encoders nos **GPIO 34/35/36/39** (input-only, ideais para isso).
- IN1–IN4 e ENA/ENB em GPIOs comuns, evitando 0/2/12/15 (strapping) e 6–11 (flash).

**Se ficar com o L298N:**
- **Remover o jumper "ATIVA 5v".** O regulador da placa (78M05) precisa de ~7 V de entrada;
  com 6 V ele não regula, e esse mesmo rail alimenta a lógica do L298. Alimentar o terminal
  5 V por fora.
- **Remover os jumpers ENA/ENB**, senão não há PWM — só liga/desliga.
- **Alimentar a ponte com 8 V**, não 6 V, para compensar a queda de ~1,5–2 V e entregar os
  6 V nominais ao motor. Dissipador obrigatório (~2 W de calor a 1 A).

**Se trocar para TB6612FNG** (recomendado): queda ~0,5 V, 1,2 A contínuo / 3,2 A pico.
Nada disso acima é necessário.

**Firmware:**
- **PCNT em modo quadratura** (hardware, 8 unidades disponíveis). Não contar por ISR.
- PID de velocidade por roda, alvo em **rad/s**. Motor pequeno com redução tem zona morta —
  vai precisar de termo integral e feedforward para sair do lugar em velocidade baixa,
  que é exatamente o regime que o Regulated Pure Pursuit usa.
- **Contrato (EDR-E):** recebe alvo rad/s por roda; devolve ticks + timestamp a ~50 Hz;
  **watchdog de 200 ms** para os motores autonomamente.
- **UART com o Pi**, cabeada. Nunca WiFi no laço de controle.

**Gate 1 (rodas no ar primeiro, depois em solo):**
- 2 m em linha reta: odometria vs. fita métrica, erro < 5 %.
- Giro de 360° no lugar: erro de heading < 10°.
- Watchdog verificado: desconectar a UART e confirmar que os motores param.

**Se este gate não passar, NÃO avance.** Todo o resto é construído em cima dele.

**Dado faltante a confirmar antes de começar:** a redução do JGB37-520 (varia de 1:9 a 1:270)
e o peso do Fofão montado. Sem isso não dá pra calcular ticks/volta nem velocidade máxima.

---

## FASE 2 — LiDAR no Pi

**Trabalho:**
- SDK ROS2 do COIN-D6 em `ros2_ws/src/`, `colcon build`.
- **Regra udev por serial do CH340 → `/dev/lidar`.** `/dev/ttyUSB0` muda de nome no boot
  quando há outros USB. Apontar o YAML para o nome fixo.
- Baud fixo em 230400 (não é configurável).
- Verificar que o nó envia o comando de partida (`AA 55 F0 0F`) — o LiDAR não gira sem ele.

**Gate 2 — teste de handedness (crítico):**
O COIN-D6 usa regra da mão esquerda, ângulo crescendo no **sentido horário**. ROS usa mão
direita, anti-horário. Se o SDK não inverter isso, o mapa sai espelhado e o AMCL nunca
converge — e o sintoma parece problema de odometria.

> Coloque um obstáculo **só à esquerda** do robô. Confirme no RViz que ele aparece **à esquerda**.
> Repita à frente e à direita. Só então prossiga.

Registre também: o scan chega a 10–15 Hz (66–100 ms por volta). Esse é o piso físico.
Dimensione distância de parada com esse número, não com "tempo real".

---

## FASE 3 — Base ROS2

**Trabalho:**
- `ROS_LOCALHOST_ONLY=1` no ambiente.
- URDF mínimo: `base_link`, `laser_link`, rodas. Medir de verdade — offset do LiDAR errado
  desalinha tudo. O ponto de emissão do COIN-D6 fica a **26,9 mm** do plano da base.
- `robot_state_publisher` + árvore TF: `map → odom → base_link → laser_link`.
- **Nó-ponte ESP32↔ROS2:** assina `/cmd_vel`, publica `/odom` e o TF `odom→base_link`.
  Traduz para o protocolo do ESP32 de motor. **Este é o único nó que fala com o ESP32.**

**Gate 3:**
- `ros2 run teleop_twist_keyboard` move o robô coerentemente (frente é frente).
- `ros2 run tf2_tools view_frames` mostra a árvore completa, sem frame órfão.
- Empurrar o robô 1 m à mão e ver `/odom` acompanhar no RViz.

---

## FASE 3.5 — Provisionamento de rede (ver EDR-0033)

Entra aqui porque depende da UART com os ESP32 já estar de pé e testada (F3), e porque
não bloqueia nada abaixo dela. Nenhuma fase seguinte depende desta.

**Princípio:** o ESP32-S3 é *interface*, não *infraestrutura*. Ele hospeda o formulário e some.
A conexão de internet pertence ao Pi, direto no roteador. Nenhum tráfego de operação passa
pelo ESP32.

**Divisão de trabalho:**

| Quem | Faz |
|---|---|
| **Pi** | Escaneia redes (único rádio que vê 5 GHz), aplica com `nmcli`, testa internet real |
| **ESP32-S3** | Sobe o AP `ORION-SETUP` (WPA2), serve a página, coleta a senha |
| **UART** | Lista desce, senha sobe, status volta |

**Fluxo:**
1. Toque na tela → ESP32-S3 sobe o AP
2. Celular conecta, abre a página
3. Lista de redes vem do Pi pela UART
4. Usuário escolhe e digita a senha
5. Senha sobe pela UART → Pi aplica com `nmcli`
6. **Pi testa internet de verdade** (buscar endpoint conhecido), não só associação
7. Sucesso → AP cai, tela volta a ser o rosto da LULU

**O passo 6 é o que quebra na prática.** Rede de hotel com portal cativo deixa o Pi associado
e com IP, mas sem internet. Sem teste real, o robô reporta sucesso e a IA falha em silêncio.

**Se o passo 6 falhar, o AP NÃO cai.** A página fica aberta para outra tentativa. Derrubar o
AP antes da confirmação deixa o operador sem nada na mão num lugar estranho.

**mDNS:** Avahi no Pi → `fofao.local` em qualquer rede, acabando com a caça a IP.
Anunciar `_orion._tcp` com a porta do FastAPI.

**Senha nunca é logada** — nem no ESP32, nem no log do Pi, nem no SQLite.

**Gate 3.5:**
- Provisionar numa rede 2,4 GHz e numa 5 GHz (a de 5 GHz prova que o scan veio do Pi).
- Senha errada de propósito: o AP continua de pé e a tela informa a falha.
- Rede sem internet (roteador desconectado do link): o passo 6 detecta e reporta.
- `ping fofao.local` responde das duas redes.
- **Desligar o ESP32-S3 com o Pi já conectado: a internet do Pi continua funcionando.**
  Esta é a prova de que ele é interface e não infraestrutura.

---

## FASE 4 — Mapa persistente

Este é o gate da camada de coordenadas. Coordenada só significa algo se o frame `map` for o
mesmo amanhã. **Se você refizer SLAM a cada boot, "cozinha = (3.2, 1.8)" muda de lugar toda
sessão e o sistema inteiro desmonta.**

**Trabalho:**
- SLAM Toolbox em modo assíncrono (menos pesado no Pi 4), mapeamento manual do ambiente.
- `ros2 run nav2_map_server map_saver_cli` → mapa em disco, versionado no git.
- Daí em diante, **subir só AMCL contra o mapa salvo**. SLAM não roda em operação normal.

**Gate 4:**
- Reiniciar o Pi. AMCL converge sobre o mapa salvo em menos de 30 s de movimento.
- Medir CPU e RAM durante navegação. Se estourar: reduzir resolução do costmap antes de
  reduzir funcionalidade.

---

## FASE 5 — Nav2

**Trabalho:** AMCL + Regulated Pure Pursuit + costmaps. Parâmetros conservadores:
velocidade baixa, costmap de resolução modesta, inflação generosa.

**Gate 5:** `NavigateToPose` para um ponto a 3 m, com um obstáculo colocado no caminho
depois que a missão começou. O robô desvia e chega. Operador junto do corte físico.

---

## FASE 6 — Ponte ROS2 ↔ EventBus

**Trabalho:** o **único** nó-ponte. Publica no EventBus: pose, status de navegação, scan
resumido (não os 400 pontos crus). Assina do EventBus: comandos de missão.

**Padrão obrigatório:** missão longa vira `asyncio.Task`, nunca execução inline em handler —
é a regra que já custou um bug neste projeto. Handler bloqueado impede a entrega de comandos
de parada e alertas de obstáculo.

**Gate 6:** um comando emitido no EventBus leva o robô a um ponto; o estado emocional muda
conforme a navegação progride e falha.

---

## FASE 7 — Camada de coordenadas (Gerente de Missão)

Fica acima do Nav2 e abaixo da cognição. **Nada de ML aqui** — isto é 80 % do valor e é
tudo SQL.

**Esquema (no SQLite existente):**

```sql
CREATE TABLE locais (
  nome TEXT PRIMARY KEY,
  x REAL, y REAL, theta REAL,
  frame TEXT NOT NULL DEFAULT 'map',
  covariancia REAL,          -- do AMCL no momento do ensino
  ensinado_em TIMESTAMP
);

CREATE TABLE tentativas (
  id INTEGER PRIMARY KEY,
  origem TEXT, destino TEXT,
  sucesso INTEGER,
  duracao_s REAL,
  n_recoveries INTEGER,      -- quantas recovery behaviors o Nav2 precisou
  hora_do_dia INTEGER,
  criado_em TIMESTAMP
);
```

**Ensino por condução:** o operador leva o Fofão no manual até o ponto e diz "isso é a cozinha";
o sistema salva a pose do AMCL naquele instante.

Duas guardas obrigatórias:
- **Se a covariância do AMCL estiver alta, recuse o ensino.** Melhor não gravar do que gravar
  uma coordenada podre que vai envenenar todas as missões futuras.
- **Gravar sempre em `map`, nunca em `odom`.** Pose em `odom` não sobrevive ao reboot.

**Gate 7:** ensinar três locais, reiniciar o Pi, navegar aos três por nome.

---

## FASE 8 — IA local de decisão

**O que ela é:** uma camada que lê as tabelas acima e escolhe objetivos. Ela recebe o mapa
e as estatísticas, não o scan cru.

**O aprendizado vem das estatísticas, não de RL.** RL no Pi 4 é desaconselhado com força:
dados escassos, o robô se quebra aprendendo, e o resultado é inexplicável — o que briga
frontalmente com a cultura de EDR deste projeto.

O que sai das tabelas, e é inspecionável:
- Taxa de sucesso por rota → rota que falha 4 em 5 vira alerta, não nova tentativa.
- Média de `n_recoveries` por trecho → corredor que sempre exige recovery ganha custo maior
  no costmap.
- Padrão por `hora_do_dia` → trechos que só falham de manhã (sol no LiDAR? porta fechada?).

Isso alimenta direto a **rubrica de mapa de saúde** que já existe no projeto: o robô passa a
ter estado fundamentado em evidência sobre os próprios caminhos.

**Gate 8:** o robô recusa uma rota historicamente ruim e explica por quê, citando o número
de tentativas e a taxa de sucesso.

---

## 4. Onde a API de nuvem entra (e onde não entra)

| Função | Onde roda | Por quê |
|---|---|---|
| Desvio de obstáculo | Nav2, local | 20–50 ms. Nuvem colide. |
| Planejamento de caminho | Nav2, local | Idem |
| Escolha de objetivo | IA local | Sobrevive à queda de internet |
| Visão (identificar objetos) | API | Sem urgência; frames sob demanda |
| Voz (ouvir/falar) | API + ESP32-S3 | Sem urgência |

**Conta que justifica a regra:** round-trip de API é 500 ms a 2 s. A 0,5 m/s isso é 25 cm a
1 m percorridos entre "vi o obstáculo" e "recebi a ordem de desviar". E 400 pontos a 10 Hz
para uma API é insustentável em custo e banda.

---

## 5. Rollback

Cada fase tem sua tag. Se uma fase falhar de forma não recuperável:

```bash
git checkout migracao-ros2-f{N-1}
```

O estado pré-migração inteiro está em `pre-migracao-ros2` e no backup do SSD.
O ORION OS anterior continua funcionando nessa tag — a migração nunca deixa o projeto
sem uma versão que roda.

---

## 6. Ordem de dependência (resumo)

```
F0 dívida ──► F1 odometria ──► F2 lidar ──► F3 base ROS2
                                                  │
                                    ┌─────────────┴─────────────┐
                                    ▼                           ▼
                             F4 mapa                   F3.5 provisionamento
                                    │                     (ramo solto)
                                    ▼
                              F5 Nav2 ──► F6 ponte
                                              │
                                              ▼
                               F7 coordenadas ──► F8 IA local
```

A F3.5 é o único ramo solto: nada depende dela. Pode ser feita em paralelo ou adiada
sem custo para o resto.

Nenhuma seta pode ser pulada. A F1 é a que mais tenta ser pulada e a que mais custa caro
quando é.
