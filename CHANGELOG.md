# Changelog

## 0.4.1-alpha.1

- preservacao de aparencia refeita com correspondencia completa de histograma RGB, impedindo
  escurecimento mesmo quando a media simples da textura parecia correta;
- pixels transparentes deixam de influenciar a analise de cor e permanecem intocados para evitar
  halos;
- protecao conservadora e configuravel para texto, interface, cabelo, folhagem e texturas com
  aparencia de rosto;
- remocao de `state/cache.json` ou de um replacement e detectada durante o monitoramento, que
  reavalia o dump sem exigir reinicio;
- o hook DirectX 8 volta a gravar um dump apagado ou truncado durante a mesma sessao do jogo.
- pasta `TT/ignore` permite bloquear permanentemente um hash que nao deve receber IA.

## 0.4.0-alpha.1

- modelo 4xNomos8kSC como caminho principal de alta qualidade para albedo/diffuse;
- plano de inferencia respeita a escala nativa 4x do Nomos e usa General v3 somente para um
  restante 2x, evitando enviar `-s 2` a um modelo exclusivamente 4x;
- preservacao de aparencia corrige media RGB, luminancia e pequenos desvios de contraste gerados
  pela rede neural antes da recompressao DDS;
- reforco anti-esticamento opcional eleva texturas pequenas em mais um nivel de resolucao;
- capturador DirectX 8 limitado ao estagio 0 para nao substituir lightmaps do Postal 2;
- filtragem anisotropica/trilinear temporaria nos replacements D3D8, com restauracao ao voltar
  para a textura original ou desativar a injecao;
- tecla HOME unificada nos modos DirectX 8 e DirectX 9/11;
- perfis renomeados pelo modo DirectX e interface refeita com tema escuro mais profissional;
- configuracoes 0.3 sao migradas automaticamente para o novo perfil/modelo.

## 0.3.0-alpha.1

- modo sem pop-in padrao: resultados novos ficam em `TT/pending` durante a partida e sao ativados
  atomicamente quando o jogo fecha ou antes da proxima abertura;
- seletor de resolucao minima do lado maior: 512, 1024, 2048 ou 4096 px;
- multiplos passes RealESRGAN 2x/4x quando necessario, sem intermediarios maiores que o alvo;
- alternancia original/aprimorada em tempo real no Postal 2 por F8 ou pelo manager, sem reiniciar;
- marcador persistente `TT/TextureFlow.disabled`, refletido ao vivo pela interface;
- cache LRU D3D8 com limite configuravel de 512 MB a 8 GB na interface;
- atualizacao segura do plugin 0.2 quando a assinatura confirma que ele pertence ao TextureFlow;
- diagnostico mostra fila pendente e estado atual da injecao;
- harness D3D8 ampliado para validar toggle e descarte por limite de memoria.

## 0.2.0-alpha.1

- capturador e injetor Direct3D 8 x86 nativo para Postal 2;
- dump DDS atomico por hash e hot reload de replacements com mipmaps;
- restauracao da textura original quando um replacement e apagado;
- watchdog D3D8 com contadores de dispositivo, binds, capturas e injecoes;
- instalacao separada por perfil, validacao x86 e remocao baseada em assinatura;
- botao de verificacao da instalacao e retransmissao do log D3D8 para a interface;
- limite minimo padrao reduzido de 128 para 64 px para assets de jogos antigos;
- proxy `d3d8.dll` priorizado no Postal 2 e diagnostico de loader alterado;
- reinicio do monitor reavalia dumps quando escala ou politica de seguranca mudam;
- tratamento defensivo de excecoes no hook para preservar o processo do jogo.

## 0.1.0-alpha.1

- pipeline DDS -> RealESRGAN -> DDS com cache e mipmaps;
- captura/injecao D3D9 e D3D11 via Texture Toolkit;
- perfis iniciais para RE4 2005 e diagnostico preliminar do Postal 2.
