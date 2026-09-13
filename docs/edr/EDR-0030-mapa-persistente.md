# EDR-0030 — Mapa persistente como frame canônico

**Data:** 13/09/2026
**Status:** Decidido
**Bloqueia:** F7 (camada de coordenadas) e F8 (IA local)

---

## Contexto

O sistema de comandos por coordenadas guarda locais nomeados ("cozinha", "portão") como
poses. Uma pose só tem significado em relação a um frame de referência.

O SLAM Toolbox constrói o frame `map` a partir de onde o robô ligou. Se o SLAM rodar a cada
boot, a origem muda e o frame `map` de hoje não é o de ontem.

## Decisão

**O mapa salvo em disco é o frame canônico.**

1. SLAM Toolbox roda **uma vez**, no mapeamento inicial, em modo assíncrono.
2. O mapa é salvo (`map_saver_cli`) e **versionado no git** junto com o código.
3. Em operação normal sobe **apenas o AMCL** contra o mapa salvo. SLAM não roda.
4. Todo local ensinado é gravado no frame `map`. **Nunca em `odom`** — pose em `odom` não
   sobrevive ao reboot.
5. Remapear é uma operação deliberada e versionada, nunca automática.

## Por que isto é bloqueante

Sem mapa persistente, "cozinha = (3.2, 1.8)" aponta para um lugar diferente a cada sessão.
A tabela `locais` vira lixo silencioso: as coordenadas continuam lá, com aparência válida, e
levam o robô ao lugar errado. **O sistema inteiro de coordenadas desmonta, sem erro visível.**

## Consequências

**Positivas**
- Coordenadas têm significado estável entre sessões.
- AMCL é muito mais leve que SLAM contínuo — alívio real no orçamento do Pi 4.
- O mapa versionado é auditável: dá pra ver quando mudou e por quê.

**Negativas**
- Mudança física no ambiente (móvel novo, parede) exige remapeamento manual.
- Após remapear, **os locais ensinados precisam ser revalidados**. Um remapeamento que não
  revalida os locais reintroduz exatamente o problema que este EDR evita.

**Guarda obrigatória**
- Se a covariância do AMCL estiver alta no momento do ensino de um local, **recusar o ensino**.
  Melhor não gravar do que gravar uma coordenada podre que envenena missões futuras.
