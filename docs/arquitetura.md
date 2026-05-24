\# Arquitetura da Plataforma CADOC 3040



\## Estrutura Geral



O projeto foi dividido em módulos especializados para separar:



\- interface gráfica

\- regras de negócio

\- integrações BACEN

\- ajustes regulatórios

\- processamento XML



\## Fluxo de Execução



1\. Usuário executa uma ação na interface.

2\. O orquestrador principal dispara a rotina correspondente.

3\. O wrapper carrega dinamicamente a função especializada.

4\. O módulo executa a regra de negócio.

5\. Logs e relatórios são gerados.



\## Organização dos Módulos



\### Fechamento\_3040\_v15.py



Responsável por:

\- GUI

\- logs

\- orquestração

\- execução de tarefas

\- carregamento dinâmico



\### fechamento\_ajustes\_\*.py



Responsáveis por:

\- validações regulatórias

\- ajustes XML

\- regras de negócio



\### fechamento\_bacen\_sta.py



Responsável por:

\- validação BACEN

\- automação STA

\- integração Selenium



\## Estratégia Técnica



\- Processamento incremental para XMLs grandes

\- Separação entre GUI e domínio

\- Logging operacional

\- Processamento em lote

\- Estrutura modular extensível

