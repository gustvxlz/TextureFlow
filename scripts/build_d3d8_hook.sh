#!/usr/bin/env bash
set -euo pipefail

app_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_root="$(dirname "$app_root")"
toolchain_root="${TEXTUREFLOW_TOOLCHAIN_ROOT:-$workspace_root/toolchains/llvm-mingw-20260908-ucrt-ubuntu-22.04-x86_64}"
minhook_root="${TEXTUREFLOW_MINHOOK_ROOT:-$workspace_root/third_party/minhook}"
build_root="$app_root/build/d3d8-x86"
output="$app_root/bin/hooks/TextureFlowD3D8-x86.asi"

cc="$toolchain_root/bin/i686-w64-mingw32-clang"
cxx="$toolchain_root/bin/i686-w64-mingw32-clang++"

if [[ ! -x "$cxx" ]]; then
    echo "llvm-mingw toolchain not found: $cxx" >&2
    exit 1
fi
if [[ ! -f "$minhook_root/include/MinHook.h" ]]; then
    echo "MinHook source not found: $minhook_root" >&2
    exit 1
fi

mkdir -p "$build_root" "$(dirname "$output")"

common=(
    -O2 -DNDEBUG -DWIN32_LEAN_AND_MEAN -DNOMINMAX -D_WIN32_WINNT=0x0601
    -ffunction-sections -fdata-sections
    -I"$minhook_root/include" -I"$minhook_root/src" -I"$minhook_root/src/hde"
)

"$cxx" "${common[@]}" -std=c++17 -Wall -Wextra -Wpedantic \
    -c "$app_root/native/d3d8/TextureFlowD3D8.cpp" -o "$build_root/TextureFlowD3D8.o"
"$cc" "${common[@]}" -std=c11 -c "$minhook_root/src/buffer.c" -o "$build_root/buffer.o"
"$cc" "${common[@]}" -std=c11 -c "$minhook_root/src/hook.c" -o "$build_root/hook.o"
"$cc" "${common[@]}" -std=c11 -c "$minhook_root/src/trampoline.c" -o "$build_root/trampoline.o"
"$cc" "${common[@]}" -std=c11 -c "$minhook_root/src/hde/hde32.c" -o "$build_root/hde32.o"

"$cxx" -shared -static -Wl,--gc-sections -Wl,--exclude-all-symbols \
    "$build_root/TextureFlowD3D8.o" "$build_root/buffer.o" "$build_root/hook.o" \
    "$build_root/trampoline.o" "$build_root/hde32.o" \
    -o "$output" -lkernel32 -luser32

"$toolchain_root/bin/llvm-strip" "$output"
echo "$output"
