# TextureFlow D3D8 hook

Small x86 ASI plugin used by the Postal 2 profile. It hooks native Direct3D 8, captures the top
mip of ordinary stage-0 2D textures on first bind, and loads hash-matched DDS replacements. Version
0.4.1 isolates lightmap stages, recreates deleted dumps during the same session, uses a persistent
HOME live toggle, applies anisotropic/trilinear
filtering only to replacements, and keeps an LRU replacement-memory budget. The manager stages
newly generated files until the game exits so the hook does not swap a texture while it is visible.

The content hash and tight-row rules intentionally match Texture Toolkit so the same manager and
cache layout can be used for D3D8, D3D9 and D3D11 games.

The implementation was informed by the public Direct3D 8 interface implementations in
`crosire/d3d8to9` and `K0bin/d8vk`, and uses MinHook for safe trampolines. Those projects are not
linked into this binary; see the top-level third-party notices for source links and licenses.

Build from the repository root with the pinned llvm-mingw toolchain:

```bash
./scripts/build_d3d8_hook.sh
```
