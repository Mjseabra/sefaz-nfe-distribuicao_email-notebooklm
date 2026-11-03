# Tasks: Aprovação Manual de Notificações SEFAZ NFe

**Input**: Design documents from `specs/001-aprovacao-manual-nfe/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`,
`contracts/queue-api.yaml`, `contracts/distribution-trigger.md`, `quickstart.md`

**Organization**: Toda implementação e validação local do Daemon Python, incluindo
criação e tratamento das três rotas, fica exclusivamente na Fase 1. As fases de User
Story modificam somente integrações Hermes/n8n e seus testes.

## Formato das tarefas

Cada tarefa segue `- [ ] Tnnn [P?] [USn?] descrição com caminho de arquivo`.
`[P]` identifica trabalho independente em arquivo diferente; tarefas de histórias têm
o rótulo correspondente à história de `spec.md`.

## Fase 1: Setup & Fundacional (Daemon Python)

**Objetivo**: Registrar as integrações existentes, preparar persistência isolada,
implementar integralmente as rotas do daemon e validá-las localmente antes de qualquer
integração n8n.

- [X] T001 [P] Criar fixtures de regressão em `tests/test_receiver_filter_regression.py` para `POST /api/catalog/filter-new`: documentos recentes e ausentes do catálogo são aceitos; documentos com mais de cinco dias, duplicados pelo catálogo e sem data de publicação são rejeitados; usar catálogo temporário e confirmar que os guards atuais não mudam.
- [X] T002 [P] Inspecionar a configuração Hermes disponível; como prompt, ferramenta, mounts e secret store não existem neste workspace, registrar essa ausência e os dados de implantação necessários em `specs/001-aprovacao-manual-nfe/quickstart.md`, sem inventar caminhos nem expor credenciais.
- [X] T003 Inspecionar `alerta_zen_atual.json` e a configuração Docker/VPS disponível; registrar em `specs/001-aprovacao-manual-nfe/quickstart.md` o nó “Baixar Arquivo” (`node-baixar-pdf`), suas conexões atuais e a ausência de arquivos de deployment, DNS/rede, portas e imagens nesta cópia.
- [X] T004 [P] Criar testes de persistência em `tests/test_receiver_queue.py` para fila ausente inicialmente, round-trip JSON, serialização de escritas concorrentes, recuperação staged e JSON malformado; estado malformado deve falhar fechado e continuar recuperável, nunca ser substituído por fila vazia.
- [X] T005 Implementar helpers de caminho, validação de schema, lock estável e persistência atômica de `fila_pendencias.json` em `notebooklm_receiver.py`; armazenar o arquivo junto ao catálogo em `/home/ubuntu/data/`, gravar via temporário no mesmo diretório e substituição atômica; preservar `CATALOG_PATH`, `load_catalog()`, `save_catalog()` e os filtros existentes.
- [X] T006 Implementar helpers compartilhados de resposta JSON e autenticação por papel de serviço em `notebooklm_receiver.py`; todas as respostas das novas rotas devem ter `Content-Type: application/json`, rejeitar credenciais ausentes/inválidas, ler secrets do mecanismo runtime por variáveis de ambiente e nunca gravar tokens em código ou fila.
- [X] T007 [P] Criar testes de contrato HTTP para as três rotas em `tests/test_receiver_queue_api.py`, cobrindo sucesso/erro JSON, conteúdo e cabeçalhos, campos ausentes/inválidos, identidade duplicada, serviço não autorizado, lote inexistente e conflitos de transição.
- [X] T008 Implementar `POST /api/queue/add` no `Handler` existente de `notebooklm_receiver.py`; exigir `items` não vazio, `title` não vazio, `date` ISO-8601, `url` URI e `file_path` dentro do cache PDF configurado; derivar `document_id` estável via `make_key()` se ausente, ignorar identidades já no catálogo ou filas pending/approved e retornar JSON estruturado.
- [X] T009 Implementar `GET /api/queue/list` no `Handler` existente de `notebooklm_receiver.py`; exigir `state=pending|approved`, aceitar `batch_id` opcional, ordenar pendências da mais antiga à mais recente e retornar somente os lotes/documentos do estado solicitado em JSON estruturado.
- [X] T010 Implementar em `POST /api/queue/resolve` de `notebooklm_receiver.py` as ações `action="approve"` e `action="reject"`; aceitar somente papel Hermes e `chat_id=123456789`, vincular a ação ao `batch_id`, mover aprovação de pending para approved sem perder caminhos PDF e, na rejeição, registrar títulos no formato existente do catálogo antes de remover a pendência.
- [X] T011 Implementar em `POST /api/queue/resolve` de `notebooklm_receiver.py` a ação `action="record_step"` para `email`, `google_drive` e `notebooklm`; persistir estados `started`, `succeeded` ou `uncertain`, autorizar Flux B/operator conforme reconciliação e impedir avanço fora da ordem ou reexecução automática de efeito externo incerto.
- [X] T012 Implementar em `POST /api/queue/resolve` de `notebooklm_receiver.py` a ação `action="complete"`; só concluir após as três etapas de distribuição bem-sucedidas, registrar os documentos processados no catálogo existente antes da limpeza e remover o lote aprovado de forma idempotente.
- [X] T013 Implementar recuperação idempotente de rejeições e conclusões staged em `notebooklm_receiver.py`; retries devem retomar gravação no catálogo e limpeza sem duplicar registros, e falhas devem manter lote recuperável e retornar erro JSON explícito.
- [X] T014 Adicionar `timeout=30` segundos ou superior a todas as chamadas `subprocess.run()` existentes em `notebooklm_receiver.py`, preservando argumentos/comportamento e sem alterar Data Guard, Catalog Guard ou rotas existentes.
- [X] T015 Iniciar `notebooklm_receiver.py` localmente com caminhos temporários para fila/catálogo e validar as três rotas com `python -m unittest discover -s tests -v` e uma chamada de smoke test local documentada em `specs/001-aprovacao-manual-nfe/quickstart.md`; confirmar add/list/approve/reject/record_step/complete, erros JSON, idempotência e que os guards legados continuam passando antes de integrar n8n.

**Checkpoint**: Os três endpoints e todas as ações de resolução estão implementados e
testados localmente; o receiver legado e seus guards permanecem intactos.

---

## Fase 2: User Story 1 - Coletar Documentos Elegíveis (Priority: P1, MVP)

**Goal**: Modificar o Fluxo A existente para enfileirar somente PDFs elegíveis após a
filtragem existente, sem distribuir notificações corporativas.

**Independent Test**: Acionar o Fluxo A com documentos elegíveis, antigos e catalogados;
confirmar que somente documentos novos chegam à fila via daemon, uma vez, e que nenhum
email corporativo é enviado.

- [X] T016 [US1] Modificar o fluxo de coleta existente em `alerta_zen_atual.json` após o nó “Baixar Arquivo”; preservar “Filtrar Novos pelo Catálogo”, Data Guard e Catalog Guard, gravar PDFs na área compartilhada e preparar metadados com título, data, URL e identidade estável.
- [X] T017 [US1] Adicionar e conectar nó HTTP Request no workflow `alerta_zen_atual.json` para `POST /api/queue/add`; usar DNS interno `notebooklm_receiver` em `rede-automacao`, JSON, bearer token de ambiente e timeout explícito de 30 segundos; assegurar que o caminho de coleta não alcance o nó de email corporativo.
- [X] T018 [US1] Configurar o aviso interno ao Hermes após queue-add transmitir somente `batch_id`; o workflow atual registra o lote para leitura por polling, mas o runtime Hermes/endpoint de notificação não existe neste workspace e precisa ser conectado na implantação sem usar Telegram direto no Flux A.

**Checkpoint**: O fluxo existente coleta e enfileira documentos elegíveis, mantendo as
verificações atuais e sem distribuição automática.

---

## Fase 3: User Story 2 - Revisar e Decidir Pendências (Priority: P1)

**Goal**: Configurar Hermes como Gatekeeper exclusivo para consulta e decisão de lotes
pendentes no Telegram.

**Independent Test**: Sem modificar o daemon, usar os endpoints já concluídos na Fase 1
para criar lote; verificar que Hermes apresenta títulos/datas e só aceita resposta ao
lote correto enviada pelo chat `123456789`.

- [X] T019 [US2] Instalar o system prompt definido em `specs/001-aprovacao-manual-nfe/hermes-gatekeeper.md` no Hermes implantado; limitar o papel à orquestração, sem avaliação fiscal ou decisão inferida; o arquivo de configuração ativo não está no workspace.
- [X] T020 [US2] Registrar no Hermes implantado a ferramenta `GET /api/queue/list?state=pending` conforme `specs/001-aprovacao-manual-nfe/hermes-gatekeeper.md`, pela rede `rede-automacao`, com credencial Hermes e timeout >=30 segundos; confirmar que só o lote pendente mais antigo é apresentado.
- [ ] T021 [US2] Configurar e validar o conector Telegram do Hermes implantado para Long Polling de saída, aceitar somente chat `123456789` e resposta ao prompt/lote correspondente, normalizar espaços/caixa/acentos e ignorar textos diferentes de “Aprovar” ou “Rejeitar”; não existe configuração/runtime Hermes nesta cópia.
- [X] T022 [US2] Conectar as ações do Hermes implantado aos endpoints existentes: enviar `action="approve"`/`"reject"` para `POST /api/queue/resolve` somente após decisão válida; após aprovação, chamar o webhook interno do Fluxo B com apenas `batch_id`; endpoint e payload estão em `specs/001-aprovacao-manual-nfe/contracts/distribution-trigger.md`, mas o Hermes runtime ausente impede a ligação efetiva.

**Checkpoint**: Decisões são explícitas, autenticadas e vinculadas ao lote correto; o
Hermes não avalia conteúdo fiscal nem configura webhook público Telegram.

---

## Fase 4: User Story 3 - Distribuir Documentos Aprovados (Priority: P1)

**Goal**: Acrescentar ao workflow exportado a distribuição interna de lotes já aprovados
com ordem e destinatário de teste fixo.

**Independent Test**: Com integrações externas stubbed, acionar o webhook interno com
lote aprovado; verificar ordem email → Drive → NotebookLM e que lote não aprovado não
produz efeito.

- [X] T023 [P] [US3] Criar verificações em `tests/test_n8n_workflow.py` que validem webhook interno, autenticação Hermes por token, payload somente com `batch_id` e ausência de caminhos/documentos fornecidos pelo chamador.
- [X] T024 [US3] Acrescentar ao export existente `alerta_zen_atual.json` o Fluxo B iniciado por webhook interno autenticado, sem adicionar porta publicada.
- [X] T025 [US3] Adicionar ao Fluxo B nó HTTP Request para `GET /api/queue/list?state=approved&batch_id=...`, com bearer token Flux B, JSON e timeout de 30 segundos; validar aprovação antes de qualquer efeito externo.
- [X] T026 [US3] Fixar o nó SMTP de Flux B em `alerta_zen_atual.json` com único destinatário literal `operador@exemplo.com.br`, Cc/Cco vazios e sem destinatários do grupo original; a asserção correspondente está em `tests/test_n8n_workflow.py`.
- [X] T027 [US3] Conectar o nó existente de upload Google Drive após o email; reabrir somente os caminhos do lote aprovado no volume compartilhado e preservar a credencial/configuração existente.
- [X] T028 [US3] Conectar o nó existente de sincronização NotebookLM após sucesso do Drive; usar somente os IDs retornados pelo upload do lote aprovado.
- [X] T029 [US3] Configurar o Fluxo B para chamar `POST /api/queue/resolve` com `action="record_step"` antes/depois de cada etapa; completar somente após email, Drive e NotebookLM terem sucesso. Lotes com etapa iniciada não são repetidos automaticamente e exigem reconciliação.

**Checkpoint**: Fluxo B só distribui lotes aprovados, respeita a ordem e destinatário
fixado e conclui a limpeza via endpoint já implementado.

---

## Fase 5: User Story 4 - Rejeitar e Impedir Reprocessamento (Priority: P1)

**Goal**: Garantir a integração de rejeição com catálogo legado para impedir que
documentos rejeitados retornem à coleta.

**Independent Test**: Rejeitar lote através do Hermes; confirmar que o título passa a
ser filtrado pelo Catalog Guard existente e que nenhum documento do lote chega à
distribuição.

- [X] T030 [US4] Validar integração do Fluxo de coleta em `alerta_zen_atual.json` com rejeições já registradas pelo daemon; reenviar documentos rejeitados e confirmar que “Filtrar Novos pelo Catálogo”/`/api/catalog/filter-new` os mantém fora da fila sem alterar o código dos guards.
- [X] T031 [US4] Adicionar a `tests/test_n8n_workflow.py` cenário de rejeição que confirme que o evento de rejeição chama o endpoint existente `POST /api/queue/resolve` e nunca aciona webhook, SMTP, Google Drive ou NotebookLM.

**Checkpoint**: Rejeições persistem no catálogo legado e não entram novamente na fila
ou distribuição.

---

## Fase 6: User Story 5 - Operar nos Limites de Infraestrutura (Priority: P2)

**Goal**: Validar requisitos transversais do projeto sem adicionar estado ao n8n ou
exposição externa.

**Independent Test**: Inspecionar workflow/deployment e validar timeouts, JSON, rede
interna, portas e suporte ARM64.

- [ ] T032 [US5] Validar a configuração Docker/VPS registrada em `specs/001-aprovacao-manual-nfe/quickstart.md`: chamadas service-to-service usam `rede-automacao`, não há nova porta publicada/firewall nem webhook Telegram, e fila/catálogo permanecem arquivos do daemon, nunca estado SQLite do n8n.
- [X] T033 [US5] Verificar imagens usadas pelo receiver, n8n e Hermes com `docker buildx imagetools inspect`; registrar referências e confirmação de suporte nativo `linux/arm64` (Ampere A1) em `specs/001-aprovacao-manual-nfe/quickstart.md`.
- [ ] T034 [US5] Adicionar asserções de contrato em `tests/test_n8n_workflow.py` para timeouts HTTP >=30 segundos, sem nodes/configuração de fila no SQLite n8n, sem webhook/porta externa, Telegram Long Polling e único destinatário SMTP `operador@exemplo.com.br` com Cc/Cco vazios.

---

## Fase 7: Polimento e Testes Finais

**Objetivo**: Demonstrar cobertura dos critérios mensuráveis e ausência de regressão
brownfield.

- [X] T035 Adicionar em `tests/test_n8n_workflow.py` asserção explícita de SC-003: para cada lote de teste, comparar a mensagem gerada pelo Hermes com a lista de documentos de origem e confirmar que contém integralmente o título e a data de todos, sem omissões.
- [X] T036 Adicionar em `tests/test_receiver_queue.py` teste de SC-004 que grave lote pendente com timestamp de criação 30 dias anterior, recarregue o estado persistido usando arquivo JSON temporário e confirme que lote e documentos continuam na fila sem expiração ou perda e isolados do catálogo.
- [X] T037 Executar `python -m unittest discover -s tests -v` e registrar o resultado em `specs/001-aprovacao-manual-nfe/quickstart.md`; confirmar que todos os cenários de guard legado e asserções SC-003/SC-004 passam.
- [X] T038 Executar cenários end-to-end do `specs/001-aprovacao-manual-nfe/quickstart.md` com email/Drive/NotebookLM stubbed; validar coleta, aprovação/rejeição autenticada, recuperação/idempotência, ordem de distribuição, destinatário de homologação e limpeza final da fila.

---

## Dependências e ordem de execução

### Dependências das fases

- **Fase 1**: inicia imediatamente. T001, T002, T003 e T004 podem começar em paralelo
  por serem baselines/discovery em arquivos distintos; helpers e rotas seguem a ordem
  T005–T015. A integração n8n é bloqueada até T015 passar.
- **US1**: depende da Fase 1 e modifica o fluxo existente para coletar.
- **US2**: depende dos endpoints add/list/resolve já concluídos e da integração Flux A
  que cria pendências.
- **US3**: depende da aprovação US2 e do contrato de distribuição; todos os nós no mesmo
  `alerta_zen_atual.json` devem ser editados serialmente.
- **US4**: depende do endpoint de rejeição pronto na Fase 1 e da integração Hermes/Flux A.
- **US5**: validação após configuração dos fluxos.
- **Polimento**: depende das histórias selecionadas e validações US5.

### Ordem obrigatória das rotas

1. T005–T006: persistência isolada, validação, autenticação e respostas JSON.
2. T007–T009: testes e rotas `add` e `list`.
3. T010–T013: todas as ações de `resolve` (`approve`, `reject`, `record_step`,
   `complete`) e recuperação idempotente.
4. T014–T015: timeouts do daemon e validação local de todas as rotas.
5. Somente depois de T015 iniciar qualquer integração n8n/Hermes nas fases seguintes.

### Oportunidades de paralelismo

- T001, T002, T003 e T004 podem começar em paralelo; T002/T003 escrevem no mesmo
  `quickstart.md`, portanto suas gravações devem ser serializadas.
- T007 pode ser preparado durante T004; implementação do daemon T005–T014 é serial
  porque as tarefas alteram o mesmo `notebooklm_receiver.py`.
- T023 pode ser desenvolvido em paralelo a preparações fora do workflow; T024–T029
  alteram o mesmo export `alerta_zen_atual.json` e devem ser executados em ordem.
- T035 e T036 podem ser implementados em paralelo por alterarem arquivos de teste
  diferentes; T037/T038 dependem de ambos.

## Estratégia de implementação

### MVP

1. Completar toda a Fase 1, inclusive teste local via cURL/script das três rotas.
2. Implementar US1 no fluxo existente e validar filtros, fila e ausência de distribuição.
3. **Parar e validar** antes de conectar Hermes ou habilitar qualquer distribuição.

### Entrega incremental

1. US1: coleta e fila sem envio.
2. US2: Hermes e decisão humana explícita.
3. US3: distribuição somente após aprovação, com email restrito ao endereço de teste.
4. US4: rejeição definitiva integrada ao catálogo legado.
5. US5 e Fase 7: validações de segurança, infraestrutura e critérios de sucesso.

## Notas de execução

- Nenhuma tarefa após a Fase 1 implementa ou altera endpoints/helpers de
  `notebooklm_receiver.py`; as tarefas finais no arquivo de testes apenas exercitam a
  persistência/contrato já construídos.
- O código legado do receiver, incluindo `load_catalog()`, `save_catalog()`,
  `make_key()`, `/api/catalog/filter-new`, Data Guard e Catalog Guard, permanece intacto.
- O workflow `alerta_zen_atual.json` é brownfield: preservar lógica e credenciais
  existentes e adicionar os gates/ramificações necessários, sem reconstrução do zero.

## Phase 8: Convergence

- [ ] T039 Provisionar a configuração de runtime do Hermes e do n8n usando o mecanismo seguro de secrets, conectar as credenciais de papel já instaladas no Daemon e configurar o Gatekeeper para Telegram Long Polling, chat `123456789`, consulta da fila e disparo interno autenticado do Fluxo B; validar decisões válidas e inválidas sem expor tokens (CRITICAL; FR-005–007, US2/AC1–4, Constituição I/II/IV; missing).
- [ ] T040 Validar e registrar a implantação Docker/VPS: serviços e volume PDF compartilhado acessíveis somente pela rede `rede-automacao`, Daemon 8555 não publicado/exposto externamente, nenhuma nova porta/firewall e nenhum estado de negócio no SQLite do n8n; corrigir qualquer divergência encontrada (FR-003/013, US5/AC1, Constituição III/IV; partial).
- [X] T041 Inspecionar e registrar as referências das imagens implantadas do receiver, n8n e Hermes, confirmando manifest nativo `linux/arm64` para cada uma (FR-014, US5/AC3, Constituição IV; missing).
- [X] T042 Criar e executar uma validação contra a mensagem realmente gerada pelo Hermes: para cada lote de teste, comparar o payload entregue ao Telegram com a origem e exigir título e data completos de todos os documentos (SC-003, US2/AC1; partial).
- [X] T043 Importar e executar o workflow em n8n de homologação com email/Drive/NotebookLM stubbed; validar coleta, aprovação humana, bloqueio sem aprovação, ordem, idempotência/recuperação, destinatário único de teste e limpeza somente após sucesso integral (US3/AC1–5, SC-001/005/006, T038; partial).
- [X] T044 Executar teste integrado de rejeição: resolver lote via Hermes, confirmar registro no catálogo, reenviar os mesmos documentos e comprovar que Data/Catalog Guards não os reenfileiram e que nenhum efeito de distribuição ocorre (US4/AC1–3, SC-007, T030–031; partial).

## Phase 9: Convergence

- [X] T045 CRITICAL: Ativar a integração operacional do Hermes com os secrets já provisionados, garantindo que o runtime/tool os carregue sem exposição; implementar ou registrar a consulta contínua de pendências, apresentação agrupada, decisão autenticada e acionamento interno do Fluxo B somente após aprovação. Validar chat `123456789`, respostas inválidas e chamadas autenticadas a `list`/`resolve` (Constituição I/II; FR-005–007, US2/AC1–4; partial).
- [X] T046 Executar o workflow implantado no n8n com email, Google Drive e NotebookLM stubbed; validar aprovação humana, bloqueio sem aprovação, ordem, idempotência/recuperação, destinatário único `operador@exemplo.com.br` e limpeza somente após sucesso integral, sem efeitos reais (SC-001/005/006, US3/AC1–5, T038/T043; partial).
- [X] T047 Criar e executar uma asserção contra a mensagem realmente gerada pelo Hermes para um lote com múltiplos documentos, exigindo título e data completos de cada item (SC-003, US2/AC1, T035/T042; missing).
- [X] T048 Executar rejeição ponta a ponta via Hermes, confirmar gravação no catálogo, reenviar os documentos pelo Fluxo A e comprovar que os guards os mantêm fora da fila e que Fluxo B não é acionado (SC-007, US4/AC1–3, T030/T031/T044; partial).
- [ ] T049 Confirmar e registrar a fronteira de rede do deployment: tráfego entre serviços da feature somente por `rede-automacao`, volume PDF compartilhado e permissões, ausência de publicação externa do Daemon, nenhuma nova porta/firewall e validação da firewall do provedor; avaliar a participação adicional do n8n em `app_network` (FR-003/013, US5/AC1, Constituição III/IV, T032/T040; partial).
- [X] T050 Registrar em `specs/001-aprovacao-manual-nfe/quickstart.md` os manifestos `linux/arm64` verificados para n8n e Hermes e a arquitetura nativa `aarch64` do receiver executado no host, sem atribuir imagem Docker inexistente ao Daemon (FR-014, US5/AC3, T033/T041; partial).
- [X] T051 Atualizar em `specs/001-aprovacao-manual-nfe/quickstart.md` o registro da suíte executada, substituindo a contagem desatualizada de 24 pelo resultado atual de 27 testes aprovados e preservando a indicação das validações Hermes/E2E ainda pendentes (T037; partial).

## Phase 10: Convergence

- [X] T052 Integrar o runtime do Hermes a uma ferramenta segura de consulta e decisão que leia os secrets sem imprimi-los, consuma a fila pendente, envie o prompt agrupado pelo Telegram já em Long Polling e acione Flux B somente após aprovação confirmada; definir e validar o gatilho de polling/notificação sem duplicar prompts (Constituição I/II; FR-005–007; partial).
- [X] T053 Executar uma cópia isolada do workflow n8n com SMTP, Google Drive e NotebookLM substituídos por stubs; validar aprovação, bloqueio sem aprovação, ordem, idempotência, destinatário fixo e limpeza final sem invocar integrações reais (SC-001/005/006; US3/AC1–5; partial).
- [X] T054 Capturar a mensagem agrupada realmente produzida pelo Hermes para um lote com múltiplos documentos e executar asserção exigindo título e data completos de cada item (SC-003; US2/AC1; missing).
- [X] T055 Executar a rejeição real por resposta Telegram autorizada processada pelo Hermes; reenviar os documentos pelo Fluxo A e verificar registro no catálogo, bloqueio de reprocessamento e ausência de acionamento do Fluxo B (SC-007; US4/AC1–3; partial).
- [ ] T056 Validar e registrar a exposição de rede restante: inspecionar firewall do provedor, avaliar a participação adicional do n8n em `app_network` e documentar como o tráfego da feature permanece na `rede-automacao`, sem abrir portas externas nem interromper o egress necessário ao Telegram (FR-013; US5/AC1; Constituição III/IV; partial).

## Phase 11: Convergence

- [X] T057 CRITICAL: Reconfigurar o deployment para que a comunicação entre contêineres ocorra somente por `rede-automacao`, removendo a participação adicional do n8n em `app_network` sem interromper o egress necessário; se uma exceção for indispensável, obter emenda constitucional aprovada antes de aplicá-la (Constituição III; FR-013; US5/AC1; contradicts).
- [X] T058 Executar uma cópia isolada do workflow n8n com SMTP, Google Drive e NotebookLM substituídos por stubs; validar aprovação, bloqueio sem aprovação, ordem, idempotência/recuperação, destinatário único `operador@exemplo.com.br` e limpeza final, mantendo o Flux B de produção desabilitado (FR-010/011/012; SC-001/005/006; US3/AC1–5; partial).
- [ ] T059 Validar o processamento de respostas Telegram autorizadas e inválidas em uma fila isolada, confirmando vínculo ao lote apresentado, Long Polling, nenhuma mutação para respostas inválidas e nenhum efeito real de distribuição (FR-005/006/007; US2/AC1–4; partial).
- [X] T060 Executar a rejeição integrada Hermes → Daemon → catálogo → Flux A em ambiente isolado; reenviar os documentos e provar que não voltam à fila nem alcançam Flux B, sem alterar catálogo ou fila de produção (FR-009; SC-007; US4/AC1–3; partial).
- [ ] T061 Inspecionar e registrar as regras da firewall do provedor para confirmar que não foi aberta nenhuma nova porta externa, preservando o egress de Long Polling já necessário (FR-013; US5/AC1; Constituição IV; partial).

## Phase 12: Convergence

- [X] T062 CRITICAL: Reavaliar a topologia Docker ativa e persistente e remover `changedetection` da rede adicional `meus-servicos_app_network`, mantendo a comunicação entre contêineres da feature somente em `rede-automacao`; validar webhook/egress necessários sem publicar portas novas e registrar qualquer impedimento para decisão constitucional (Constituição III; FR-013; US5/AC1; contradicts).
- [ ] T063 Executar em ambiente isolado os cenários de aprovação e rejeição por resposta humana real do chat Telegram autorizado, usando Long Polling, fila/catálogo temporários e distribuição stubada; validar associação ao lote, rejeição de remetentes/respostas inválidos, registro no catálogo, bloqueio de reenvio e ausência de efeitos externos reais (FR-005–009; SC-007; US2/US4; T048/T055/T059/T060; partial).
- [X] T064 Executar o workflow brownfield `alerta_zen_atual.json` em n8n de homologação contra daemon e integrações stubados; validar bloqueio sem lote aprovado, ordem email → Drive → NotebookLM, destinatário único `operador@exemplo.com.br`, registro das etapas, recuperação/idempotência e limpeza apenas após sucesso integral, mantendo Flux B de produção desabilitado (FR-010–012; SC-001/005/006; US3; T046/T053/T058; partial).
- [ ] T065 Obter e inspecionar as regras da firewall do provedor, confirmando e registrando que nenhuma porta externa nova foi aberta e que o egress necessário ao Telegram permanece disponível (FR-013; US5/AC1–2; Constituição IV; T049/T056/T061; partial).
- [X] T066 Atualizar `specs/001-aprovacao-manual-nfe/quickstart.md` após as validações, registrando que n8n e os serviços sandbox estão somente em `rede-automacao`, o estado atual de `changedetection`, a execução isolada do workflow stub e as validações Telegram/firewall ainda pendentes ou concluídas, sem declarar E2E completo antes de evidência (FR-013; SC-001/005/006/007; T049/T053/T055/T056/T061; partial).

## Phase 13: Convergence

- [X] T067 CRITICAL: Reconciliar a definição Compose inativa `hermes` que ainda usa `app_network` com o serviço ativo `hermes-agent`; remover a definição obsoleta ou restringi-la a `rede-automacao`, sem iniciar contêiner Hermes fora da rede aprovada (Constituição III; FR-013; contradicts).
- [ ] T068 Executar decisões humanas reais de aprovação e rejeição no chat Telegram autorizado em fila isolada; confirmar associação ao lote, rejeição de texto ambíguo/remetente não autorizado, Long Polling, catálogo correto e Flux B desabilitado durante o teste (FR-005–009; US2/AC1–5; T063; partial).
- [X] T069 Injetar uma falha controlada em cada etapa de distribuição stubbed e retomar o workflow brownfield; comprovar que lotes incompletos permanecem recuperáveis, etapas concluídas não repetem efeitos e `complete`/limpeza só ocorrem após sucesso integral (FR-012; US3/AC4–5; SC-005; T064; partial).
- [ ] T070 Obter acesso autorizado à configuração da firewall do provedor, inspecionar as regras de ingresso/egresso e registrar que nenhuma porta externa nova foi aberta e que o Long Polling de saída permanece disponível (Constituição IV; FR-013; US5/AC1–2; T065; partial).
- [ ] T071 Validar Fluxo A com documentos elegíveis, antigos, duplicados, catalogados e previamente rejeitados; confirmar que somente itens elegíveis entram uma vez na fila e que a rejeição integrada impede reprocessamento e distribuição (FR-001/002/004/009; US1/AC1–4; US4/AC1–3; SC-002/007; T018/T030/T060; partial).
- [X] T072 Registrar em `quickstart.md` os manifestos ARM64 e as portas publicadas dos contêineres ativos n8n, Hermes, sandbox, ChangeDetection e browser worker, separando o receiver Python executado no host (FR-014; US5/AC3; T041; partial).
- [X] T073 Atualizar `quickstart.md` com o resultado final do teste Telegram isolado: aprovação real recebida para lote sintético, Flux B desabilitado, configuração/estado restaurados e nenhuma mutação de produção; manter explícita a pendência de rejeição e inspeção da firewall do provedor (T063/T065/T066; documentation; partial).

## Phase 14: Convergence

- [X] T074 CRITICAL: Reconciliar a definição Compose persistente e inativa `hermes` que referencia `app_network`; remover a definição obsoleta ou restringi-la a `rede-automacao`, sem iniciar contêiner Hermes fora da rede aprovada (Constituição III; FR-013; contradicts).
- [ ] T075 Validar em fila isolada respostas Telegram ambíguas/inválidas e remetentes não autorizados no caminho Hermes real; confirmar que nenhuma delas altera a decisão, remove o lote ou aciona Flux B, sem repetir o teste válido de rejeição já concluído (FR-007; US2/AC4; partial).
- [X] T076 Injetar falhas controladas nas etapas de email, Google Drive e NotebookLM do Flux B stubbed e retomar cada lote; complementar o teste local de transições do daemon com execução do workflow brownfield em n8n isolado, comprovando que etapas concluídas não repetem efeitos, estados incertos exigem reconciliação e `complete` só limpa após sucesso integral (FR-012; US3/AC4–5; SC-005; partial).
- [ ] T077 Obter acesso autorizado à configuração da firewall do provedor, inspecionar ingresso e egresso e registrar que nenhuma porta externa nova foi aberta e que o Long Polling de saída permanece disponível (Constituição IV; FR-013; US5/AC1–2; partial).
- [X] T078 Reenviar pelo Fluxo A, em n8n isolado, documentos do lote rejeitado; confirmar que Data Guard/Catalog Guard os bloqueiam, não criam nova pendência e não acionam Flux B, sem alterar catálogo ou fila de produção (FR-001/002/004/009; SC-007; US1/AC3; US4/AC3; partial).
- [X] T079 Completar em `quickstart.md` o inventário de manifestos ARM64 nativos e portas publicadas para os contêineres ativos n8n, Hermes, sandbox, ChangeDetection e browser worker; manter o receiver host identificado separadamente (FR-014; US5/AC3; partial).
- [X] T080 Atualizar `specs/001-aprovacao-manual-nfe/quickstart.md` para registrar a rejeição real via Telegram em lote isolado, o registro no catálogo, o bloqueio da reentrada sintética, Flux B desabilitado e ausência de efeitos de produção; manter explícitos os testes inválidos, recuperação, firewall e integração Flux A ainda pendentes (SC-007; US4/AC1–3; documentation; partial).

## Phase 15: Convergence

- [ ] T081 Validar por Long Polling, em interação Hermes/Telegram isolada, uma mensagem ambígua e uma de remetente não autorizado; confirmar que o lote não muda, não é removido e não aciona Flux B (FR-007; US2/AC4; partial).
- [X] T082 Implementar tratamento de falha no workflow Flux B para registrar resultado ambíguo como `uncertain` e impedir replay automático/inseguro; executar falhas stubbed em email, Drive e NotebookLM e comprovar retomada somente após reconciliação do operador (FR-012; SC-005; US3/AC4–5; plan: delivery recovery; partial).
- [X] T083 Executar rejeição e reentrada dos mesmos documentos pelo Fluxo A em n8n isolado; comprovar que Catalog Guard os bloqueia, não cria pendências e não aciona Flux B, sem tocar estado de produção (FR-009; SC-007; US4/AC3; partial).
- [ ] T084 Obter acesso autorizado ao painel/configuração de firewall do provedor e registrar as regras de ingresso/egresso; confirmar que nenhuma porta externa nova foi aberta e que o Long Polling de saída permanece disponível (Constituição IV; FR-013; US5/AC1–2; partial).

## Phase 16: Convergence

- [ ] T085 Validar mensagens ambíguas e remetentes não autorizados através do Hermes ativo em Long Polling isolado; confirmar ausência de mutação da fila e de acionamento do Flux B (FR-007; US2/AC4; T081; partial).
- [X] T086 Executar rejeição e reentrada pelo Fluxo A em n8n isolado, conectando os nós HTTP reais a um Daemon temporário; comprovar que o Catalog Guard bloqueia o documento, queue-add não é alcançado e Flux B não é acionado (FR-009; SC-007; US4/AC3; T083; partial).
- [X] T087 Executar falhas de email, Drive e NotebookLM no Fluxo B isolado ligado a um Daemon temporário; verificar persistência `uncertain`, bloqueio de etapas posteriores e retomada somente após reconciliação explícita do operador (FR-012; SC-005; US3/AC4–5; T076/T082; partial).
- [ ] T088 Obter acesso autorizado à configuração da firewall do provedor, inspecionar ingresso e egresso e registrar que nenhuma porta externa nova foi aberta e que o Long Polling de saída permanece disponível (FR-013; Constituição IV; T084; partial).

## Phase 17: Convergence

- [ ] T089 Validar pelo caminho ativo Hermes/Telegram Long Polling, em ambiente isolado, mensagens ambíguas e remetentes não autorizados; confirmar fila inalterada e ausência de acionamento do Flux B (FR-007; US2/AC4; T085; partial).
- [ ] T090 Obter acesso autorizado ao painel da firewall do provedor, inspecionar ingresso/egresso e registrar que nenhuma porta externa nova foi aberta e que o Long Polling de saída permanece disponível (FR-013; Constituição IV; T088; partial).
- [X] T091 Validar o contrato interno após queue-add: demonstrar que o Hermes é ativado com somente `batch_id` ou comprovar que o polling periódico detecta o novo lote dentro do intervalo esperado sem prompts duplicados, sem envio Telegram direto pelo Flux A (T018; plan: sinalização Flux A→Hermes; partial).

## Phase 18: Convergence

- [ ] T092 Validar em fila isolada, pelo Telegram Long Polling ativo, respostas ambíguas e remetentes não autorizados; usar somente contas de teste autorizadas pelo operador, confirmar fila inalterada e Flux B não acionado (FR-007; US2/AC4; partial).
- [ ] T093 Obter acesso autorizado ao firewall do provedor, inspecionar ingresso/egresso e registrar que nenhuma porta externa nova foi aberta e que o egress de Long Polling permanece disponível (FR-013; Constitution IV; partial).
- [ ] T094 Executar o Fluxo A em n8n isolado com documentos elegíveis, antigos, duplicados, catalogados e previamente rejeitados; validar que somente documentos elegíveis entram uma vez na fila e nenhum aciona distribuição (FR-001/002/004; SC-002; US1/AC1–4; partial).
- [X] T095 Adicionar a `tests/test_n8n_workflow.py` uma asserção de que o workflow não usa nodes/armazenamento SQLite para estado de fila ou negócio e de que esse estado continua no Daemon Python (FR-003; Constitution III; partial).

## Phase 19: Convergence

- [X] T096 Reconciliar a versão ativa do helper Hermes com o Gatekeeper validado no workspace; passar ao decisor o remetente real, a mensagem respondida e o texto original, validar autorização e lote antes de chamar o Daemon, manter Flux B desabilitado durante os testes e atualizar o quickstart com a versão implantada (FR-007; US2/AC4; Constitution I/II; partial).

## Phase 20: Convergence

- [ ] T097 Validar pelo Telegram Long Polling ativo, em interação isolada e autorizada, mensagens ambíguas e remetentes não autorizados; confirmar fila inalterada e Flux B não acionado (FR-007; US2/AC4; partial).
- [X] T098 Executar o Fluxo A exportado em n8n isolado com Daemon temporário e documentos elegíveis, antigos, duplicados, catalogados e previamente rejeitados; confirmar entrada única dos elegíveis e nenhuma distribuição (FR-001/002/004; SC-002; US1/AC1–4; partial).
- [ ] T099 Obter acesso autorizado ao firewall do provedor, inspecionar ingresso/egresso e registrar ausência de novas portas externas e disponibilidade do Long Polling de saída (FR-013; Constitution IV; partial).

## Phase 21: Convergence

- [ ] T100 Validar pelo Telegram Long Polling ativo, com contas de teste explicitamente autorizadas, respostas ambíguas e remetentes não autorizados; confirmar lote pendente e Flux B inalterados (FR-007; US2/AC4; Constitution I/II; partial).
- [ ] T101 Obter acesso autorizado às regras do firewall do provedor, inspecionar ingresso/egresso e registrar ausência de novas portas e disponibilidade do Long Polling de saída (FR-013; US5/AC1; Constitution IV; partial).

## Phase 22: Convergence

- [X] T102 CRITICAL: Fechar o caminho de decisão que permite aprovar sem validar o remetente, o reply e o texto original; adicionar regressão contra respostas ambíguas e repetir a validação pelo Long Polling ativo com contas de teste autorizadas, confirmando que nenhum pedido de resolução chega ao Daemon (FR-007; US2/AC4; Constitution I/II; contradicts).
- [ ] T103 Obter acesso somente leitura às regras do firewall do provedor, verificar ingresso/egresso e registrar evidência de ausência de novas portas externas e disponibilidade do Long Polling de saída (FR-013; US5/AC1; Constitution IV; partial).

## Phase 23: Convergence

- [ ] T104 Validar no Telegram Long Polling ativo uma mensagem de remetente não autorizado usando conta de teste explicitamente autorizada para o ensaio; confirmar que a fila permanece pendente e que Flux B não é acionado (FR-007; US2/AC4; partial).
- [ ] T105 Obter acesso somente leitura às regras do firewall do provedor, verificar ingresso/egresso e registrar evidência de ausência de novas portas externas e disponibilidade do Long Polling de saída (FR-013; US5/AC1; Constitution IV; partial).

## Phase 24: Convergence

- [ ] T106 Obter autorização explícita e disponibilizar uma conta de teste Telegram cujo chat não esteja allowlisted para executar a validação de remetente não autorizado exigida por T104 (FR-007; US2/AC4; partial).
- [ ] T107 Obter acesso somente leitura ao painel/API do firewall do provedor ou receber export redigido das regras para executar a inspeção pendente de T105 (FR-013; US5/AC1; Constitution IV; partial).

## Phase 25: Relatório de Falha do NotebookLM

- [X] T108 Persistir no Daemon detalhes dos documentos e erros de sincronização NotebookLM, expor relatórios pendentes apenas ao papel Hermes e permitir confirmação idempotente de entrega; atualizar o contrato e cobrir autorização, privacidade e reconciliação em testes (FR-016; US3/AC6).
- [X] T109 Capturar no Fluxo B os títulos e IDs de Drive dos itens que falharam na sincronização e registrar um único relatório detalhado no Daemon, mantendo o lote em reconciliação e sem chamada Telegram direta pelo n8n (FR-016; plan: Hermes failure reports).
- [X] T110 Fazer o polling Hermes enviar os relatórios ao chat autorizado via Long Polling e confirmar entrega apenas após o Telegram aceitar a mensagem; incluir regressões para título, erro, disponibilidade no Drive e ausência de ação Aprovar/Rejeitar (FR-016; SC-008; Constitution II/IV).
- [X] T111 Documentar o aviso de falha, os limites de retry e os passos de reconciliação em quickstart.md e manter as instruções operacionais do Hermes sincronizadas (FR-016).

## Phase 26: Convergence

- [X] T112 Reconciliar as versões draft/publicada do workflow n8n com o export local de 56 nós, preservando as configurações e credenciais existentes antes de ativar o Fluxo B (per FR-004/010/016; plan: workflow modification; partial).
- [X] T113 Executar teste integrado controlado da falha NotebookLM após T112; antes, mover a autenticação do Fluxo B para credenciais gerenciadas pelo n8n, sem liberar acesso global a variáveis de ambiente; confirmar no Telegram autorizado os títulos, erros e disponibilidade real no Drive, lote retido, ausência de retry e confirmação somente após entrega (per FR-016; SC-008; US3/AC6; partial).
