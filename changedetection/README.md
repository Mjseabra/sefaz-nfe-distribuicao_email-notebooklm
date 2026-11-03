# Configuração do Changedetection.io (SEFAZ NF-e)

Este diretório contém os metadados e instruções de configuração dos dois monitores oficiais utilizados no **Changedetection.io** para alimentar o pipeline de automação via webhook no n8n.

---

## 1. Monitor 1: Informes Técnicos
* **UUID do Monitor:** `9dc33d76-3d92-423b-aa3e-f01c185fa7b3`
* **Título:** `Nfe - Informes Técnicos`
* **URL Monitorada:** `https://www.nfe.fazenda.gov.br/portal/listaConteudo.aspx?tipoConteudo=hXzemuyNHW4=`
* **Filtro CSS (Include):** `#conteudoDinamico`
* **URL de Notificação (Webhook):** `json://n8n:5678/webhook/alerta-zen`
* **Formato do Payload (Notification Body):**
  ```json
  { "title": {{ watch_title|tojson }}, "url": {{ watch_url|tojson }}, "diff": {{ diff|tojson }} }
  ```

---

## 2. Monitor 2: Notas Técnicas
* **UUID do Monitor:** `d30337ed-58a5-43ab-9f98-2d882408a3d5`
* **Título:** `Nfe - Notas Técnicas`
* **URL Monitorada:** `https://www.nfe.fazenda.gov.br/portal/listaConteudo.aspx?tipoConteudo=04BIflQt1aY=`
* **Filtro CSS (Include):** `#conteudoDinamico`
* **URL de Notificação (Webhook):** `json://n8n:5678/webhook/alerta-zen`
* **Formato do Payload (Notification Body):**
  ```json
  { "title": {{ watch_title|tojson }}, "url": {{ watch_url|tojson }}, "diff": {{ diff|tojson }} }
  ```

---

## 3. Como funciona a integração
1. O Changedetection.io verifica as páginas da SEFAZ periodicamente.
2. Quando detecta alteração dentro de `#conteudoDinamico`, ele gera o texto das diferenças (`diff`).
3. O webhook interno `json://n8n:5678/webhook/alerta-zen` é acionado na rede Docker interna (`rede-automacao`).
4. O n8n recebe o alerta, faz o parsing das linhas alteradas, faz o download dos novos PDFs e enfileira no Daemon Python para aprovação manual no Telegram.
