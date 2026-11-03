# SEFAZ NF-e – Monitoramento, Aprovação Manual e Distribuição Inteligente

Sistema automatizado e resiliente para detecção, triagem com aprovação manual no Telegram, distribuição por e-mail corporativo com anexos em PDF, armazenamento em nuvem (Google Drive) e indexação no Google NotebookLM para consultas fiscais assistidas por IA.

---

## 🏛️ Arquitetura da Solução

O fluxo opera em etapas desacopladas e tolerantes a falhas:

```
[Portal SEFAZ NF-e]
       │
       ▼ (Varredura periódica CSS: #conteudoDinamico)
[Changedetection.io]
       │
       ▼ (Webhook interno JSON: /webhook/alerta-zen)
[n8n Workflow] ──(Download PDFs & Validação)──► [Cache Local / Shared Volume]
       │
       ▼ (Enfileiramento REST: POST /api/queue/add)
[Daemon Python (notebooklm_receiver.py)]
       │
       ▼ (Long Polling & Notificação interativa)
[Hermes Agent (Telegram Gatekeeper)]
       │
   [Aprovação / Rejeição Manual via Reply no Telegram]
       │
       ├─► (Se Rejeitado) ──► Registra no Catálogo e encerra.
       │
       └─► (Se Aprovado)
             │
             ▼ (Webhook interno com Header Auth)
         [n8n Workflow (Distribuição)]
             │
             ├─► 1. Envio de E-mail SMTP (HTML com layout corporativo e PDFs anexados)
             ├─► 2. Upload para o Google Drive
             └─► 3. Indexação como Fonte no Google NotebookLM
```

---

## 📦 Componentes do Repositório

* **`changedetection/`**: Configurações e metadados dos monitores de Informes e Notas Técnicas da SEFAZ.
* **`alerta_zen_atual.json`**: Workflow de produção exportado do n8n (54 nós, utilizando Header Auth nativo).
* **`notebooklm_receiver.py`**: Daemon Python responsável pelo gerenciamento de estado, fila de pendências (`fila_pendencias.json`), catálogo de documentos históricos (`notebooklm_catalog.json`) e controle de concorrência.
* **`hermes/`**: Scripts do Gatekeeper Telegram (`nfe_gatekeeper.py`, `nfe_queue_poller.py`), prompt de sistema e instruções de operação.
* **`ops/`**: Serviço vigia (`nfe_notebooklm_watchdog.py`) e timers do systemd para renovação de sessão do Google e autocura de lotes retidos.
* **`specs/`**: Especificações funcionais completas, modelo de dados, contratos de API e guia operacional (`quickstart.md`).
* **`tests/`**: Suíte de testes automatizados (57 testes unitários e de integração).

---

## 🚀 Como Executar os Testes

Para rodar a suíte completa de testes:

```bash
python -m unittest discover -s tests
```

---

## 🔒 Segurança e Credenciais

Este repositório foi construído seguindo rigorosas práticas de segurança:
* Nenhuma senha, token de API, bot token ou cookie de sessão está commitado no repositório.
* Todas as credenciais de serviço utilizam variáveis de ambiente e autenticação nativa do n8n.
* O arquivo `.gitignore` protege dados locais sensíveis, caches temporários e arquivos de fila.
