# Auditoria multicamada — 09-10/11/2026

Base: backup 7eda681 (= GitHub 08/11). Somente leitura; nada aplicado no Pi.
Método: auditor por camada → verificador independente → só então correção com teste.
Provas (scripts) no scratchpad da sessão: `provas/c1_*.py`, `c2_*.py`, `c4_*.py`, `c5_*.py`.

## Estado do loop
| Camada | Auditoria | Verificação |
|---|---|---|
| 1 Segurança de movimento | ✅ entregue | ⏳ em andamento (com C5-01/02) |
| 2 Sensores → Nav2 | ✅ entregue | ✅ 0 refutados (1 agravado) |
| 3 Kernel/comunicação | ✅ entregue | ⏳ falta |
| 4 Firmware ESP32 | ✅ entregue (compila: pio, -Wall, 0 warning) | ⏳ falta |
| 5 Mission/IA | ✅ entregue | ⏳ falta |
| 6 Visão/voz/UI/config | ✅ entregue | ⏳ falta |
| Suíte base Windows | morta por falta de RAM aos 88% (1 F visto) | refazer sozinha |

## Correção do chefe (registrar o próprio erro)
Eu disse que os plugins Jarvis morriam por `responder(tools=)` → TypeError em toda fala.
**Errado em parte:** o registry está VAZIO (`jarvis_plugins/__init__.py:19` não importa nenhum
plugin), o degrau sai em `mission_planner.py:780` antes de consultar contexto. Não há custo por fala.
Patch do dono: eu disse que não aplicava; ERRADO — `git apply --check` dá rc=0 no 7eda681 (refeito 10/11, não reproduzi a falha). O auditor 5 estava certo.

## Achados (aguardando verificação) — por severidade

### CRÍTICOS — parar o robô
- **C1-01** STOP não cancela goal do Nav2: `URL_CANCELAR` (nav2_goal.py:33) órfã; bridge_cmd_vel
  volta a mandar CMD_VEL em ≤100 ms (medido 80 ms). Só AUTÔNOMO (409) ou fim do goal param.
- **C1-02 = C5-01** "Jarvis, para" vai direto ao firmware (conversar_jarvis.py:493); a navegação
  não sabe → FOLLOW/EXPLORE/Nav2 voltam a andar em ≤200 ms. Começar passa pela navegação, parar não.
- **C1-03** STOP da webui durante EXPLORE espera o passo acabar (medido: apertado 1,03 s, chegou
  4,53 s); e VELOCIDADE depois do STOP o sobrescreve na vaga única (20 STOP publicados, 0 entregues).
- **C5-02** STOP falado espera `_consultar_contexto` (3 consultas de 2 s) antes de sair: medido 6,03 s.
  (mission_planner.py:470/532 — o `_pediu_para_parar` vem DEPOIS.)
- **C1-04** Ré em MANUAL sem proteção nenhuma: piso 15 cm < corpo traseiro 22 cm (filtro de carcaça
  de 08/11). Autônomo: 50 cm viram 42 na frente e 28 atrás. Nav2 roda em MANUAL → BackUp sem trava.
  Teste verde por fake permissivo (`_trava_padrao` com corpo=0).

### ALTOS
- **C1-05 / C4-03** Movimento com fim (MOVE_DISTANCE/TURN graus) fora do homem-morto (main.cpp:632-637);
  Pi travado = anda até o fim (2 m ≈ 45 s). E o homem-morto de 5 s é renovado por QUALQUER COMMAND
  (SET_PAN_TILT da visão) → gesto "vem" (MOVE_FORWARD sem STOP) nunca para pelo firmware.
- **C4-01 / C5-03** Firmware dá ACK ANTES de reconhecer e nunca manda NACK (comentário mente);
  `varrer_a_frente` → SCAN_FRONT inexistente → robô diz "Varrendo a frente" e nada acontece.
- **C4-02** Sem teto de PWM: duty 100% (10 V no motor de 6 V) em toda partida, roda travada 1,5 s,
  e FOLLOW/webui v=0,11 = 100% contínuo (3,3x o max_speed_percent 30 do EDR-0037).
- **C2-01** /scan com 12 raios no ângulo de INÍCIO do setor (objeto a 25° à direita aparece à frente);
  os 360 `raios_cm` estão no mesmo payload. (O patch do dono parece corrigir — conferir.)
- **C2-02** /estado nunca limpa o LiDAR no `lidar.error`; bridge carimba "agora" → Nav2 num mundo congelado.
- **C2-03** Bússola congelada (I2C falha, firmware mantém rumo_valido) APAGA o giro do encoder:
  giro de 90° vira ~2° em 6 s. Journal 9501 afirma o contrário.
- Bridge: distancia_cm None → TypeError no timer → provável morte do nó sem respawn (sem /scan,/odom,TF).

### MÉDIOS
C1-06 payload malformado do Notebook mata a recepção do link (porteiro sem try) · C1-07 bridge_cmd_vel
pega o volante uma vez só (409 depois) · C2-04 âncora tardia da bússola salta TF 180° · C2-05 sem TF
base_link→laser, robot_radius 0,20 < 0,246 · C2-06 carimbo do poll e janela de 45 quadros (~3,4 voltas)
· C2-07 retrato da IA sem desconto do corpo ("frente livre 3 cm") · C2-10 = C1-04 · C4-04 relé lanterna
ainda no GPIO 2 (não boota) · C4-05 comentários de segurança falsos + IN do L298N flutuam no reset
· C4-06 quadro serial 320 B com 5 B de folga (TEST_WHEEL) · C4-07 nenhum .bin tem versão/commit
· C4-08 ouvidos: Wi-Fi que não sobe no boot = surdo para sempre; OTA sem senha · C5-04 "parar"/"para de"
comem frases ("quero parar de fumar") · C5-05 4 idas ao Pi antes da hora; extrator gasta cota com
"Parado." · C5-07 patch do dono = exec escapa para `os`, timeout não mata, file_ops base_dir do modelo,
kill/systemctl/launch → NÃO aplicar sem a forma segura · C5-08 vault síncrono no laço do Pi
· C5-09 GOAP ligado ignora acoes_permitidas.

### BAIXOS
C1-08 TEST_WHEEL fora do porteiro, traseira cega = livre · C2-08 docs/comentários · C2-09 slam_toolbox
`*_frame_name` · C4 overflow PCNT sem seção crítica · C5-10 PonteDecisao órfã + `intervalo_s` duplicado
(vale 600) · C5-11 create_task sem referência (mission_planner.py:846).

## Conferido e CERTO
Porteiro único (send/request/_encaminhar); trava recusa sem leitura/velha; watchdog CMD_VEL 500 ms;
convenção de ângulo coerente (frente 19, direita=180 confirmado pelo dono); catálogo falado fechado e
com teto (500→200 cm, 10000°→360°); conserto do "para" de 05/08 de pé; SQLite WAL+lock+backup;
firmware compila limpo; constantes de roda/bitola/pulsos iguais e presas por teste; baud 250000.

## Próximo (ao voltar o limite)
1. Refazer camadas 3 e 6; verificadores das camadas 1, 2, 4, 5.
2. (feito) patch aplica limpo — e por isso é perigoso: liga exec sem sandbox.
3. Correções só dos CRÍTICOS sobreviventes, no branch `auditoria-2026-11-09`, com teste — dono aprova.

## Camada 6 — visão, voz, UI, config (entregue 10/11)
ALTOS: **C6-01** o Pi não sabe quando a visão/conversa viram a cabeça (repasse só Pi→Notebook) → FOLLOW
calcula desvio ≈0 com a pessoa a ±40° · **C6-03** cancelar fala (Professor.parar, "para") solta a
`_trava_fala` com áudio tocando → 2 sd.play simultâneos = reabre o SEGV de 05/08 · **C6-04** webui sem
senha em NADA (exigir_senha ausente, padrão False): /desligar, /controle, /comando, /api/configuracao de
fora = 200; testes de 401 só passam por monkeypatch; CSRF por text/plain · **C6-05** API do Notebook
0.0.0.0:8081 sem auth comanda o planner e expõe câmera; ouvidos 9100 e ustreamer 8091 abertos ·
**C6-06** fastapi/uvicorn/pydantic importados no topo e não declarados → Notebook não sobe em instalação
limpa; aiohttp só no extra display; ollama/openwakeword declarados e mortos · **C6-08** README manda
`python -m orion` como boot real, mas produção é `tools/conversar_jarvis.py` (orion-avatar) — o outro
caminho não repassa nada ao Pi.
MÉDIOS: C6-02 Pi repassa à visão comandos de cabeça que ele descartou · C6-07 follow_distance_m 1.2 no
YAML ignorado (código usa 80 cm); IP do Notebook fixo no server.py:47 · C6-09 ARQUITETURA (Fofão, tela
ESP32-S3, TB6600, "sem encoder").
BAIXOS: C6-10 ESTADO_DOS_MODULOS cita .h inexistentes · 48 chaves de YAML que ninguém lê (security.*
promete segurança que não existe).
Para a camada 3: TCP 5757 aceita qualquer cliente como mission_core e SUBSTITUI o link do Notebook;
ninguém para o robô em `comm.module_lost`.

## Camada 3 — kernel e comunicação (entregue 10/11)
ALTOS: **A1** o bus despacha um evento por vez e as pontes de IA (ponte_conselho/conversa/decisao)
esperam o Gemini DENTRO do handler → visão e heartbeat do Notebook ficam presos (medido: 2,02 s com IA
de 2 s; até 20 s real). No FOLLOW, "a pessoa voltou" fica preso enquanto a IA pensa. · **A2** `drain()`
sem timeout: um peer que para de ler congela o heartbeat de TODOS (medido: corpo 0 heartbeats e 0 perdas
detectadas em 3 s).
MÉDIOS: **A3** exceção não-OSError (UnicodeDecodeError, ValueError do porteiro) mata a recepção do link
sem log (= C1-06) · **A4** heartbeat passa pelo bus: engasgo >3 s no Pi = falsa perda do corpo +
reconexão que reseta o ESP32 (medido 1 module_lost); trava carimba a idade do scan na hora do handler ·
**A5** navigation.goal_resultado no vazio (e nav2_subindo/pronto/desligado).
BAIXOS: A6 protocol_mismatch no vazio → aparece como "Hardware Core não encontrado" · A7 CancelledError
num handler mata o bus e ninguém vigia a tarefa · 3 create_task sem referência.
CERTO: baud 250000 em todo lugar (pendência "8 tools em 115200" RESOLVIDA), porta by-id, reconexão
serial ligada, CRC/ressincronização, isolamento de Exception no bus.

## Verificação da camada 2 (10/11) — 0 refutados
- **Bridge TypeError = ALTA e REAL, não hipótese:** setor sem eco publica `distancia_cm: None` de
  propósito desde 19/09 → `float(None)` no timer → rclpy.spin sai → sem respawn (nav.launch, compose
  `restart: no`) e o gerente_nav2 só olha se o container está Running → **Nav2 sem /scan,/odom,TF sem
  ninguém saber**. Zero testes do bridge no repo.
- C2-01 ALTA ✔ · C2-03 ALTA ✔ (journal 9501 já viu o rumo congelado e afirmou o contrário) ·
  C2-02 rebaixado p/ MÉDIA (a trava tem watchdog próprio, não há colisão) · C2-04/05/07/10 MÉDIA ✔ ·
  C2-06 BAIXA-MÉDIA · C2-08/09 BAIXA (slam_toolbox lê `map_frame`/`odom_frame`; padrões coincidem).
- C2-10: **piso manual da ré inalcançável** (prova v2_trava_manual_re.py: parede a 1/8/15/27 cm da
  traseira passa) = confirma C1-04 por outro caminho. ALTA.
- C2-07: `desvio_frontal` tem o mesmo defeito (IA recebe "obstáculo a 33 mm").
- **Patch do dono:** corrige C2-01 e o TypeError (raios_cm 360, None→inf); C2-05 só em parte
  (robot_radius 0,25, sem TF laser). Ressalva: inf só limpa o costmap com `inf_is_valid: true`, ausente.

## Verificação da cadeia do STOP (10/11) — 0 refutados
- C1-01 ALTA hoje (só `/goal_nav2` publica goal e nenhum botão o chama) → CRÍTICA quando usar Nav2.
  Agravante: recovery `backup` do Nav2 dá ré em MANUAL = sem trava.
- C1-02/C5-01 CRÍTICA ✔ (CMD_VEL volta em 140 ms).
- C1-03 CRÍTICA, PIOR: em AUTÔNOMO a IA manda outro EXPLORE logo depois e **o STOP nunca chega**
  (prova v_stop_explore_sobrescrito.py). O "0 de 20" era pior caso forçado; real é raro mas existe.
- C5-02 ALTA: ~6 s só quando o Pi repassa comandos mas não responde memória = **SSD solta** (depois de queda).
- C1-04 CRÍTICA ✔. C1-05/C4-03 ALTA (gesto "vem" MÉDIA; LacoGestos em produção não provado).

### Conserto desenhado: um ponto único `NavigationCore.parar_tudo(origem)`
Antes de qualquer await: zera `_comando_pendente`, cancela `_tarefa_comando` e `_tarefa_follow`,
HOLD, `_velocidade_travada=True`; depois publica `navigation.parar` e manda STOP.
1. `_ao_receber_comando`: HOLD e MANUAL STOP furam a vaga → C1-03.
2. `definir_velocidade` recusa com a trava ligada; solta só num `navigation.goal` novo → C1-01.
3. `Nav2Goal` assina `navigation.parar` → POST `/cancelar` (to_thread) → C1-01.
4. Gancho em `_encaminhar` (registrado no __main__ junto do porteiro, NÃO no porteiro) quando um STOP
   repassado vai ao corpo → C1-02, venha de onde vier.
5. **DECISÃO DO DONO:** STOP em AUTÔNOMO passa o volante para MANUAL? (senão a IA re-decide na hora).
Fora do ponto único: C5-02 = mover `_pediu_para_parar` para antes do contexto (2 linhas);
C1-04 = `perto - corpo < limite` (**DECISÃO DO DONO:** 50 cm contados do para-choque?) + `_trava_padrao`
com 8/22; C1-05 = firmware renova homem-morto só em comando de roda + HEARTBEAT do Pi; STOP no finally do gesto.

## Verificação 4+5 e 3+6 (10/11) — LOOP FECHADO, 0 refutados por inteiro
- **MAIS GRAVE:** o patch "não commitado" é o `git diff` da árvore do PI (atualizar_backup.sh:38) →
  os plugins (exec escapa p/ `os`, systemctl/launch, kill que o patch CONSERTA) **provavelmente já
  rodam no Pi** se o serviço reiniciou depois de 19/09. "Inertes" vale só para o 7eda681. Conferir por SSH.
- C4-02 REBAIXADO (BAIXA-MÉDIA): "100% em regime" refutado por simulação de malha fechada (60-91%);
  o risco real é roda travada a 100% por 1,5 s. · C4-06 BAIXA (nenhum comando real estoura) ·
  C4-01 MÉDIA (já existe SCAN_FRONT pelo LiDAR na navegação; a voz não usa) · C4-04/C4-08 MÉDIA · C4-07 BAIXA.
- C5-04/05/08/09 ✔ · C5-06 ✔ (registry vazio é a causa, não o TypeError) · C5-07 CRÍTICA se roda no Pi;
  timeout do code_executor deixa o sys.stdout do processo inteiro sequestrado.
- A1 MÉDIA→ALTA pelo chat: **cliente OpenAI sem timeout** (ai_manager.py:258/318, padrão 600 s x2) trava
  o bus do Notebook por minutos · A2/A3/A4 MÉDIA (A3: Pi pode ficar surdo ao Notebook até restart) · A5 BAIXA.
- C6-01 ALTA ✔ (também estado_do_mundo.py:472) · C6-03 ALTA ✔ (+ escuta religa cedo, ouve a própria voz)
  · C6-04 MÉDIA (sem senha é decisão do dono; o bug é a docstring mentir + CSRF text/plain) · C6-05 MÉDIA
  (+ pessoa_id forjável) · C6-06 MÉDIA · C6-08 BAIXA · TCP 5757 aceita qualquer cliente e derruba o Notebook: MÉDIA/ALTA.
- Linhas citadas de heartbeat.py (366, 481-500) não existiam; reais: heartbeat.py:56/170-186, service.py:380.

## Decisões relatadas pelo verificador da camada 2 (AINDA NÃO confirmadas pelo dono nesta janela)
- 50 cm da trava contam do centro do LiDAR (C2-10 vira BAIXA, sem conserto).
- Raio de segurança 25 cm em TODAS as direções, a partir do LiDAR; risco aceito: cantos de trás a 29,4 cm.
- Medida: LiDAR a 10 cm da roda da frente e 15 cm da de trás (10+15 = roda_apoio_distancia_m 0,25 ✔).
  Falta: qual roda é a motriz (TF x = -0,10 ou +0,15) e conferir os 8 cm (LiDAR→ponta) contra os 10 cm (LiDAR→roda da frente).
  **RESOLVIDO 11/11 (dono: "vai valer o que tá agora"):** motriz = frente; a ponta do corpo fica 2 cm atrás
  do eixo e a roda passa 3 cm à frente dele (footprint +0,03). As duas medidas valem juntas.
  Nav2: usar `footprint` circular deslocado + TF base_link→laser, não robot_radius.
- Rodas motrizes = FRENTE (relatado). base_link = eixo dianteiro; TF base_link→laser (-0,10, 0, ~0,45).
  Risco de giro: o diferencial gira no eixo, a traseira varre 37,5 cm em volta dele — o círculo de 25 cm
  do LiDAR não cobre giro no lugar. Proposta: trava = círculo 25 cm (avanço/ré) + giro confere 37,5+folga
  em volta do eixo; Nav2 footprint = RETÂNGULO real (x -0,32..+0,03, y ±0,195).

## CONFIRMADO PELO DONO NA JANELA PRINCIPAL (10/11): "confirmo tudo, pode preparar no branch"
Raio 25 cm do LiDAR em todas as direções (risco dos cantos aceito) · giro no lugar confere 37,5 cm + folga
em volta do eixo dianteiro · rodas motrizes na frente, LiDAR 10 cm atrás do eixo · 50 cm da trava
contam do LiDAR · STOP em AUTÔNOMO passa para MANUAL · plugins Jarvis DESLIGADOS · tudo no branch,
nada gravado no robô.
