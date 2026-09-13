# Plano de teste da alfa

## DirectX 9 - Resident Evil 4 (2005)

1. Fazer backup manual do save e iniciar o jogo uma vez sem TextureFlow.
2. Instalar o perfil DirectX 9 com minimo de 1024 px, modo seguro, preservacao de aparencia,
   protecao de letras/rostos, reforco anti-esticamento e modo sem pop-in.
3. Confirmar o banner Texture Toolkit e abrir o painel com HOME.
4. Manter TextureFlow em **Processamento > Iniciar monitoramento** e carregar uma area
   pequena/repetivel.
5. Confirmar que `TT/dump` recebe DDS e que o contador do aplicativo avanca.
6. Confirmar que resultados novos entram em `TT/pending`, sem mudanca visual durante a partida.
7. Fechar o jogo, confirmar a promocao para `TT/inject` e reabri-lo no mesmo ponto.
8. Desmarcar/marcar Injection no painel HOME e confirmar troca imediata sem recarregar a fase.
9. Confirmar que o cache injeta sem novo processamento e comparar screenshots no mesmo ponto.
   Cor, exposicao e iluminacao devem permanecer iguais; detalhes de albedo devem estar mais nitidos.
10. Comparar paredes e pisos vistos de frente e em angulo para verificar melhora de texturas
    esticadas, sem halos, quadriculado novo ou mudanca de brilho.
11. Conferir menus, placas, legendas e rostos: devem permanecer originais, sem letras tortas ou
    feicoes inventadas.
12. Apagar um replacement de teste mantendo o monitor aberto e confirmar no log que ele foi
    detectado e regenerado automaticamente.
13. Mover um replacement ruim para `TT\ignore` e confirmar que ele permanece original e nao volta
    a ser gerado; no Postal 2, pressionar HOME duas vezes para limpar o replacement residente.
14. Observar VRAM, travamentos, cintilacao, UI alterada e tempo medio por textura.

## DirectX 8 - Postal 2

1. Fechar o jogo, selecionar `System\Postal2.exe` e reinstalar o perfil DirectX 8 para receber o
   hook 0.4.1.
2. Escolher minimo de 1024 px, 2048 MB de VRAM, modo seguro, preservacao de aparencia, protecao de
   letras/rostos, reforco anti-esticamento e modo sem pop-in; iniciar o monitor.
3. Confirmar `Hook ativo: Direct3DCreate8` e `Dispositivo Direct3D 8 interceptado` no log.
4. Carregar uma area pequena e confirmar aumento de chamadas `SetTexture` e DDS em `TT\dump`.
5. Esperar resultados em `TT\pending` e caminhar/reentrar na area: nada deve mudar no meio da partida.
6. Fechar o jogo mantendo o manager aberto; confirmar a promocao de pending para inject.
7. Reabrir e comparar uma superficie no mesmo ponto. A iluminacao global, os lightmaps, as cores e
   a exposicao devem permanecer iguais; o albedo deve ganhar detalhe real.
8. Comparar pisos e paredes em angulo: replacements devem usar filtragem anisotropica/trilinear,
   sem alterar os filtros das texturas originais.
9. Pressionar HOME: confirmar originais imediatos e log `DESATIVADAS`; pressionar HOME novamente e
   confirmar replacements sem reiniciar ou recarregar a fase.
10. Repetir o passo 9 usando o botao da aba **Processamento**.
11. Para testar LRU, usar temporariamente um limite baixo e confirmar no log `LRU liberado(s)` sem
    alternancia sistematica por distancia ou cintilacao.
12. Reiniciar o jogo e confirmar carregamento pelo cache sem repetir a inferencia.
13. Repetir com xPatch/ReShade somente depois do teste limpo, um mod por vez.
14. Apagar um DDS de `TT\dump` com o jogo aberto, aguardar pelo menos um segundo e revisitar a
    textura; confirmar que o hook recria o dump e o manager volta a processa-lo.

### Interpretacao do watchdog de 20 segundos

- `device=0`: plugin abriu, mas o processo nao criou um dispositivo D3D8;
- `device=1, SetTexture=0`: dispositivo apareceu, mas os binds nao passaram pelo hook;
- `capturadas=0` com binds: formatos nao suportados, texturas pequenas/dinamicas ou LockRect negado;
- `capturadas>0, carregamentos=0`: na primeira partida isso e esperado no modo sem pop-in; verifique
  `TT\pending`, feche o jogo e teste novamente na abertura seguinte.
- `injecao=0`: pressione HOME ou o botao do manager para reativar.
- uso residente perto do limite: o LRU esta funcionando; linhas de liberacao confirmam a expulsao.

## Informacoes uteis ao relatar erro

- edicao exata do jogo e nome do executavel;
- se usa RE4 Tweaks, HD Project, ReShade ou wrapper grafico;
- `TextureToolkit.log` (RE4) ou `TextureFlowD3D8.log` (Postal 2) e `state/cache.json`;
- screenshot da textura errada e hash DDS correspondente;
- driver da GPU, resolucao minima e limite de VRAM escolhidos.
