# ORION OS — System Engineering Specification (SES)
## Capítulo 11 — Memory Core
Versão 1.0 — Especificação Oficial
## 1. Objetivo
O Memory Core é o subsistema responsável por armazenar, organizar e recuperar todo o conhecimento do ORION OS. Ele fornece ao Mission Core memória de curto e longo prazo, permitindo que o robô reconheça pessoas, aprenda ambientes, lembre de objetos e evolua com base no histórico de interações.
## 2. Localização na Arquitetura
O Memory Core executa no Raspberry Pi (Motion Core), utilizando o banco SQLite armazenado no SSD de 500 GB como armazenamento persistente (EDR-0019). Nenhum outro módulo acessa o banco diretamente; todo acesso ocorre pela API de memória, exposta ao Notebook pela Ethernet (comm.request) e aos serviços locais do Raspberry diretamente.
## 3. Memória de Curto Prazo
Mantida em RAM, com escrita periódica em banco quando relevante.
Conteúdo:
- Conversa atual (janela de contexto para a IA).
- Missão em execução e seu progresso.
- Estado atual do robô (posição estimada, sensores, modo).
- Últimos eventos recebidos do Event Bus.
- Pessoas e objetos atualmente visíveis.
A memória de curto prazo expira automaticamente por tempo ou por mudança de contexto.
## 4. Memória de Longo Prazo
Persistida em SQLite.
Categorias:
- Pessoas: identidade, nível de autorização, preferências, histórico de interações.
- Ambientes: nome, características visuais, objetos predominantes, mapa simplificado.
- Objetos: classe, descrição, ambiente associado, última localização conhecida.
- Histórico de missões: missão, resultado, duração, falhas.
- Eventos relevantes: alertas, quedas de comunicação, obstáculos recorrentes.
- Conhecimento aprendido: fatos informados pelos usuários e confirmados.
## 5. Estrutura das Tabelas
Tabelas mínimas obrigatórias:
- pessoas (id, nome, autorizacao, embedding_face, criado_em, atualizado_em)
- ambientes (id, nome, descricao, assinatura_visual, criado_em)
- objetos (id, classe, descricao, ambiente_id, ultima_posicao, visto_em)
- missoes (id, tipo, parametros, estado, resultado, inicio, fim)
- eventos (id, origem, tipo, payload, timestamp)
- conversas (id, pessoa_id, papel, texto, timestamp)
- conhecimento (id, chave, valor, fonte, confianca, criado_em)
- configuracao_memoria (chave, valor)
O modelo físico completo, índices e políticas de manutenção são definidos no Capítulo 15 (Database Core).
## 6. API de Memória
A API é o único ponto de acesso à memória. Operações mínimas:
- memory.remember(categoria, dados)
- memory.recall(categoria, filtro, limite)
- memory.update(categoria, id, dados)
- memory.forget(categoria, id)
- memory.context(pessoa_id) — monta o contexto para a IA.
- memory.stats() — estatísticas de uso.
Toda operação gera evento no Event Bus (memory.updated, memory.recall_executed) para fins de diagnóstico.
## 7. Aprendizado Contínuo
O Memory Core consolida informações da memória de curto prazo em longo prazo segundo regras:
- Pessoa vista repetidamente e nomeada por usuário autorizado → registro em pessoas.
- Ambiente identificado pelo Vision Core com alta confiança → registro em ambientes.
- Fato informado explicitamente ("lembre que...") → registro em conhecimento.
- Missões concluídas → registro em missoes com resultado.
Informações de baixa confiança permanecem em quarentena até confirmação.
## 8. Privacidade e Segurança
- Todos os dados permanecem locais, sem envio à nuvem.
- Dados de pessoas exigem confirmação de usuário autorizado.
- A API valida origem das solicitações.
- Operações de exclusão são registradas em log.
## 9. Requisitos de Desempenho
- Consultas de contexto em menos de 100 ms (incluindo a ida e volta pela rede local, que adiciona poucos milissegundos).
- Escrita assíncrona para não bloquear o Mission Core nem o laço de navegação.
- Recuperação automática após falha do banco (Capítulo 15).
## 10. EDR-0008 (atualizado por EDR-0019)
Decisão: centralizar toda a memória do sistema em um único banco SQLite no SSD do Raspberry, acessado exclusivamente pela API de memória.
Motivação:
- Fonte única de verdade.
- Operação offline.
- A memória permanente vive no corpo do robô: trocar o Notebook não apaga o que o robô aprendeu.
- Simplicidade de backup e recuperação.
- Baixo acoplamento entre módulos e armazenamento.
## Conclusão
O Memory Core transforma o ORION OS em um sistema que aprende. Ao concentrar o conhecimento em uma API única sobre SQLite, o robô ganha memória confiável, auditável e evolutiva, sem comprometer a modularidade da plataforma.
