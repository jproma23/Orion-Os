# ORION OS — System Engineering Specification (SES)
## Capítulo 15 — Database Core
Versão 1.0 — Especificação Oficial
## 1. Objetivo
O Database Core define o modelo de dados oficial do ORION OS, as regras de acesso ao SQLite, os índices, a política de backup e os procedimentos de recuperação. Ele é a fundação de persistência utilizada pelo Memory Core e pelos demais serviços, executando no Raspberry Pi (EDR-0019).
## 2. Tecnologia
- Banco: SQLite 3, arquivo único orion.db no SSD de 500 GB do Raspberry Pi (nunca no cartão SD).
- Modo WAL (Write-Ahead Logging) habilitado para leitura concorrente.
- Acesso exclusivo por um Database Manager no Raspberry; nenhum módulo abre o arquivo diretamente. O Notebook acessa via API de memória pela Ethernet.
- Migrações versionadas (schema_version) aplicadas no boot.
## 3. Modelagem Completa
Domínios e tabelas:
Identidade e memória:
- pessoas, ambientes, objetos, conhecimento, conversas (Capítulo 11).
Operação:
- missoes (id, tipo, parametros_json, estado, resultado, origem, inicio, fim)
- eventos (id, origem, tipo, payload_json, timestamp)
- telemetria (id, origem, metrica, valor, timestamp)
Sistema:
- logs (id, nivel, origem, mensagem, contexto_json, timestamp)
- diagnosticos (id, modulo, teste, resultado, detalhes, timestamp)
- configuracao (chave, valor, perfil, atualizado_em)
- schema_version (versao, aplicado_em)
Todas as tabelas possuem chave primária, timestamps e, quando aplicável, chaves estrangeiras com integridade referencial ativada.
## 4. Índices
Índices mínimos obrigatórios:
- eventos (timestamp), eventos (origem, tipo)
- telemetria (origem, metrica, timestamp)
- missoes (estado), missoes (inicio)
- conversas (pessoa_id, timestamp)
- logs (nivel, timestamp)
- objetos (ambiente_id)
Novos índices exigem justificativa registrada em EDR quando impactarem escrita.
## 5. Política de Retenção
- telemetria: 30 dias (agregação diária após 7 dias).
- eventos: 90 dias.
- logs: 30 dias (erros: 180 dias).
- conversas e memória de longo prazo: sem expiração automática.
A limpeza roda em tarefa noturna com VACUUM incremental.
## 6. Backup
- Backup automático diário via API de backup do SQLite (cópia consistente sem parar o sistema).
- Retenção: 7 diários + 4 semanais.
- Destino primário: diretório dedicado no SSD do Raspberry.
- Réplica cruzada: o backup diário é copiado ao Notebook pela Ethernet — se o SSD falhar, a memória do robô sobrevive no Notebook (e vice-versa).
- Evento database.backup_completed / database.backup_failed publicado a cada execução.
## 7. Recuperação
Procedimento no boot:
1. Verificar integridade (PRAGMA integrity_check).
2. Falha detectada → tentar recuperação via WAL.
3. Persistindo a falha → restaurar backup mais recente.
4. Sem backup válido → criar banco novo e publicar database.rebuilt (modo degradado de memória).
Todos os passos geram log e alerta na interface.
## 8. Desempenho
- Escritas em lote para telemetria e eventos.
- Transações curtas; nenhuma transação aberta durante chamadas de IA.
- Consultas do Memory Core respondidas em menos de 100 ms (com índices).
## 9. Eventos Publicados
database.ready
database.backup_completed
database.backup_failed
database.integrity_error
database.rebuilt
## 10. EDR-0012 (atualizado por EDR-0019)
Decisão: adotar SQLite em arquivo único com WAL no SSD do Raspberry, acesso centralizado e backups diários automáticos com réplica cruzada no Notebook.
Motivação:
- Zero administração e operação offline.
- SSD elimina o risco clássico de corrupção por desgaste do cartão SD.
- Confiabilidade comprovada para carga embarcada.
- Backup e restauração triviais (arquivo único).
- Migração futura para outro SGBD isolada no Database Manager.
## Conclusão
O Database Core garante que todo o conhecimento e histórico do ORION X esteja persistido de forma íntegra, recuperável e eficiente, sustentando o aprendizado contínuo definido pelo Memory Core.
