# Componentes de terceiros

O pacote inclui binarios open source sem modificacao e uma pequena adaptacao conceitual da logica
de classificacao do texup. Os textos completos de licenca ficam em `licenses/`.

| Componente | Versao/revisao | Uso | Licenca |
|---|---|---|---|
| Texture Toolkit | v1.1.0 | dump e replacement D3D9/D3D11, x86/x64 | MIT |
| MinHook | v1.3.4 | trampolines do capturador D3D8 x86 | BSD-2-Clause |
| Ultimate ASI Loader | v9.7.4 | carrega o plugin `.asi` | zlib |
| Real-ESRGAN ncnn Vulkan | v0.2.0 | inferencia neural via Vulkan | MIT |
| 4xNomos8kSC (conversao NCNN) | snapshot tumuyan2/realsr-models 2026-09-13; modelo original de Philip Hofmann (Phhofm) | modelo principal 4x para texturas realistas | CC BY 4.0 |
| RealESRGAN General x4 v3 (conversao NCNN) | snapshot Upscayl custom-models 2026-09-12 | pesos de super-resolucao | BSD-3-Clause (modelo original), credito Xintao Wang/Upscayl |
| DirectXTex / texconv | may2026 | conversao DDS, compressao e mipmaps | MIT |
| texup | commit 89cc596d7abe0b21410df863b096bdc02b428811 | referencia para classificacao | MIT |
| Python standalone | CPython 3.12.14, build 20260901 | runtime portatil | PSF e licencas incluidas |

Projetos de origem:

- https://github.com/BadassBaboon/Texture-Toolkit
- https://github.com/TsudaKageyu/minhook
- https://github.com/crosire/d3d8to9 (referencia de interfaces D3D8; nao vinculado)
- https://github.com/K0bin/d8vk (referencia de comportamento D3D8; nao vinculado)
- https://github.com/ThirteenAG/Ultimate-ASI-Loader
- https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan
- https://github.com/Phhofm/models/tree/main/4xNomos8kSC
- https://huggingface.co/tumuyan2/realsr-models/tree/main/models-ESRGAN-Nomos8kSC
- https://github.com/upscayl/custom-models
- https://github.com/microsoft/DirectXTex
- https://github.com/veryCoolTimo/texture-auto-upscaler
- https://github.com/astral-sh/python-build-standalone
