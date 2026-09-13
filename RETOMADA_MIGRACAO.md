# RETOMADA — Migração ROS2

Protocolo de reentrada específico da migração. Complementa o `RETOMADA.md` do projeto, não
o substitui.

---

## Ao iniciar qualquer sessão

1. Ler `CLAUDE.md` (regras arquiteturais do projeto)
2. Ler `PLANO_MIGRACAO_ORION_ROS2.md` (o plano completo)
3. Ler o bloco **Estado atual** no fim deste arquivo
4. `git log --oneline -15` e `git status`
5. Rodar a suíte de testes
6. Resolver trabalho não commitado antes de começar qualquer coisa nova

## Ao encerrar qualquer sessão

Atualizar o bloco **Estado atual** abaixo. Sessão que termina sem isso obriga a próxima a
reconstruir o contexto do zero — foi exatamente o problema que originou o protocolo RETOMADA.

## Regras que valem em toda sessão

- Português em código, comentário e documentação
- EDR antes de código
- Uma fase por sessão
- Nenhum gate é pulado
- Toda afirmação precisa de evidência; "deve funcionar" não conta
- Commit com tag `migracao-ros2-fN` ao fechar uma fase
- **Rodas no ar** em todo primeiro acionamento de motor após qualquer mudança
- **Watchdog de 200 ms nunca é desabilitado**, nem para debug

---

## Mapa de fases

| Fase | Assunto | Gate | Status |
|---|---|---|---|
| F0 | Dívida técnica e baseline | Testes no nível registrado, EDR-0032 e EDR-0034 fechados | ⬜ |
| F1 | ESP32 de motor e odometria | 2 m < 5 % erro, 360° < 10°, watchdog | ⬜ |
| F2 | LiDAR no Pi | Teste de handedness no RViz | ⬜ |
| F3 | Base ROS2 (URDF, TF, ponte) | Teleop coerente, árvore TF completa | ⬜ |
| F3.5 | Provisionamento de rede | 2,4 e 5 GHz, falha não derruba AP | ⬜ |
| F4 | Mapa persistente | AMCL converge após reboot | ⬜ |
| F5 | Nav2 | NavigateToPose 3 m com obstáculo | ⬜ |
| F6 | Ponte ROS2 ↔ EventBus | Comando do EventBus leva o robô a um ponto | ⬜ |
| F7 | Camada de coordenadas | 3 locais, reboot, navegar por nome | ⬜ |
| F8 | IA local de decisão | Recusa rota ruim e explica por quê | ⬜ |

## EDRs desta migração

| EDR | Assunto | Status |
|---|---|---|
| 0028 | Fronteira ROS2 × EventBus | ✅ |
| 0029 | Papel da IA local | ✅ |
| 0030 | Mapa persistente | ✅ |
| 0031 | Contrato do ESP32 de motor | ✅ |
| 0032 | Distro ROS2 | ⚠️ pendente |
| 0033 | Provisionamento de rede | ✅ |
| 0034 | ControleManual × PAUSED_MANUAL | ⚠️ pendente, bloqueia F7 |
| 0035 | Rosto expressivo em camadas | ✅ independente das fases |

---

## Pendências de dado (confirmar antes da F1)

- [ ] Redução do JGB37-520 (varia de 1:9 a 1:270) — define ticks/volta e velocidade máxima
- [ ] Peso do Fofão montado (com Pi, LiDAR, bateria, chassi) — define se o torque fecha
- [ ] Ambiente do mapa: interno estável ou externo com terreno mudando — define se AMCL basta

---

## Estado atual

**Fase:** F0 — não iniciada
**Último trabalho:** documentação de migração criada (plano + EDRs 0028 a 0034)
**Status dos testes:** não levantado ainda — é a primeira tarefa da F0
**Próximo passo:** criar a tag `pre-migracao-ros2`, fazer backup do SSD, rodar a suíte e
registrar o que passa e o que falha hoje

**Bloqueios conhecidos:**
- EDR-0034 (ControleManual) precisa ser fechado antes da F1, porque muda a forma da ponte
- EDR-0032 (distro) precisa ser fechado antes da F3
