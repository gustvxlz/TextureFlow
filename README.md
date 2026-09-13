# TextureFlow Alpha 0.4.1

TextureFlow observa as texturas DDS capturadas por um jogo, seleciona apenas candidatas seguras,
aplica super-resolucao neural na GPU e injeta o resultado pelo hash da textura. Esta build possui
dois caminhos de captura: **Direct3D 8 nativo para Postal 2** e Texture Toolkit para jogos
**Direct3D 9/11**, incluindo Resident Evil 4 de 2005.

## O que esta alfa faz

- instala o plugin correto e o Ultimate ASI Loader sem sobrescrever DLLs existentes;
- captura texturas D3D8 do Postal 2 no primeiro uso, sem converter o renderizador do jogo;
- ativa dump e injecao de DDS em `TT/dump` e `TT/inject`;
- observa texturas novas enquanto o jogo esta aberto;
- converte DDS para PNG com DirectXTex `texconv`;
- identifica e ignora normal maps, mascaras, cubemaps, volumes e formatos desconhecidos;
- melhora albedo/diffuse com o modelo 4xNomos8kSC via Vulkan e usa General x4 v3 apenas para
  completar escalas 2x quando necessario;
- permite escolher 512, 1024, 2048 ou 4096 px como resolucao minima do lado maior;
- oferece reforco anti-esticamento: texturas pequenas recebem uma etapa extra de resolucao quando
  o limite de 4096 px permite;
- iguala o histograma de cor e luminosidade ao original, inclusive em texturas com transparencia,
  evitando que o material fique mais claro ou escuro;
- protege automaticamente letras, interface e texturas com aparencia de rosto contra deformacao
  generativa;
- preserva a proporcao, a compressao compativel e gera mipmaps completos;
- por padrao grava resultados novos em `TT/pending` e so os ativa fora da partida, eliminando a
  troca perceptivel enquanto o jogador anda;
- permite alternar original/aprimorada durante a partida com **HOME** nos dois modos DirectX;
- no Postal 2, substitui somente o estagio principal e preserva lightmaps; quando uma textura
  aprimorada esta ativa, usa filtragem anisotropica/trilinear e restaura a configuracao original ao desativar;
- limita os replacements residentes do Postal 2 por um cache LRU configuravel de VRAM;
- usa cache nas proximas execucoes;
- mantem os pacotes originais do jogo intactos.

As texturas aprimoradas sao geradas localmente pelo modelo incluido. Nao e necessario baixar um
texture pack por jogo; somente o cache DDS criado no computador ocupa espaco adicional em disco.

## Uso rapido no Windows

1. Extraia o ZIP para uma pasta simples, por exemplo `C:\TextureFlow-Alpha`.
2. Abra `Start TextureFlow Alpha.cmd`.
3. Escolha o perfil e selecione o executavel real do jogo (o perfil costuma ser detectado pelo nome).
4. Escolha a resolucao minima. Comece em **1024 px** e mantenha preservacao de aparencia,
   protecao de letras/rostos, reforco anti-esticamento e **Modo sem pop-in** marcados.
5. Escolha o limite de VRAM e clique em **Instalar captura no jogo**.
6. Na aba **Processamento**, clique em **Iniciar monitoramento** e depois abra o jogo.
7. Jogue normalmente. As novas versoes sao preparadas em `TT\pending`, mas nao substituem nada que
   esteja visivel nessa partida.
8. Feche o jogo mantendo o manager aberto. Ele move os resultados para `TT\inject`; na proxima
   abertura, cada replacement entra ja no primeiro bind, sem mostrar antes a versao original.

Se a janela nao abrir, use `Start TextureFlow Alpha (console).cmd` para ver a mensagem de erro.

Use **HOME** no Postal 2 para alternar imediatamente todas as texturas aprimoradas, ou use o botao na
aba de processamento. Isso libera o cache de replacements, restaura a filtragem do jogo e volta aos
originais sem fechar nem recarregar a fase. Em DirectX 9/11, HOME abre o painel do Texture Toolkit;
alterne **Injection**. Se o
jogo ja tiver um `dinput8.dll`, `d3d8.dll` ou `d3d9.dll`, o instalador preserva o arquivo e tenta o
proximo nome seguro. Se todos estiverem ocupados, ele cancela sem sobrescrever mods ou wrappers.

Ao atualizar a partir da 0.2 ou 0.3, feche o jogo e clique novamente em
**Instalar captura no jogo**.
O instalador atualiza apenas um ASI cuja assinatura ainda seja a da versao anterior do TextureFlow.

Se voce ja gerou replacements com uma versao anterior e viu a cena mudar de brilho, renomeie
temporariamente `TT\inject` para `TT\inject_backup_antigo` antes do primeiro teste da 0.4.1.
Isso preserva o cache antigo para recuperacao e garante que nenhum DDS anterior apareca enquanto a
nova correcao e processada.

## Perfis

### DirectX 9 - Resident Evil 4 (2005)

Selecione `bio4.exe` ou `game.exe`, conforme sua edicao. Comece com minimo de 1024 px e modo seguro.
A injecao acontece sem modificar os arquivos `.lfs`, `.udas` ou outros pacotes. O limite LRU da
interface e especifico do hook nativo D3D8; no RE4, acompanhe a VRAM pelo jogo/driver.

### DirectX 8 - Postal 2

1. Selecione o `Postal2.exe` dentro da pasta `System` da instalacao, nao um atalho ou launcher.
2. Use o modo `DirectX 8 - Postal 2`, minimo de 1024 px e modo seguro.
3. Clique em **Instalar captura no jogo** e depois em **Iniciar**.
4. Abra o jogo usando o renderizador Direct3D. Jogue em uma area pequena por alguns minutos.
5. Procure linhas `Textura capturada` no aplicativo ou em `TextureFlowD3D8.log`.

O plugin x86 intercepta `Direct3DCreate8`, a criacao do dispositivo e os binds do estagio 0. Estagios
posteriores, normalmente usados por lightmaps, passam intocados. No primeiro bind principal ele le o
mip principal, remove padding do driver, calcula um hash estavel e grava um
DDS em `TT\dump`. No modo padrao, o manager termina o upscale em `TT\pending`; depois que o jogo
fecha, promove o DDS para `TT\inject`. O plugin carrega todos os mipmaps no primeiro uso da sessao.
Formatos atualmente capturados: DXT1, DXT2/3, DXT4/5 e RGB(A) de 32 bits compativel.

O hook nao tenta voltar para a textura original apenas porque um objeto ficou longe. Mipmaps ja
reduzem o custo de amostragem a distancia, mas toda a alocacao ainda conta para memoria. Para
economizar sem criar pop-in, o cache mantem as texturas usadas recentemente e libera as mais antigas
ao ultrapassar `VramBudgetMB`; se voltarem a ser necessarias, elas sao recarregadas do cache em disco.

Este e um caminho novo de alfa. O binario foi compilado e teve instalacao, formato PE e pipeline
validados automaticamente, mas a validacao final dentro de uma copia real do Postal 2 depende do
teste no Windows. Se houver falha, envie o log: ele diferencia plugin nao carregado, dispositivo
D3D8 ausente, zero chamadas `SetTexture`, formato ignorado e falha de leitura.

## Seguranca e limites

- Teste apenas em jogos single-player/offline. Nao injete DLLs em jogos com anti-cheat.
- A alfa processa uma textura por vez para reduzir picos de VRAM.
- Postal 2 e 32 bits; o instalador rejeita um executavel x64 em vez de instalar o plugin errado.
- A protecao de rosto/texto e heuristica porque dumps por hash nao carregam o nome semantico
  original. Se ela falhar, mova o DDS ruim de `TT\inject` para `TT\ignore`; aquele hash permanecera
  original e nao sera regenerado. No Postal 2, pressione HOME duas vezes depois de mover para
  liberar o replacement que ja estava na VRAM.
- Minimo de 1024 px e limite de 2048 MB sao os valores iniciais conservadores. O reforco
  anti-esticamento pode elevar uma textura pequena ate o proximo nivel; 2048/4096 px podem
  aumentar muito o tempo, o cache em disco e a memoria usada.
- Desmarcar o modo sem pop-in permite hot reload imediato, mas a troca pode voltar a ficar visivel.
- Normal maps e material masks continuam deliberadamente ignorados no modo seguro.
- Feche o jogo antes de remover arquivos do hook.
- O botao **Remover hook** apaga somente arquivos cuja assinatura ainda corresponde ao que o
  TextureFlow instalou. Dumps, replacements e configuracoes sao preservados.

## Diagnostico

- RE4 sem painel ao pressionar HOME: verifique `TextureToolkit.log` na pasta do executavel.
- Postal 2 sem `TextureFlowD3D8.log`: o ASI Loader nao carregou o plugin.
- Log D3D8 sem `Dispositivo Direct3D 8 interceptado`: confirme o executavel e o renderizador.
- Log com dispositivo mas sem `SetTexture`: ha outro caminho grafico/wrapper interferindo.
- Imagem preta ou erro Vulkan: atualize o driver da GPU e use tile 128/256.
- Textura muda enquanto voce anda: confirme que **Modo sem pop-in** esta marcado; resultados novos
  devem aparecer em `TT\pending`, nunca em `TT\inject` durante a partida.
- Cache ou replacement apagado durante o monitoramento: a 0.4.1 detecta a remocao e reavalia os
  dumps automaticamente; no DirectX 8, um dump apagado tambem e capturado novamente no mesmo jogo.
- Textura errada: pressione HOME (Postal 2) ou desative Injection (D3D9/11), remova o hash de
  `TT\inject` e mantenha o modo seguro.

## Desenvolvimento

O repositorio mantem o codigo-fonte, testes e licencas. O runtime Python, modelos e executaveis de
terceiros ficam no ZIP pronto da pagina **Releases**, evitando um clone pesado e historico Git com
binarios. O aplicativo usa apenas a biblioteca padrao do Python. Para abrir pelo codigo-fonte:

```bash
PYTHONPATH=src python -m textureflow.app
```

Para rodar os testes:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

O hook DirectX 8 requer LLVM-MinGW x86 e MinHook. Por padrao, os scripts procuram essas dependencias
nas pastas irmas `toolchains/` e `third_party/minhook`; caminhos diferentes podem ser informados com
`TEXTUREFLOW_TOOLCHAIN_ROOT`, `TEXTUREFLOW_MINHOOK_ROOT` e, no smoke test, `TEXTUREFLOW_WIBO`.

Contribuicoes sao bem-vindas. Consulte `CONTRIBUTING.md` antes de abrir um pull request.

Consulte `THIRD_PARTY_NOTICES.md` para versoes, licencas e origem das dependencias open source.
Os testes executados nesta build e o limite da validacao sem uma copia do jogo estao em
`VALIDATION.md` e `TEST_PLAN.md`.
