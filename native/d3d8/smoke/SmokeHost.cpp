#include <windows.h>
#include <d3d8.h>

#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <vector>

namespace
{
    using Create8Fn = IDirect3D8 *(WINAPI *)(UINT);
    using GetLastTextureFn = IDirect3DBaseTexture8 *(WINAPI *)();
    using GetTextureInfoFn = BOOL(WINAPI *)(IDirect3DBaseTexture8 *, UINT *, UINT *, UINT *, DWORD *);
    using SmokeSelectFn = IDirect3DBaseTexture8 *(WINAPI *)(IDirect3DDevice8 *, DWORD,
                                                            IDirect3DBaseTexture8 *);
    using SmokeSetEnabledFn = void(WINAPI *)(BOOL);
    using SmokeSetBudgetBytesFn = void(WINAPI *)(ULONGLONG);
    using SmokeResidentCountFn = UINT(WINAPI *)();
    using SmokeCaptureCountFn = ULONGLONG(WINAPI *)();

#pragma pack(push, 1)
    struct DdsPixelFormat
    {
        uint32_t size, flags, fourcc, rgb_bits, r_mask, g_mask, b_mask, a_mask;
    };

    struct DdsHeader
    {
        uint32_t size, flags, height, width, pitch_or_linear_size, depth, mip_count;
        uint32_t reserved[11];
        DdsPixelFormat pixel_format;
        uint32_t caps, caps2, caps3, caps4, reserved2;
    };
#pragma pack(pop)

    constexpr uint32_t fourcc(char a, char b, char c, char d)
    {
        return static_cast<uint32_t>(static_cast<uint8_t>(a)) |
               (static_cast<uint32_t>(static_cast<uint8_t>(b)) << 8) |
               (static_cast<uint32_t>(static_cast<uint8_t>(c)) << 16) |
               (static_cast<uint32_t>(static_cast<uint8_t>(d)) << 24);
    }

    uint64_t rotate_left(uint64_t value, int amount)
    {
        return (value << amount) | (value >> (64 - amount));
    }

    uint64_t hash_pixels(const uint8_t *data, size_t size)
    {
        constexpr uint64_t prime1 = 0x9E3779B185EBCA87ull;
        constexpr uint64_t prime2 = 0xC2B2AE3D27D4EB4Full;
        uint64_t hash = 0x27D4EB2F165667C5ull;
        const size_t original_size = size;
        while (size >= 8)
        {
            uint64_t word = 0;
            std::memcpy(&word, data, sizeof(word));
            hash ^= word * prime1;
            hash = rotate_left(hash, 31) * prime2;
            data += 8;
            size -= 8;
        }
        if (size != 0)
        {
            uint64_t word = 0;
            std::memcpy(&word, data, size);
            hash ^= word * prime1;
            hash = rotate_left(hash, 31) * prime2;
        }
        hash ^= original_size;
        hash ^= hash >> 33;
        hash *= 0xFF51AFD7ED558CCDull;
        hash ^= hash >> 33;
        hash *= 0xC4CEB9FE1A85EC53ull;
        hash ^= hash >> 33;
        return hash;
    }

    bool copy_file(const char *source, const char *destination)
    {
        FILE *input = std::fopen(source, "rb");
        if (input == nullptr)
            return false;
        FILE *output = std::fopen(destination, "wb");
        if (output == nullptr)
        {
            std::fclose(input);
            return false;
        }
        char buffer[4096];
        size_t count = 0;
        bool ok = true;
        while ((count = std::fread(buffer, 1, sizeof(buffer), input)) != 0)
        {
            if (std::fwrite(buffer, 1, count, output) != count)
            {
                ok = false;
                break;
            }
        }
        std::fclose(output);
        std::fclose(input);
        return ok;
    }

    bool file_exists(const char *path)
    {
        FILE *file = std::fopen(path, "rb");
        if (file == nullptr)
            return false;
        std::fclose(file);
        return true;
    }

    bool remove_if_present(const char *path)
    {
        errno = 0;
        return std::remove(path) == 0 || errno == ENOENT;
    }

    // Wibo's DeleteFileA emulation keeps an already opened path visible through
    // GetFileAttributesA until process exit. Truncating exercises the same
    // "replacement unavailable" branch without depending on that emulation quirk.
    bool invalidate_file(const char *path)
    {
        FILE *file = std::fopen(path, "wb");
        if (file == nullptr)
            return false;
        return std::fclose(file) == 0;
    }

    bool write_dxt1_replacement(const char *path, uint32_t width, uint32_t height,
                                uint32_t mip_count)
    {
        FILE *file = std::fopen(path, "wb");
        if (file == nullptr)
            return false;
        const uint32_t magic = fourcc('D', 'D', 'S', ' ');
        DdsHeader header = {};
        header.size = sizeof(header);
        header.flags = 0x1u | 0x2u | 0x4u | 0x1000u | 0x80000u | 0x20000u;
        header.width = width;
        header.height = height;
        header.pitch_or_linear_size = ((width + 3) / 4) * ((height + 3) / 4) * 8;
        header.mip_count = mip_count;
        header.pixel_format.size = sizeof(DdsPixelFormat);
        header.pixel_format.flags = 0x4u;
        header.pixel_format.fourcc = fourcc('D', 'X', 'T', '1');
        header.caps = 0x1000u | 0x8u | 0x400000u;
        bool ok = std::fwrite(&magic, 1, sizeof(magic), file) == sizeof(magic) &&
                  std::fwrite(&header, 1, sizeof(header), file) == sizeof(header);
        for (uint32_t level = 0; ok && level < mip_count; ++level)
        {
            const uint32_t level_width = (std::max)(1u, width >> level);
            const uint32_t level_height = (std::max)(1u, height >> level);
            const uint32_t size = ((level_width + 3) / 4) *
                                  (std::max)(1u, (level_height + 3) / 4) * 8;
            std::vector<uint8_t> payload(size);
            for (uint32_t index = 0; index < size; ++index)
                payload[index] = static_cast<uint8_t>((index * 13 + level * 29) & 0xFF);
            ok = std::fwrite(payload.data(), 1, payload.size(), file) == payload.size();
        }
        std::fclose(file);
        return ok;
    }

    int fail(const char *message, int code)
    {
        std::fprintf(stderr, "FAIL %d: %s (winerr=%lu)\n", code, message,
                     static_cast<unsigned long>(GetLastError()));
        return code;
    }
}

int main()
{
    HMODULE d3d8_module = LoadLibraryA("d3d8.dll");
    if (d3d8_module == nullptr)
        return fail("fake d3d8.dll did not load", 10);
    HMODULE hook_module = LoadLibraryA("TextureFlow.D3D8.asi");
    if (hook_module == nullptr)
        return fail("TextureFlow hook did not load", 11);

    auto create8 = reinterpret_cast<Create8Fn>(GetProcAddress(d3d8_module, "Direct3DCreate8"));
    auto get_last = reinterpret_cast<GetLastTextureFn>(GetProcAddress(d3d8_module, "FakeGetLastTexture"));
    auto get_info = reinterpret_cast<GetTextureInfoFn>(GetProcAddress(d3d8_module, "FakeGetTextureInfo"));
    auto smoke_select = reinterpret_cast<SmokeSelectFn>(GetProcAddress(hook_module, "TextureFlowSmokeSelect"));
    auto set_enabled = reinterpret_cast<SmokeSetEnabledFn>(
        GetProcAddress(hook_module, "TextureFlowSmokeSetEnabled"));
    auto set_budget = reinterpret_cast<SmokeSetBudgetBytesFn>(
        GetProcAddress(hook_module, "TextureFlowSmokeSetBudgetBytes"));
    auto resident_count = reinterpret_cast<SmokeResidentCountFn>(
        GetProcAddress(hook_module, "TextureFlowSmokeResidentCount"));
    auto capture_count = reinterpret_cast<SmokeCaptureCountFn>(
        GetProcAddress(hook_module, "TextureFlowSmokeCaptureCount"));
    if (create8 == nullptr || get_last == nullptr || get_info == nullptr || smoke_select == nullptr ||
        set_enabled == nullptr || set_budget == nullptr || resident_count == nullptr ||
        capture_count == nullptr)
        return fail("fake exports unavailable", 12);

    // Keep capture/recovery independent from replacement files left by a prior Wibo run. The
    // injection path is enabled explicitly after the regenerated dump has been verified.
    set_enabled(FALSE);

    IDirect3D8 *d3d8 = create8(D3D_SDK_VERSION);
    if (d3d8 == nullptr)
        return fail("Direct3DCreate8 returned null", 13);
    D3DPRESENT_PARAMETERS parameters = {};
    parameters.Windowed = TRUE;
    parameters.SwapEffect = D3DSWAPEFFECT_DISCARD;
    IDirect3DDevice8 *device = nullptr;
    if (FAILED(d3d8->CreateDevice(0, D3DDEVTYPE_HAL, nullptr, 0, &parameters, &device)) || device == nullptr)
        return fail("CreateDevice failed", 14);

    IDirect3DTexture8 *texture = nullptr;
    if (FAILED(device->CreateTexture(64, 64, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &texture)) ||
        texture == nullptr)
        return fail("CreateTexture failed", 15);
    D3DLOCKED_RECT locked = {};
    if (FAILED(texture->LockRect(0, &locked, nullptr, 0)))
        return fail("LockRect failed", 16);
    std::vector<uint8_t> expected_pixels(64 * 64 * 4);
    for (UINT y = 0; y < 64; ++y)
    {
        auto *row = static_cast<unsigned char *>(locked.pBits) + y * locked.Pitch;
        for (UINT x = 0; x < 64; ++x)
        {
            row[x * 4 + 0] = static_cast<unsigned char>((x * 3 + y) & 0xFF);
            row[x * 4 + 1] = static_cast<unsigned char>((x + y * 5) & 0xFF);
            row[x * 4 + 2] = static_cast<unsigned char>((x * 7 + y * 2) & 0xFF);
            row[x * 4 + 3] = 255;
            std::memcpy(expected_pixels.data() + (y * 64 + x) * 4, row + x * 4, 4);
        }
    }
    texture->UnlockRect(0);

    const uint64_t expected_hash = hash_pixels(expected_pixels.data(), expected_pixels.size());
    char dump_path[MAX_PATH] = {};
    char inject_path[MAX_PATH] = {};
    std::snprintf(dump_path, MAX_PATH, "TT\\dump\\%016llX.dds",
                  static_cast<unsigned long long>(expected_hash));
    std::snprintf(inject_path, MAX_PATH, "TT\\inject\\%016llX.dds",
                  static_cast<unsigned long long>(expected_hash));

    // Make the harness repeatable even when it is launched directly rather than through the
    // shell wrapper. The stage-isolation assertion below must only observe files from this run.
    if (!remove_if_present(dump_path) || !remove_if_present(inject_path))
        return fail("could not clear stale BGRA smoke files", 45);

    if (smoke_select(device, 1, texture) != texture)
        return fail("stage 1 lightmap was substituted", 17);
    if (file_exists(dump_path))
        return fail("stage 1 lightmap was captured", 18);

    if (FAILED(device->SetTexture(0, smoke_select(device, 0, texture))))
        return fail("first SetTexture failed", 17);
    if (!file_exists(dump_path))
        return fail("hook did not create a DDS dump", 18);

    // Disable replacement selection while validating dump recovery so a stale injector file from
    // an interrupted earlier harness run cannot mask the capture assertion under Wibo.
    set_enabled(FALSE);
    if (!invalidate_file(dump_path))
        return fail("could not invalidate dump cache", 43);
    const ULONGLONG captures_before_recovery = capture_count();
    Sleep(1200);
    if (smoke_select(device, 0, texture) != texture)
        return fail("deleted dump was not regenerated during the same session", 44);
    if (capture_count() != captures_before_recovery + 1)
        return fail("deleted dump was not regenerated during the same session", 44);
    set_enabled(TRUE);

    set_enabled(TRUE);
    if (!copy_file(dump_path, inject_path))
        return fail("could not stage replacement", 20);

    Sleep(1200);
    if (FAILED(device->SetTexture(0, smoke_select(device, 0, texture))))
        return fail("replacement SetTexture failed", 21);
    if (get_last() == texture)
        return fail("hook did not substitute replacement texture", 22);
    DWORD filter_value = 0;
    if (FAILED(device->GetTextureStageState(0, D3DTSS_MINFILTER, &filter_value)) ||
        filter_value != D3DTEXF_ANISOTROPIC)
        return fail("anisotropic minification was not enabled", 40);
    if (FAILED(device->GetTextureStageState(0, D3DTSS_MIPFILTER, &filter_value)) ||
        filter_value != D3DTEXF_LINEAR)
        return fail("trilinear mip filtering was not enabled", 41);

    if (!invalidate_file(inject_path))
        return fail("could not invalidate replacement", 23);
    Sleep(1200);
    if (FAILED(device->SetTexture(0, smoke_select(device, 0, texture))))
        return fail("restore SetTexture failed", 24);
    if (get_last() != texture)
        return fail("hook did not restore original texture", 25);

    IDirect3DTexture8 *dxt_texture = nullptr;
    if (FAILED(device->CreateTexture(64, 64, 1, 0, D3DFMT_DXT1, D3DPOOL_MANAGED, &dxt_texture)) ||
        dxt_texture == nullptr)
        return fail("DXT1 CreateTexture failed", 26);
    if (FAILED(dxt_texture->LockRect(0, &locked, nullptr, 0)))
        return fail("DXT1 LockRect failed", 27);
    constexpr UINT dxt_row = 16 * 8;
    constexpr UINT dxt_rows = 16;
    std::vector<uint8_t> expected_dxt(dxt_row * dxt_rows);
    for (UINT row_index = 0; row_index < dxt_rows; ++row_index)
    {
        auto *row = static_cast<unsigned char *>(locked.pBits) + row_index * locked.Pitch;
        for (UINT byte = 0; byte < dxt_row; ++byte)
            row[byte] = static_cast<uint8_t>((row_index * 17 + byte * 3) & 0xFF);
        std::memcpy(expected_dxt.data() + row_index * dxt_row, row, dxt_row);
    }
    dxt_texture->UnlockRect(0);
    const uint64_t dxt_hash = hash_pixels(expected_dxt.data(), expected_dxt.size());
    std::snprintf(dump_path, MAX_PATH, "TT\\dump\\%016llX.dds",
                  static_cast<unsigned long long>(dxt_hash));
    std::snprintf(inject_path, MAX_PATH, "TT\\inject\\%016llX.dds",
                  static_cast<unsigned long long>(dxt_hash));
    if (!remove_if_present(dump_path) || !remove_if_present(inject_path))
        return fail("could not clear stale DXT1 smoke files", 46);
    if (FAILED(device->SetTexture(0, smoke_select(device, 0, dxt_texture))))
        return fail("DXT1 first SetTexture failed", 28);
    if (!file_exists(dump_path))
        return fail("DXT1 dump missing or hash included row padding", 29);
    if (!write_dxt1_replacement(inject_path, 128, 128, 8))
        return fail("DXT1 mip replacement creation failed", 30);

    Sleep(1200);
    IDirect3DBaseTexture8 *selected_dxt = smoke_select(device, 0, dxt_texture);
    if (FAILED(device->SetTexture(0, selected_dxt)) || selected_dxt == dxt_texture)
        return fail("DXT1 replacement was not selected", 31);
    UINT replacement_width = 0, replacement_height = 0, replacement_levels = 0;
    DWORD replacement_format = 0;
    if (!get_info(selected_dxt, &replacement_width, &replacement_height, &replacement_levels,
                  &replacement_format) || replacement_width != 128 || replacement_height != 128 ||
        replacement_levels != 8 || replacement_format != static_cast<DWORD>(D3DFMT_DXT1))
        return fail("DXT1 replacement dimensions/mips/format are wrong", 32);

    if (resident_count() != 1)
        return fail("replacement cache count is wrong", 33);

    set_enabled(FALSE);
    if (resident_count() != 0 || smoke_select(device, 0, dxt_texture) != dxt_texture)
        return fail("live disable did not restore the original", 34);
    if (FAILED(device->GetTextureStageState(0, D3DTSS_MINFILTER, &filter_value)) ||
        filter_value != D3DTEXF_POINT ||
        FAILED(device->GetTextureStageState(0, D3DTSS_MIPFILTER, &filter_value)) ||
        filter_value != D3DTEXF_NONE)
        return fail("live disable did not restore game filtering", 42);
    set_enabled(TRUE);
    selected_dxt = smoke_select(device, 0, dxt_texture);
    if (selected_dxt == dxt_texture || resident_count() != 1)
        return fail("live enable did not restore the replacement", 35);

    char bgra_dump_path[MAX_PATH] = {};
    char bgra_inject_path[MAX_PATH] = {};
    std::snprintf(bgra_dump_path, MAX_PATH, "TT\\dump\\%016llX.dds",
                  static_cast<unsigned long long>(expected_hash));
    std::snprintf(bgra_inject_path, MAX_PATH, "TT\\inject\\%016llX.dds",
                  static_cast<unsigned long long>(expected_hash));
    if (!copy_file(bgra_dump_path, bgra_inject_path))
        return fail("could not restage BGRA replacement", 36);
    Sleep(1200);
    if (smoke_select(device, 0, texture) == texture || resident_count() != 2)
        return fail("second replacement did not enter cache", 37);

    set_budget(1);
    if (resident_count() != 1)
        return fail("LRU budget did not evict one replacement", 38);
    if (smoke_select(device, 0, dxt_texture) == dxt_texture || resident_count() != 1)
        return fail("evicted replacement did not reload within budget", 39);

    std::printf("PASS: D3D8 core handled stage isolation, filtering, cache recovery, BGRA/DXT1, mips, toggle and LRU\n");
    return 0;
}
