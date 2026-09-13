# Contribuindo com o TextureFlow

TextureFlow ainda e uma alfa experimental. Antes de propor uma mudanca, teste com uma copia limpa
do jogo, somente em modo single-player/offline, e preserve os arquivos originais.

## Ambiente

- Python 3.11 ou mais recente para o manager e os testes;
- LLVM-MinGW com alvo i686 para compilar o hook DirectX 8;
- MinHook 1.3.4;
- Wibo para executar o harness Windows no Linux (opcional).

Os scripts aceitam `TEXTUREFLOW_TOOLCHAIN_ROOT`, `TEXTUREFLOW_MINHOOK_ROOT` e
`TEXTUREFLOW_WIBO` para localizar essas dependencias fora das pastas padrao.

## Antes do pull request

1. Execute `PYTHONPATH=src python -m unittest discover -s tests -v`.
2. Compile com `./scripts/build_d3d8_hook.sh`.
3. Quando Wibo estiver disponivel, execute `./scripts/smoke_d3d8_hook.sh`.
4. Descreva o jogo, a API DirectX, a GPU, o formato da textura e como reproduzir o problema.
5. Nao envie dumps, caches, arquivos proprietarios de jogos ou modelos sem licenca compativel.

Ao contribuir, voce concorda que sua contribuicao sera distribuida sob a licenca MIT do projeto.
