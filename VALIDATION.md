# Validacao da build 0.4.1-alpha.1

Executada em 2026-09-13 antes do empacotamento:

- compilacao release do plugin `TextureFlowD3D8-x86.asi` com LLVM/MinGW, sem warnings;
- analise estatica Clang sem diagnosticos;
- confirmacao PE32/i386 e ausencia dos exports exclusivos do harness;
- 35 testes Python de DDS, classificacao, correspondencia de histograma, transparencia, protecao
  de texturas sensiveis, resolucao minima, reforco anti-esticamento, recuperacao de cache,
  de hash manual, plano de modelos, pending, toggle, instalacao e remocao;
- validacao dos pesos NCNN 4xNomos8kSC e confirmacao de entrada `data`, saida `output` e escala 4x;
- harness D3D8 nativo com pitch preenchido em BGRA e DXT1;
- isolamento do estagio 0: o harness confirma que um bind no estagio 1 nao e capturado nem substituido;
- ativacao anisotropica/trilinear em replacement e restauracao dos filtros originais ao desativar;
- dump por hash ignorando padding do driver;
- regeneracao de dump apagado/truncado durante a mesma sessao;
- carregamento de replacement DXT1 128x128 com oito mipmaps;
- desativacao e reativacao das texturas em runtime sem reiniciar o dispositivo;
- liberacao do cache residente ao desativar e descarte LRU ao reduzir o limite de memoria;
- verificacao dos hashes registrados em `SHA256SUMS.txt` e teste de integridade do ZIP final.

O harness usa objetos D3D8 simulados para testar deterministicamente o nucleo do hook. Ele nao e
uma afirmacao de compatibilidade final com toda edicao/mod do Postal 2. A ausencia de pop-in depende
de manter o modo sem pop-in ativo e promover `TT/pending` apenas fora da partida. O teste dentro de
uma copia real do jogo precisa ser feito no Windows; `TEST_PLAN.md` lista os passos e os sinais que
devem ser coletados caso algo falhe.
