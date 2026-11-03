# Feature Specification: Aprovação Manual de Notificações SEFAZ NFe

**Feature Branch**: `not-created`

**Created**: 2026-10-31

**Status**: Draft

**Input**: User description: "Separar o monitoramento SEFAZ NFe em coleta e distribuição, com fila de pendências, aprovação humana explícita pelo Hermes via Telegram e distribuição apenas após aprovação. Rejeições devem ser catalogadas permanentemente. Durante implementação e testes, e-mails só podem ser enviados para operador@exemplo.com.br."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Coletar documentos elegíveis (Priority: P1)

Após receber documentos do portal da SEFAZ, o sistema de coleta identifica quais são
recentes e ainda não constam no catálogo oficial nem em uma pendência existente. Os
documentos elegíveis ficam disponíveis em uma caixa de entrada para revisão, sem
acionar os destinatários corporativos.

**Why this priority**: A separação começa por uma fila confiável que impede alertas
automáticos e elimina documentos antigos ou repetidos antes da revisão.

**Independent Test**: Fornecer documentos com diferentes datas e identificadores,
incluindo itens já catalogados e pendentes; verificar que somente documentos elegíveis
aparecem uma vez na caixa de entrada e que nenhum e-mail é enviado.

**Acceptance Scenarios**:

1. **Given** um documento com data dentro dos últimos cinco dias e ausente do catálogo e
   da fila, **When** o sistema o coleta, **Then** ele aparece uma única vez como pendente
   com título, data, URL e caminho do arquivo.
2. **Given** um documento com mais de cinco dias, **When** o sistema o coleta, **Then**
   ele não é enfileirado.
3. **Given** um documento já presente no catálogo ou na fila, **When** ele é recebido
   novamente, **Then** não é criada uma segunda pendência.
4. **Given** documentos novos elegíveis, **When** são coletados, **Then** nenhum e-mail
   nem notificação corporativa é enviado antes da aprovação humana.

---

### User Story 2 - Revisar e decidir uma pendência (Priority: P1)

O usuário autorizado recebe do Hermes uma solicitação agrupada de aprovação via
Telegram, com títulos e datas dos documentos. Ele pode responder “Aprovar” ou
“Rejeitar”. A solicitação continua pendente até uma decisão válida, sem expiração
automática.

**Why this priority**: A decisão humana explícita é o controle central que evita
distribuições automáticas ou não autorizadas.

**Independent Test**: Criar um lote pendente e confirmar que o Hermes apresenta todos os
documentos agrupados; testar respostas válidas, inválidas e de remetentes não
autorizados; verificar que somente uma resposta válida do usuário autorizado altera o
estado do lote.

**Acceptance Scenarios**:

1. **Given** um lote com um ou mais documentos pendentes, **When** o Hermes solicita
   revisão, **Then** o usuário recebe uma mensagem agrupada com o título e a data de
   cada documento.
2. **Given** um lote pendente, **When** o usuário autorizado responde “Aprovar”, **Then**
   o lote passa à distribuição aprovada e somente seus documentos podem ser
   distribuídos.
3. **Given** um lote pendente, **When** o usuário autorizado responde “Rejeitar”,
   **Then** seus documentos seguem o fluxo de rejeição e não são distribuídos.
4. **Given** uma resposta diferente das opções aceitas ou enviada por remetente não
   autorizado, **When** ela é recebida, **Then** o lote permanece pendente e nenhum
   efeito de aprovação ou rejeição ocorre.
5. **Given** um lote sem decisão, **When** passa qualquer intervalo de tempo, **Then**
   ele permanece disponível para decisão sem expiração automática.

---

### User Story 3 - Distribuir documentos aprovados (Priority: P1)

Depois da aprovação humana de um lote, o sistema de distribuição envia um e-mail
consolidado, disponibiliza os arquivos no Google Drive e sincroniza os documentos no
NotebookLM, nessa ordem. A fila só é esvaziada quando todas as etapas forem concluídas.

**Why this priority**: Essa etapa entrega os documentos aos destinos corporativos sem
contornar o gate de aprovação.

**Independent Test**: Aprovar um lote de teste e verificar a ordem de conclusão do
e-mail, do Drive e do NotebookLM, o destinatário de teste exclusivo e a remoção da fila
somente após sucesso integral.

**Acceptance Scenarios**:

1. **Given** um lote aprovado, **When** a distribuição começa, **Then** as etapas são
   executadas na ordem e-mail, Google Drive e NotebookLM.
2. **Given** a fase de implementação ou testes, **When** o e-mail consolidado é enviado,
   **Then** seu único destinatário é `operador@exemplo.com.br`, sem outros destinatários em
   Para, Cc ou Cco.
3. **Given** um lote sem aprovação humana válida, **When** a distribuição é solicitada,
   **Then** nenhum e-mail, upload ou sincronização é executado.
4. **Given** uma etapa da distribuição que falha, **When** o fluxo é interrompido,
   **Then** o lote não é removido da fila nem apresentado como concluído; etapas já
   concluídas não são repetidas de modo a duplicar seus efeitos.
5. **Given** que e-mail, Drive e NotebookLM concluíram com sucesso, **When** o lote é
   finalizado, **Then** seus documentos são registrados no catálogo e a fila é
   esvaziada.
6. **Given** que os arquivos já foram arquivados no Google Drive e a importação de um ou
   mais documentos falha no NotebookLM, **When** o erro é registrado, **Then** o usuário
   autorizado recebe pelo Hermes uma mensagem com os títulos dos arquivos afetados e o
   erro informado, os arquivos continuam disponíveis no Drive e o lote fica retido sem
   nova tentativa automática.

---

### User Story 4 - Rejeitar e impedir reprocessamento (Priority: P1)

Quando o usuário rejeita um lote, o sistema registra todos os documentos no catálogo
oficial como rejeitados antes de remover o lote da caixa de entrada. Recebimentos
posteriores dos mesmos documentos não criam novas pendências.

**Why this priority**: A rejeição deve ser definitiva para esses documentos e não pode
resultar em uma distribuição futura acidental.

**Independent Test**: Rejeitar um lote, confirmar seu registro no catálogo e sua remoção
da fila e, em seguida, reenviar os mesmos documentos; verificar que nenhum deles volta
a ser enfileirado.

**Acceptance Scenarios**:

1. **Given** um lote pendente e uma rejeição válida, **When** a resolução é concluída,
   **Then** cada documento é registrado como rejeitado no catálogo e o lote é removido
   da fila.
2. **Given** uma falha ao registrar documentos rejeitados, **When** a resolução é
   tentada, **Then** a fila não é esvaziada e os documentos não são marcados como
   resolvidos.
3. **Given** documentos anteriormente rejeitados, **When** são recebidos novamente,
   **Then** permanecem fora da fila e não são distribuídos.

---

### User Story 5 - Operar dentro dos limites de infraestrutura (Priority: P2)

Como responsável pela operação, quero que as integrações funcionem dentro dos limites
de rede e arquitetura do projeto, para que o fluxo possa ser executado na VPS sem
ampliar sua exposição externa.

**Why this priority**: Os controles de implantação protegem os dados e mantêm a solução
compatível com a infraestrutura de destino.

**Independent Test**: Validar a configuração de comunicação e as imagens do conjunto de
serviços; testar timeouts e confirmar que respostas de API, inclusive erros, são JSON.

**Acceptance Scenarios**:

1. **Given** os contêineres do sistema em execução, **When** serviços se comunicam,
   **Then** a comunicação entre eles ocorre somente pela rede interna
   `rede-automacao`, sem novas portas externas abertas.
2. **Given** uma atualização de mensagens Telegram, **When** o Hermes consulta o serviço,
   **Then** usa Long Polling de saída e não depende de webhook público.
3. **Given** as imagens Docker necessárias ao sistema, **When** são validadas para o
   ambiente de destino, **Then** cada uma oferece suporte nativo a ARM64 (Ampere A1).
4. **Given** uma operação externa bloqueante ou uma falha de API, **When** o sistema a
   executa, **Then** há timeout configurado e a resposta de API é JSON estruturado,
   nunca HTML genérico.

### Edge Cases

- Documentos com data ausente ou inválida não são enfileirados; o sistema sinaliza a
  falha de validação sem tratá-los como elegíveis.
- A data exatamente no limite de cinco dias não é considerada “mais de cinco dias”;
  pode ser enfileirada se cumprir os demais critérios.
- Repetições do mesmo documento no mesmo recebimento ou em recebimentos simultâneos
  produzem no máximo uma pendência.
- Mensagem Telegram vazia, ambígua ou com texto diferente de “Aprovar” ou “Rejeitar”
  não altera a decisão; o lote continua pendente.
- Indisponibilidade do Telegram, catálogo ou de um destino de distribuição não pode
  resultar em aprovação presumida nem em remoção prematura da fila.
- Lotes diferentes aguardando decisão não podem ser aprovados ou rejeitados pela
  resposta destinada a outro lote.
- Uma falha parcial de distribuição mantém o estado necessário para retomar o lote sem
  repetir efeitos externos já concluídos.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: O sistema MUST excluir da fila documentos cuja data seja anterior a mais
  de cinco dias em relação ao momento da coleta.
- **FR-002**: O sistema MUST evitar enfileirar documentos já existentes no catálogo
  oficial, em lotes pendentes ou repetidos no mesmo recebimento.
- **FR-003**: O sistema MUST manter para cada documento pendente título, data, URL e
  caminho do arquivo em estado intermediário isolado, gerido pelo Daemon Python, sem
  gravar estado, filas ou regras de negócio no SQLite interno do n8n.
- **FR-004**: O Fluxo A do n8n MUST coletar e encaminhar documentos elegíveis, sem
  aprovar ou iniciar a distribuição corporativa.
- **FR-005**: O Hermes MUST atuar exclusivamente como Gatekeeper da aprovação e MUST
  enviar ao usuário autorizado uma solicitação agrupada com títulos e datas do lote.
- **FR-006**: A comunicação do Telegram MUST usar exclusivamente Long Polling de saída;
  a solicitação de aprovação ao usuário é uma mensagem de controle e não uma
  notificação corporativa aos destinatários finais.
- **FR-007**: O sistema MUST aceitar somente respostas “Aprovar” ou “Rejeitar” do
  usuário autorizado para decidir o lote correspondente. O Hermes MUST orquestrar essa
  decisão sem avaliar regras fiscais.
- **FR-008**: Uma pendência sem decisão MUST permanecer disponível sem prazo de
  expiração definido pelo sistema.
- **FR-009**: Ao rejeitar um lote, o sistema MUST registrar seus documentos como
  rejeitados no catálogo oficial antes de removê-los da fila; se o registro falhar, MUST
  manter o lote pendente.
- **FR-010**: O Fluxo B do n8n MUST distribuir somente lotes aprovados, na ordem:
  e-mail consolidado, upload para Google Drive e sincronização no NotebookLM.
- **FR-011**: Durante implementação e testes, todo e-mail de distribuição MUST ter
  exclusivamente `operador@exemplo.com.br` como destinatário, sem destinatários adicionais
  em Para, Cc ou Cco.
- **FR-012**: Após a distribuição completa, o sistema MUST registrar os documentos
  processados no catálogo oficial e remover o lote da fila. Uma falha em qualquer etapa
  MUST manter o lote recuperável; a retomada MUST evitar duplicar efeitos de etapas já
  concluídas.
- **FR-013**: Contêineres MUST comunicar-se somente pela rede interna Docker
  `rede-automacao`; a solução MUST NOT expor novas portas externas no firewall.
- **FR-014**: Imagens Docker utilizadas MUST oferecer suporte nativo à arquitetura
  ARM64 (Ampere A1).
- **FR-015**: Scripts e daemons MUST configurar timeouts para operações externas ou
  potencialmente bloqueantes. Respostas de API MUST ser payloads JSON estruturados,
  inclusive em falhas, nunca HTML genérico.
- **FR-016**: Quando a sincronização do NotebookLM falhar após o arquivamento no Google
  Drive, o Hermes MUST enviar ao chat autorizado uma mensagem com o lote, o título de
  cada arquivo cuja importação falhou, o erro disponível e a confirmação de que os
  arquivos permanecem no Drive. O lote MUST permanecer recuperável e a operação MUST
  aguardar reconciliação, sem repetição automática; o n8n MUST NOT enviar Telegram
  diretamente.

### Key Entities

- **Documento SEFAZ**: documento recebido para triagem, identificado de forma suficiente
  para detectar repetições; inclui título, data, URL e caminho do arquivo.
- **Lote pendente**: conjunto de documentos apresentado conjuntamente ao usuário e
  associado a um estado de decisão que não expira automaticamente.
- **Decisão de aprovação**: resposta de um usuário autorizado (“Aprovar” ou “Rejeitar”)
  vinculada a um lote específico.
- **Registro do catálogo**: entrada oficial que indica que um documento já foi
  processado ou rejeitado e, portanto, não deve voltar à fila.
- **Resultado de distribuição**: estado das etapas de e-mail, Google Drive e NotebookLM
  de um lote aprovado, usado para determinar conclusão ou retomada.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Em todos os cenários de teste, 100% dos e-mails corporativos enviados
  correspondem a um lote com aprovação humana válida anterior; nenhum e-mail é enviado
  antes dessa aprovação.
- **SC-002**: Em um conjunto de validação contendo documentos elegíveis, antigos,
  duplicados e rejeitados, 100% dos antigos, duplicados e rejeitados ficam fora da fila,
  e cada documento elegível aparece no máximo uma vez.
- **SC-003**: Em 100% dos lotes apresentados para decisão, a mensagem agrupada contém
  o título e a data de cada documento do lote.
- **SC-004**: Um lote sem resposta continua disponível após avanço simulado de 30 dias,
  sem expiração automática ou perda dos documentos.
- **SC-005**: Em todos os testes de distribuição, as etapas concluídas ocorrem na ordem
  e-mail, Drive e NotebookLM; a fila só é removida depois do sucesso integral.
- **SC-006**: Em todos os testes de e-mail durante implementação e testes, o único
  destinatário é `operador@exemplo.com.br`; o grupo completo de participantes nunca recebe
  esses e-mails.
- **SC-007**: Após uma rejeição registrada, 100% dos reenvios dos mesmos documentos são
  reconhecidos como já resolvidos e não geram uma nova pendência.
- **SC-008**: Em 100% das falhas de sincronização NotebookLM após o upload ao Drive, o
  usuário autorizado recebe um aviso com todos os títulos afetados, o erro reportado e
  a informação de que os arquivos estão no Drive; nenhuma tentativa automática seguinte
  de sincronização é iniciada.

## Assumptions

- O catálogo oficial já tem ou terá um meio de identificar unicamente os documentos e
  registrar seus estados de rejeitado ou processado; o mecanismo e sua propriedade são
  definidos no planejamento.
- O pedido de aprovação agrupa um conjunto fixo de documentos. Documentos coletados
  posteriormente não são adicionados silenciosamente a um lote que o usuário já viu.
- Respostas são reconhecidas após remover espaços externos e ignorar diferenças de
  maiúsculas/minúsculas e acentuação; qualquer outro conteúdo não decide o lote.
- Apenas remetentes Telegram previamente configurados como aprovadores podem decidir
  lotes. A lista e o processo de configuração são definidos no planejamento.
- O e-mail consolidado inclui os documentos do lote e seus dados de identificação; o
  formato exato é definido no planejamento.
- O limite exclusivo de destinatário `operador@exemplo.com.br` permanece ativo durante toda
  implementação e teste. Qualquer mudança para destinatários de produção exige uma
  autorização explícita separada antes da ativação em produção.
- O registro no catálogo após distribuição bem-sucedida é necessário para que a coleta
  posterior não volte a processar os mesmos documentos.
