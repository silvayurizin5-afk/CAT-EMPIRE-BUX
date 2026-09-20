# Ranking e banner de entregas

O perfil e o ranking convertem o total acumulado em reais em Robux:
`floor(total gasto / preço por Robux)`. A conversão acontece depois da soma,
sem arredondar cada pedido. Todos os tipos de produto entram no total.
Somente pedidos pagos, processando ou entregues do servidor são considerados.

A cotação usada é a `padrao` ativa do servidor. Na ausência dela, usa-se a
primeira cotação ativa por `sort_order` e ID. Sem cotações ativas, aplica-se o
padrão da loja de R$ 0,029 por Robux (R$ 2,90 por 100 Robux).
Alterar a cotação muda a equivalência do histórico na próxima leitura do ranking.

Ajustes administrativos em reais continuam sendo aplicados antes da conversão.
Não há ajuste independente de Robux; valores antigos na coluna de ajuste de
Robux são ignorados. Zerar/restaurar a economia preserva o histórico de pedidos.

As mensagens de entrega e sua prévia anexam sempre `app/assets/delivery_banner.gif`
como `nextbuy-entrega.gif`. O GIF existente no repositório é reutilizado sem
conversão para PNG. Configurações antigas que desativavam o banner ou escolhiam
imagem estática/URL não substituem mais a animação. Textos, emojis e imagens dos
produtos continuam configuráveis.

Não é necessária migração de banco. As mudanças entram em funcionamento após
integrar a branch e atualizar/reiniciar o bot; mensagens já publicadas permanecem
como estavam.
