#!/usr/bin/env bash
set -euo pipefail

app_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_root="$(dirname "$app_root")"
toolchain_root="${TEXTUREFLOW_TOOLCHAIN_ROOT:-$workspace_root/toolchains/llvm-mingw-20260908-ucrt-ubuntu-22.04-x86_64}"
minhook_root="${TEXTUREFLOW_MINHOOK_ROOT:-$workspace_root/third_party/minhook}"
build_root="$app_root/build/d3d8-smoke"
hook_build_root="$build_root/hook-objects"
cc="$toolchain_root/bin/i686-w64-mingw32-clang"
cxx="$toolchain_root/bin/i686-w64-mingw32-clang++"
wibo="${TEXTUREFLOW_WIBO:-$workspace_root/toolchains/wibo}"

mkdir -p "$build_root"
find "$build_root" -mindepth 1 -maxdepth 1 -type f -delete
mkdir -p "$hook_build_root"
find "$hook_build_root" -mindepth 1 -maxdepth 1 -type f -delete
if [[ -d "$build_root/TT" ]]; then
    find "$build_root/TT" -type f -delete
    find "$build_root/TT" -depth -type d -empty -delete
fi

common=(-std=c++17 -DWIN32_LEAN_AND_MEAN -DNOMINMAX -D_WIN32_WINNT=0x0601)
hook_common=(-O2 -DNDEBUG -DWIN32_LEAN_AND_MEAN -DNOMINMAX -D_WIN32_WINNT=0x0601
    -ffunction-sections -fdata-sections -I"$minhook_root/include" -I"$minhook_root/src"
    -I"$minhook_root/src/hde")
"$cxx" "${hook_common[@]}" -std=c++17 -DTEXTUREFLOW_WIBO_SMOKE \
    -c "$app_root/native/d3d8/TextureFlowD3D8.cpp" -o "$hook_build_root/TextureFlowD3D8.o"
"$cc" "${hook_common[@]}" -std=c11 -c "$minhook_root/src/buffer.c" -o "$hook_build_root/buffer.o"
"$cc" "${hook_common[@]}" -std=c11 -c "$minhook_root/src/hook.c" -o "$hook_build_root/hook.o"
"$cc" "${hook_common[@]}" -std=c11 -c "$minhook_root/src/trampoline.c" -o "$hook_build_root/trampoline.o"
"$cc" "${hook_common[@]}" -std=c11 -c "$minhook_root/src/hde/hde32.c" -o "$hook_build_root/hde32.o"
"$cxx" -shared -static -Wl,--gc-sections -Wl,--exclude-all-symbols \
    "$hook_build_root/TextureFlowD3D8.o" "$hook_build_root/buffer.o" "$hook_build_root/hook.o" \
    "$hook_build_root/trampoline.o" "$hook_build_root/hde32.o" \
    -Wl,--kill-at -o "$build_root/TextureFlow.D3D8.asi" -lkernel32 -luser32

"$cxx" "${common[@]}" -O0 -Wno-dll-attribute-on-redeclaration -shared -static -Wl,--kill-at \
    "$app_root/native/d3d8/smoke/FakeD3D8.cpp" -o "$build_root/d3d8.dll"
"$cxx" "${common[@]}" -O2 -static \
    "$app_root/native/d3d8/smoke/SmokeHost.cpp" -o "$build_root/TextureFlowD3D8Smoke.exe"

"$wibo" -C "$build_root" TextureFlowD3D8Smoke.exe
