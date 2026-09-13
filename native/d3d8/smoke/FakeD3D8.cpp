#include <windows.h>
#include <d3d8.h>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <vector>

namespace
{
    struct FakeTexture
    {
        void **vtable = nullptr;
        UINT width = 0;
        UINT height = 0;
        UINT levels = 0;
        D3DFORMAT format = D3DFMT_UNKNOWN;
        std::vector<UINT> pitches;
        std::vector<std::vector<uint8_t>> pixels;
    };

    struct FakeObject
    {
        void **vtable = nullptr;
    };

    void *g_d3d8_vtable[16] = {};
    void *g_device_vtable[100] = {};
    void *g_texture_vtable[19] = {};
    FakeObject g_d3d8;
    FakeObject g_device;
    IDirect3DBaseTexture8 *g_last_texture = nullptr;
    DWORD g_texture_stage_states[8][32] = {};
    volatile LONG g_call_noise = 0;

    ULONG STDMETHODCALLTYPE fake_add_ref(void *)
    {
        return static_cast<ULONG>(InterlockedIncrement(&g_call_noise));
    }

    ULONG STDMETHODCALLTYPE fake_release(void *)
    {
        return 1;
    }

    D3DRESOURCETYPE STDMETHODCALLTYPE fake_get_type(IDirect3DTexture8 *)
    {
        return D3DRTYPE_TEXTURE;
    }

    HRESULT STDMETHODCALLTYPE fake_get_device_caps(IDirect3DDevice8 *, D3DCAPS8 *caps)
    {
        if (caps == nullptr)
            return D3DERR_INVALIDCALL;
        std::memset(caps, 0, sizeof(*caps));
        caps->TextureFilterCaps = D3DPTFILTERCAPS_MINFLINEAR |
                                  D3DPTFILTERCAPS_MAGFLINEAR |
                                  D3DPTFILTERCAPS_MIPFLINEAR |
                                  D3DPTFILTERCAPS_MINFANISOTROPIC;
        caps->MaxAnisotropy = 16;
        return D3D_OK;
    }

    HRESULT STDMETHODCALLTYPE fake_get_texture_stage_state(IDirect3DDevice8 *, DWORD stage,
                                                            D3DTEXTURESTAGESTATETYPE state,
                                                            DWORD *value)
    {
        if (stage >= 8 || static_cast<DWORD>(state) >= 32 || value == nullptr)
            return D3DERR_INVALIDCALL;
        *value = g_texture_stage_states[stage][static_cast<DWORD>(state)];
        return D3D_OK;
    }

    HRESULT STDMETHODCALLTYPE fake_set_texture_stage_state(IDirect3DDevice8 *, DWORD stage,
                                                            D3DTEXTURESTAGESTATETYPE state,
                                                            DWORD value)
    {
        if (stage >= 8 || static_cast<DWORD>(state) >= 32)
            return D3DERR_INVALIDCALL;
        g_texture_stage_states[stage][static_cast<DWORD>(state)] = value;
        return D3D_OK;
    }

    HRESULT STDMETHODCALLTYPE fake_get_level_desc(IDirect3DTexture8 *self, UINT level,
                                                   D3DSURFACE_DESC *description)
    {
        if (self == nullptr || description == nullptr)
            return D3DERR_INVALIDCALL;
        auto *texture = reinterpret_cast<FakeTexture *>(self);
        if (level >= texture->levels)
            return D3DERR_INVALIDCALL;
        const UINT width = (std::max)(1u, texture->width >> level);
        const UINT height = (std::max)(1u, texture->height >> level);
        std::memset(description, 0, sizeof(*description));
        description->Format = texture->format;
        description->Type = D3DRTYPE_SURFACE;
        description->Usage = 0;
        description->Pool = D3DPOOL_MANAGED;
        description->Size = static_cast<UINT>(texture->pixels[level].size());
        description->MultiSampleType = D3DMULTISAMPLE_NONE;
        description->Width = width;
        description->Height = height;
        return D3D_OK;
    }

    HRESULT STDMETHODCALLTYPE fake_lock_rect(IDirect3DTexture8 *self, UINT level,
                                              D3DLOCKED_RECT *locked, const RECT *, DWORD)
    {
        if (self == nullptr || locked == nullptr)
            return D3DERR_INVALIDCALL;
        auto *texture = reinterpret_cast<FakeTexture *>(self);
        if (level >= texture->levels)
            return D3DERR_INVALIDCALL;
        locked->Pitch = static_cast<INT>(texture->pitches[level]);
        locked->pBits = texture->pixels[level].data();
        return D3D_OK;
    }

    HRESULT STDMETHODCALLTYPE fake_unlock_rect(IDirect3DTexture8 *, UINT level)
    {
        return D3D_OK;
    }

    HRESULT STDMETHODCALLTYPE fake_create_texture(IDirect3DDevice8 *, UINT width, UINT height,
                                                   UINT levels, DWORD, D3DFORMAT format, D3DPOOL,
                                                   IDirect3DTexture8 **returned_texture)
    {
        InterlockedIncrement(&g_call_noise);
        if (returned_texture == nullptr || width == 0 || height == 0 || levels == 0 ||
            (format != D3DFMT_A8R8G8B8 && format != D3DFMT_DXT1 &&
             format != D3DFMT_DXT3 && format != D3DFMT_DXT5))
            return D3DERR_INVALIDCALL;

        auto *texture = new FakeTexture();
        texture->vtable = g_texture_vtable;
        texture->width = width;
        texture->height = height;
        texture->levels = levels;
        texture->format = format;
        texture->pitches.resize(levels);
        texture->pixels.resize(levels);
        for (UINT level = 0; level < levels; ++level)
        {
            const UINT level_width = (std::max)(1u, width >> level);
            const UINT level_height = (std::max)(1u, height >> level);
            const UINT block_bytes = format == D3DFMT_DXT1 ? 8u :
                                     (format == D3DFMT_DXT3 || format == D3DFMT_DXT5 ? 16u : 0u);
            const UINT tight_row = block_bytes != 0
                ? ((level_width + 3) / 4) * block_bytes : level_width * 4;
            const UINT rows = block_bytes != 0 ? (std::max)(1u, (level_height + 3) / 4) : level_height;
            // Deliberate padding proves the hook ignores driver-selected row padding when hashing
            // and copies only the tight rows when uploading replacements.
            texture->pitches[level] = tight_row + 16;
            texture->pixels[level].resize(static_cast<size_t>(texture->pitches[level]) * rows);
        }
        *returned_texture = reinterpret_cast<IDirect3DTexture8 *>(texture);
        return D3D_OK;
    }

    HRESULT STDMETHODCALLTYPE fake_set_texture(IDirect3DDevice8 *, DWORD,
                                                IDirect3DBaseTexture8 *texture)
    {
        InterlockedIncrement(&g_call_noise);
        g_last_texture = texture;
        return D3D_OK;
    }

    HRESULT STDMETHODCALLTYPE fake_create_device(IDirect3D8 *, UINT, D3DDEVTYPE, HWND, DWORD,
                                                  D3DPRESENT_PARAMETERS *,
                                                  IDirect3DDevice8 **returned_device)
    {
        InterlockedIncrement(&g_call_noise);
        if (returned_device == nullptr)
            return D3DERR_INVALIDCALL;
        *returned_device = reinterpret_cast<IDirect3DDevice8 *>(&g_device);
        return D3D_OK;
    }

    void initialize_vtables()
    {
        g_d3d8.vtable = g_d3d8_vtable;
        g_device.vtable = g_device_vtable;
        g_d3d8_vtable[1] = reinterpret_cast<void *>(&fake_add_ref);
        g_d3d8_vtable[2] = reinterpret_cast<void *>(&fake_release);
        g_d3d8_vtable[15] = reinterpret_cast<void *>(&fake_create_device);
        g_device_vtable[1] = reinterpret_cast<void *>(&fake_add_ref);
        g_device_vtable[2] = reinterpret_cast<void *>(&fake_release);
        g_device_vtable[7] = reinterpret_cast<void *>(&fake_get_device_caps);
        g_device_vtable[20] = reinterpret_cast<void *>(&fake_create_texture);
        g_device_vtable[61] = reinterpret_cast<void *>(&fake_set_texture);
        g_device_vtable[62] = reinterpret_cast<void *>(&fake_get_texture_stage_state);
        g_device_vtable[63] = reinterpret_cast<void *>(&fake_set_texture_stage_state);
        g_texture_vtable[1] = reinterpret_cast<void *>(&fake_add_ref);
        g_texture_vtable[2] = reinterpret_cast<void *>(&fake_release);
        g_texture_vtable[10] = reinterpret_cast<void *>(&fake_get_type);
        g_texture_vtable[14] = reinterpret_cast<void *>(&fake_get_level_desc);
        g_texture_vtable[16] = reinterpret_cast<void *>(&fake_lock_rect);
        g_texture_vtable[17] = reinterpret_cast<void *>(&fake_unlock_rect);
        g_texture_stage_states[0][D3DTSS_MINFILTER] = D3DTEXF_POINT;
        g_texture_stage_states[0][D3DTSS_MAGFILTER] = D3DTEXF_POINT;
        g_texture_stage_states[0][D3DTSS_MIPFILTER] = D3DTEXF_NONE;
        g_texture_stage_states[0][D3DTSS_MAXANISOTROPY] = 1;
    }
}

extern "C" __declspec(dllexport) IDirect3D8 *WINAPI Direct3DCreate8(UINT)
{
    InterlockedIncrement(&g_call_noise);
    initialize_vtables();
    return reinterpret_cast<IDirect3D8 *>(&g_d3d8);
}

extern "C" __declspec(dllexport) IDirect3DBaseTexture8 *WINAPI FakeGetLastTexture()
{
    return g_last_texture;
}

extern "C" __declspec(dllexport) BOOL WINAPI FakeGetTextureInfo(IDirect3DBaseTexture8 *base,
                                                                 UINT *width, UINT *height,
                                                                 UINT *levels, DWORD *format)
{
    if (base == nullptr || width == nullptr || height == nullptr || levels == nullptr || format == nullptr)
        return FALSE;
    auto *texture = reinterpret_cast<FakeTexture *>(base);
    *width = texture->width;
    *height = texture->height;
    *levels = texture->levels;
    *format = static_cast<DWORD>(texture->format);
    return TRUE;
}

BOOL APIENTRY DllMain(HMODULE, DWORD, LPVOID)
{
    return TRUE;
}
