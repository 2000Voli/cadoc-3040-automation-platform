# CADOC 3040 Automation Platform

Plataforma desktop em Python para automação, validação e ajuste de arquivos XML do CADOC 3040, com foco em fechamento regulatório, rastreabilidade operacional e redução de retrabalho manual.

## Objetivo

Automatizar rotinas operacionais relacionadas ao fechamento do CADOC 3040, incluindo validações, ajustes XML, integração com validador BACEN, automações STA e geração de relatórios de apoio.

## Funcionalidades

- Interface desktop com Tkinter
- Processamento em lote de arquivos XML
- Ajustes automatizados em XMLs regulatórios
- Validação de composição de IPOC
- Ajuste de início de relacionamento do cliente
- Inclusão de características especiais
- Filtro de operações por contrato
- Renomeação de XMLs por FIDC
- Validação de arquivos via validador BACEN
- Automação de envio/consulta no STA
- Geração de logs e relatórios Excel

## Arquitetura

O projeto utiliza uma arquitetura modular:

- `Fechamento_3040_v15.py`: orquestrador principal, interface gráfica, configurações, logs e execução de tarefas.
- `fechamento_ajustes_arrumeitor.py`: regras de correção de XML por atributo e regex.
- `fechamento_ajustes_carac_especial.py`: inclusão de característica especial em operações.
- `fechamento_ajustes_filtro_saida.py`: geração de XMLs filtrados por contrato.
- `fechamento_ajustes_ipoc.py`: validação da composição do IPOC.
- `fechamento_ajustes_relacionamento.py`: ajuste de início de relacionamento.
- `fechamento_ajustes_renomear_nc.py`: renomeação de XMLs por FIDC.
- `fechamento_bacen_sta.py`: integração com validador BACEN e automações STA.

## Destaques Técnicos

- Python
- Tkinter
- pandas
- openpyxl
- Selenium
- XML processing
- Processamento em lote
- Logging operacional
- Validação regulatória
- Automação web
- Integração Python + Java

## Impacto Operacional

A solução foi criada para apoiar o fechamento regulatório do CADOC 3040, reduzindo atividades manuais, aumentando a rastreabilidade dos ajustes e mitigando riscos de inconsistências antes do envio ao Banco Central.

## Estrutura do Projeto

```text
cadoc-3040-automation-platform/
│
├── README.md
├── requirements.txt
├── .gitignore
├── src/
│   ├── Fechamento_3040_v15.py
│   ├── fechamento_ajustes_arrumeitor.py
│   ├── fechamento_ajustes_carac_especial.py
│   ├── fechamento_ajustes_filtro_saida.py
│   ├── fechamento_ajustes_ipoc.py
│   ├── fechamento_ajustes_relacionamento.py
│   ├── fechamento_ajustes_renomear_nc.py
│   └── fechamento_bacen_sta.py
│
├── samples/
└── docs/