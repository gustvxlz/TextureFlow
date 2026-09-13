#include <windows.h>
#include <d3d8.h>

#include <MinHook.h>

#include <algorithm>
#include <atomic>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#ifdef TEXTUREFLOW_WIBO_SMOKE
// Wibo intentionally implements only the small Win32 subset needed by compiler toolchains. The
// smoke test uses ASCII paths, so these adapters let the exact hook logic run there while the
// release binary continues to use Unicode Win32 APIs on Windows.
namespace textureflow_wibo
{
    bool narrow(LPCWSTR source, char *destination, size_t count)
    {
        if (source == nullptr || destination == nullptr || count == 0)
            return false;
        size_t index = 0;
        for (; source[index] != L'\0' && index + 1 < count; ++index)
        {
            if (source[index] > 0x7F)
                return false;
            destination[index] = static_cast<char>(source[index]);
        }
        destination[index] = '\0';
        return source[index] == L'\0';
    }

    DWORD module_filename(HMODULE module, LPWSTR destination, DWORD count)
    {
        char path[MAX_PATH] = {};
        const DWORD length = GetModuleFileNameA(module, path, MAX_PATH);
        if (length == 0 || length >= count)
            return 0;
        for (DWORD index = 0; index <= length; ++index)
            destination[index] = static_cast<unsigned char>(path[index]);
        return length;
    }

    UINT system_directory(LPWSTR destination, UINT count)
    {
        char path[MAX_PATH] = {};
        const UINT length = GetSystemDirectoryA(path, MAX_PATH);
        if (length == 0 || length >= count)
            return 0;
        for (UINT index = 0; index <= length; ++index)
            destination[index] = static_cast<unsigned char>(path[index]);
        return length;
    }

    HANDLE create_file(LPCWSTR path, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES security,
                       DWORD creation, DWORD flags, HANDLE template_file)
    {
        char converted[MAX_PATH * 2] = {};
        return narrow(path, converted, sizeof(converted))
            ? CreateFileA(converted, access, share, security, creation, flags, template_file)
            : INVALID_HANDLE_VALUE;
    }

    BOOL create_directory(LPCWSTR path, LPSECURITY_ATTRIBUTES security)
    {
        char converted[MAX_PATH * 2] = {};
        return narrow(path, converted, sizeof(converted)) && CreateDirectoryA(converted, security);
    }

    DWORD file_attributes(LPCWSTR path)
    {
        char converted[MAX_PATH * 2] = {};
        return narrow(path, converted, sizeof(converted))
            ? GetFileAttributesA(converted) : INVALID_FILE_ATTRIBUTES;
    }

    BOOL file_size_ex(HANDLE file, PLARGE_INTEGER size)
    {
        if (size == nullptr)
            return FALSE;
        DWORD high = 0;
        const DWORD low = GetFileSize(file, &high);
        if (low == INVALID_FILE_SIZE && GetLastError() != NO_ERROR)
            return FALSE;
        size->QuadPart = (static_cast<int64_t>(high) << 32) | low;
        return TRUE;
    }

    BOOL file_attributes_ex(LPCWSTR path, GET_FILEEX_INFO_LEVELS level, LPVOID data)
    {
        char converted[MAX_PATH * 2] = {};
        if (!narrow(path, converted, sizeof(converted)) || level != GetFileExInfoStandard || data == nullptr)
            return FALSE;
        const DWORD attributes = GetFileAttributesA(converted);
        if (attributes == INVALID_FILE_ATTRIBUTES)
            return FALSE;
        HANDLE file = CreateFileA(converted, GENERIC_READ, FILE_SHARE_READ, nullptr,
                                  OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file == INVALID_HANDLE_VALUE)
            return FALSE;
        LARGE_INTEGER size = {};
        const BOOL have_size = file_size_ex(file, &size);
        CloseHandle(file);
        if (!have_size)
            return FALSE;
        auto *result = static_cast<WIN32_FILE_ATTRIBUTE_DATA *>(data);
        std::memset(result, 0, sizeof(*result));
        result->dwFileAttributes = attributes;
        result->nFileSizeHigh = static_cast<DWORD>(static_cast<uint64_t>(size.QuadPart) >> 32);
        result->nFileSizeLow = static_cast<DWORD>(size.QuadPart);
        result->ftLastWriteTime.dwLowDateTime = 1;
        return TRUE;
    }

    BOOL delete_file(LPCWSTR path)
    {
        char converted[MAX_PATH * 2] = {};
        return narrow(path, converted, sizeof(converted)) && DeleteFileA(converted);
    }

    BOOL move_file_ex(LPCWSTR from, LPCWSTR to, DWORD)
    {
        char converted_from[MAX_PATH * 2] = {};
        char converted_to[MAX_PATH * 2] = {};
        if (!narrow(from, converted_from, sizeof(converted_from)) ||
            !narrow(to, converted_to, sizeof(converted_to)))
            return FALSE;
        HANDLE input = CreateFileA(converted_from, GENERIC_READ, FILE_SHARE_READ, nullptr,
                                   OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (input == INVALID_HANDLE_VALUE)
            return FALSE;
        HANDLE output = CreateFileA(converted_to, GENERIC_WRITE, 0, nullptr,
                                    CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (output == INVALID_HANDLE_VALUE)
        {
            CloseHandle(input);
            return FALSE;
        }
        char buffer[4096];
        bool ok = true;
        while (true)
        {
            DWORD read = 0;
            if (!ReadFile(input, buffer, sizeof(buffer), &read, nullptr))
            {
                ok = false;
                break;
            }
            if (read == 0)
                break;
            DWORD written = 0;
            if (!WriteFile(output, buffer, read, &written, nullptr) || written != read)
            {
                ok = false;
                break;
            }
        }
        CloseHandle(output);
        CloseHandle(input);
        if (ok)
            DeleteFileA(converted_from);
        return ok ? TRUE : FALSE;
    }

    HMODULE module_handle(LPCWSTR name)
    {
        char converted[MAX_PATH * 2] = {};
        return narrow(name, converted, sizeof(converted)) ? GetModuleHandleA(converted) : nullptr;
    }

    HMODULE load_library(LPCWSTR name)
    {
        char converted[MAX_PATH * 2] = {};
        return narrow(name, converted, sizeof(converted)) ? LoadLibraryA(converted) : nullptr;
    }

    void output_debug(LPCSTR)
    {
    }
}

#define GetModuleFileNameW textureflow_wibo::module_filename
#define GetSystemDirectoryW textureflow_wibo::system_directory
#define CreateFileW textureflow_wibo::create_file
#define CreateDirectoryW textureflow_wibo::create_directory
#define GetFileAttributesW textureflow_wibo::file_attributes
#define GetFileAttributesExW textureflow_wibo::file_attributes_ex
#define GetFileSizeEx textureflow_wibo::file_size_ex
#define DeleteFileW textureflow_wibo::delete_file
#define MoveFileExW textureflow_wibo::move_file_ex
#define GetModuleHandleW textureflow_wibo::module_handle
#define LoadLibraryW textureflow_wibo::load_library
#define OutputDebugStringA textureflow_wibo::output_debug
#endif

// TextureFlow's Direct3D 8 capture path.
//
// The hook deliberately has no in-game overlay. It captures only ordinary 2D textures when the
// game first binds them, writes a tightly-packed DDS to TT/dump, and loads a DDS with the same
// content-hash name from TT/inject. New neural results are normally staged by the manager until the
// next game session, avoiding a visible mid-scene swap. This file also owns the live toggle and a
// bounded LRU replacement cache; all classification/upscaling policy remains in the manager.

namespace
{
    constexpr char kVersion[] = "0.4.1-alpha.1";
    constexpr uint64_t kPrime1 = 0x9E3779B185EBCA87ull;
    constexpr uint64_t kPrime2 = 0xC2B2AE3D27D4EB4Full;
    constexpr uint32_t kMaxDimension = 8192;
    constexpr uint64_t kMaxFileBytes = 512ull * 1024 * 1024;

    std::wstring g_game_dir;
    std::wstring g_dump_dir;
    std::wstring g_inject_dir;
    std::wstring g_disable_path;
    std::wstring g_log_path;
    bool g_auto_dump = true;
    bool g_enable_injection = true;
    uint32_t g_check_interval_ms = 1000;
    uint32_t g_min_dump_dimension = 8;
    uint32_t g_toggle_hotkey = VK_HOME;
    uint64_t g_vram_budget_bytes = 2048ull * 1024 * 1024;
    uint64_t g_replacement_bytes = 0;
    uint64_t g_use_sequence = 0;
    DWORD g_last_control_check = 0;
    bool g_toggle_key_was_down = false;

    std::mutex g_log_mutex;
    std::mutex g_state_mutex;
    thread_local bool g_inside_hook = false;

    struct TextureRecord
    {
        uint64_t hash = 0;
        bool capture_attempted = false;
        bool capture_in_progress = false;
        DWORD last_dump_check = 0;
    };

    struct ReplacementRecord
    {
        IDirect3DTexture8 *texture = nullptr;
        uint64_t file_size = 0;
        FILETIME write_time = {};
        uint64_t resident_bytes = 0;
        uint64_t last_used = 0;
    };

    struct TextureFilterState
    {
        bool captured = false;
        bool enhanced = false;
        DWORD min_filter = D3DTEXF_POINT;
        DWORD mag_filter = D3DTEXF_POINT;
        DWORD mip_filter = D3DTEXF_NONE;
        DWORD max_anisotropy = 1;
    };

    std::unordered_map<IDirect3DBaseTexture8 *, TextureRecord> g_textures;
    std::unordered_map<uint64_t, ReplacementRecord> g_replacements;
    std::unordered_map<IDirect3DDevice8 *, TextureFilterState> g_filter_states;
    std::unordered_map<uint64_t, DWORD> g_last_file_check;
    std::unordered_set<uint64_t> g_logged_unsupported_formats;

    std::atomic<uint64_t> g_set_texture_calls{0};
    std::atomic<uint64_t> g_capture_count{0};
    std::atomic<uint64_t> g_injection_count{0};
    std::atomic<uint64_t> g_capture_failures{0};
    std::atomic<bool> g_device_seen{false};
    std::atomic<bool> g_runtime_enabled{true};

    using Direct3DCreate8Fn = IDirect3D8 *(WINAPI *)(UINT);
    using CreateDeviceFn = HRESULT(STDMETHODCALLTYPE *)(IDirect3D8 *, UINT, D3DDEVTYPE, HWND, DWORD,
                                                        D3DPRESENT_PARAMETERS *, IDirect3DDevice8 **);
    using CreateTextureFn = HRESULT(STDMETHODCALLTYPE *)(IDirect3DDevice8 *, UINT, UINT, UINT, DWORD,
                                                         D3DFORMAT, D3DPOOL, IDirect3DTexture8 **);
    using SetTextureFn = HRESULT(STDMETHODCALLTYPE *)(IDirect3DDevice8 *, DWORD, IDirect3DBaseTexture8 *);

    Direct3DCreate8Fn g_original_direct3d_create8 = nullptr;
    CreateDeviceFn g_original_create_device = nullptr;
    CreateTextureFn g_original_create_texture = nullptr;
    SetTextureFn g_original_set_texture = nullptr;

    bool g_d3d8_interface_hooked = false;
    bool g_device_hooked = false;

    uint64_t rotate_left(uint64_t value, int amount)
    {
        return (value << amount) | (value >> (64 - amount));
    }

    uint64_t load_u64(const uint8_t *data)
    {
        uint64_t value = 0;
        std::memcpy(&value, data, sizeof(value));
        return value;
    }

    uint64_t mix_word(uint64_t hash, uint64_t word)
    {
        hash ^= word * kPrime1;
        hash = rotate_left(hash, 31);
        hash *= kPrime2;
        return hash;
    }

    uint64_t hash_bytes(const uint8_t *data, size_t size)
    {
        if (data == nullptr || size == 0)
            return 0;

        uint64_t hash = 0x27D4EB2F165667C5ull;
        const size_t original_size = size;
        while (size >= 8)
        {
            hash = mix_word(hash, load_u64(data));
            data += 8;
            size -= 8;
        }
        if (size != 0)
        {
            uint8_t tail[8] = {};
            std::memcpy(tail, data, size);
            hash = mix_word(hash, load_u64(tail));
        }

        hash ^= static_cast<uint64_t>(original_size);
        hash ^= hash >> 33;
        hash *= 0xFF51AFD7ED558CCDull;
        hash ^= hash >> 33;
        hash *= 0xC4CEB9FE1A85EC53ull;
        hash ^= hash >> 33;
        return hash;
    }

    std::string format_hash(uint64_t hash)
    {
        char text[24] = {};
        std::snprintf(text, sizeof(text), "%016llX", static_cast<unsigned long long>(hash));
        return text;
    }

    std::wstring widen_ascii(const std::string &value)
    {
        return std::wstring(value.begin(), value.end());
    }

    std::wstring join_path(const std::wstring &left, const std::wstring &right)
    {
        if (left.empty())
            return right;
        if (left.back() == L'\\' || left.back() == L'/')
            return left + right;
        return left + L"\\" + right;
    }

    void log_line(const char *level, const char *format, ...)
    {
        char message[2048] = {};
        va_list args;
        va_start(args, format);
        std::vsnprintf(message, sizeof(message), format, args);
        va_end(args);

        SYSTEMTIME now = {};
        GetLocalTime(&now);
        char line[2300] = {};
        std::snprintf(line, sizeof(line), "%04u-%02u-%02u %02u:%02u:%02u [%s] %s\r\n",
                      now.wYear, now.wMonth, now.wDay, now.wHour, now.wMinute, now.wSecond,
                      level, message);
        OutputDebugStringA(line);

        std::lock_guard<std::mutex> lock(g_log_mutex);
        HANDLE file = CreateFileW(g_log_path.c_str(), FILE_APPEND_DATA,
                                  FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                  nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file != INVALID_HANDLE_VALUE)
        {
            DWORD written = 0;
            WriteFile(file, line, static_cast<DWORD>(std::strlen(line)), &written, nullptr);
            CloseHandle(file);
        }
    }

    bool file_exists(const std::wstring &path)
    {
        const DWORD attrs = GetFileAttributesW(path.c_str());
        return attrs != INVALID_FILE_ATTRIBUTES && (attrs & FILE_ATTRIBUTE_DIRECTORY) == 0;
    }

    bool query_file_stamp(const std::wstring &path, uint64_t &size, FILETIME &write_time)
    {
        WIN32_FILE_ATTRIBUTE_DATA data = {};
        if (!GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &data) ||
            (data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0)
            return false;
        size = (static_cast<uint64_t>(data.nFileSizeHigh) << 32) | data.nFileSizeLow;
        write_time = data.ftLastWriteTime;
        // A DDS needs the 4-byte magic plus a 124-byte base header. Treat partially written,
        // truncated or zero-byte files exactly like a deleted cache entry so they are regenerated
        // instead of permanently blocking capture/reload.
        return size >= 128 && size <= kMaxFileBytes;
    }

    bool same_filetime(const FILETIME &left, const FILETIME &right)
    {
        return left.dwLowDateTime == right.dwLowDateTime &&
               left.dwHighDateTime == right.dwHighDateTime;
    }

    void restore_texture_filtering(IDirect3DDevice8 *device)
    {
        if (device == nullptr)
            return;
        TextureFilterState saved;
        {
            std::lock_guard<std::mutex> lock(g_state_mutex);
            auto found = g_filter_states.find(device);
            if (found == g_filter_states.end() || !found->second.captured || !found->second.enhanced)
                return;
            saved = found->second;
            found->second.enhanced = false;
        }
        device->SetTextureStageState(0, D3DTSS_MINFILTER, saved.min_filter);
        device->SetTextureStageState(0, D3DTSS_MAGFILTER, saved.mag_filter);
        device->SetTextureStageState(0, D3DTSS_MIPFILTER, saved.mip_filter);
        device->SetTextureStageState(0, D3DTSS_MAXANISOTROPY, saved.max_anisotropy);
    }

    void enhance_texture_filtering(IDirect3DDevice8 *device)
    {
        if (device == nullptr)
            return;

        TextureFilterState saved;
        bool already_enhanced = false;
        {
            std::lock_guard<std::mutex> lock(g_state_mutex);
            auto &state = g_filter_states[device];
            already_enhanced = state.enhanced;
            if (!state.captured)
            {
                DWORD min_filter = 0, mag_filter = 0, mip_filter = 0, max_anisotropy = 1;
                if (FAILED(device->GetTextureStageState(0, D3DTSS_MINFILTER, &min_filter)) ||
                    FAILED(device->GetTextureStageState(0, D3DTSS_MAGFILTER, &mag_filter)) ||
                    FAILED(device->GetTextureStageState(0, D3DTSS_MIPFILTER, &mip_filter)) ||
                    FAILED(device->GetTextureStageState(0, D3DTSS_MAXANISOTROPY, &max_anisotropy)))
                {
                    g_filter_states.erase(device);
                    return;
                }
                state.captured = true;
                state.min_filter = min_filter;
                state.mag_filter = mag_filter;
                state.mip_filter = mip_filter;
                state.max_anisotropy = max_anisotropy;
            }
            state.enhanced = true;
            saved = state;
        }
        if (already_enhanced)
            return;

        D3DCAPS8 caps = {};
        if (FAILED(device->GetDeviceCaps(&caps)))
            return;
        const DWORD anisotropy = (std::max)(static_cast<DWORD>(1),
            (std::min)(static_cast<DWORD>(16), caps.MaxAnisotropy));
        const DWORD min_filter = anisotropy > 1 &&
            (caps.TextureFilterCaps & D3DPTFILTERCAPS_MINFANISOTROPIC) != 0
            ? D3DTEXF_ANISOTROPIC
            : ((caps.TextureFilterCaps & D3DPTFILTERCAPS_MINFLINEAR) != 0
               ? D3DTEXF_LINEAR : saved.min_filter);
        const DWORD mag_filter = (caps.TextureFilterCaps & D3DPTFILTERCAPS_MAGFLINEAR) != 0
            ? D3DTEXF_LINEAR : saved.mag_filter;
        const DWORD mip_filter = (caps.TextureFilterCaps & D3DPTFILTERCAPS_MIPFLINEAR) != 0
            ? D3DTEXF_LINEAR : saved.mip_filter;

        device->SetTextureStageState(0, D3DTSS_MAXANISOTROPY, anisotropy);
        device->SetTextureStageState(0, D3DTSS_MINFILTER, min_filter);
        device->SetTextureStageState(0, D3DTSS_MAGFILTER, mag_filter);
        device->SetTextureStageState(0, D3DTSS_MIPFILTER, mip_filter);
    }

    void release_replacement_cache(const char *reason)
    {
        std::vector<IDirect3DTexture8 *> textures;
        {
            std::lock_guard<std::mutex> lock(g_state_mutex);
            textures.reserve(g_replacements.size());
            for (const auto &entry : g_replacements)
            {
                if (entry.second.texture != nullptr)
                    textures.push_back(entry.second.texture);
            }
            g_replacements.clear();
            g_last_file_check.clear();
            g_replacement_bytes = 0;
        }
        for (IDirect3DTexture8 *texture : textures)
            texture->Release();
        if (!textures.empty())
            log_line("INFO", "%zu replacement(s) liberado(s): %s", textures.size(), reason);
    }

    void set_runtime_enabled(bool enabled, const char *source)
    {
        const bool previous = g_runtime_enabled.exchange(enabled, std::memory_order_relaxed);
        if (previous == enabled)
            return;
        if (!enabled)
            release_replacement_cache("injecao desativada");
        else
        {
            std::lock_guard<std::mutex> lock(g_state_mutex);
            g_last_file_check.clear();
        }
        log_line("INFO", "Texturas aprimoradas %s por %s",
                 enabled ? "ATIVADAS" : "DESATIVADAS; originais restauradas", source);
    }

    bool write_runtime_disabled_marker(bool disabled)
    {
        if (!disabled)
        {
            if (!file_exists(g_disable_path))
                return true;
            return DeleteFileW(g_disable_path.c_str()) != FALSE;
        }
        CreateDirectoryW(join_path(g_game_dir, L"TT").c_str(), nullptr);
        HANDLE file = CreateFileW(g_disable_path.c_str(), GENERIC_WRITE,
                                  FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr, CREATE_ALWAYS,
                                  FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file == INVALID_HANDLE_VALUE)
            return false;
        constexpr char marker[] = "TextureFlow runtime disabled\r\n";
        DWORD written = 0;
        const bool ok = WriteFile(file, marker, sizeof(marker) - 1, &written, nullptr) &&
                        written == sizeof(marker) - 1;
        CloseHandle(file);
        return ok;
    }

    void refresh_runtime_control()
    {
#ifdef TEXTUREFLOW_WIBO_SMOKE
        return;
#else
        const DWORD now = GetTickCount();
        if (now - g_last_control_check < 125)
            return;
        g_last_control_check = now;

        bool requested = g_enable_injection && !file_exists(g_disable_path);
        const bool key_down = g_toggle_hotkey != 0 &&
                              (GetAsyncKeyState(static_cast<int>(g_toggle_hotkey)) & 0x8000) != 0;
        if (key_down && !g_toggle_key_was_down && g_enable_injection)
        {
            requested = !g_runtime_enabled.load(std::memory_order_relaxed);
            if (!write_runtime_disabled_marker(!requested))
                log_line("WARN", "HOME nao conseguiu atualizar TextureFlow.disabled");
        }
        g_toggle_key_was_down = key_down;
        set_runtime_enabled(requested, key_down ? "atalho" : "manager");
#endif
    }

    std::vector<IDirect3DTexture8 *> evict_to_budget_locked(uint64_t protected_hash)
    {
        std::vector<IDirect3DTexture8 *> evicted;
        while (g_replacement_bytes > g_vram_budget_bytes && g_replacements.size() > 1)
        {
            auto victim = g_replacements.end();
            for (auto candidate = g_replacements.begin(); candidate != g_replacements.end(); ++candidate)
            {
                if (candidate->first == protected_hash)
                    continue;
                if (victim == g_replacements.end() ||
                    candidate->second.last_used < victim->second.last_used)
                    victim = candidate;
            }
            if (victim == g_replacements.end())
                break;
            if (victim->second.texture != nullptr)
                evicted.push_back(victim->second.texture);
            g_replacement_bytes = victim->second.resident_bytes > g_replacement_bytes
                ? 0 : g_replacement_bytes - victim->second.resident_bytes;
            g_last_file_check.erase(victim->first);
            g_replacements.erase(victim);
        }
        return evicted;
    }

    bool read_file(const std::wstring &path, std::vector<uint8_t> &data)
    {
        HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE,
                                  nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file == INVALID_HANDLE_VALUE)
            return false;

        LARGE_INTEGER length = {};
        if (!GetFileSizeEx(file, &length) || length.QuadPart <= 0 ||
            static_cast<uint64_t>(length.QuadPart) > kMaxFileBytes)
        {
            CloseHandle(file);
            return false;
        }
        data.resize(static_cast<size_t>(length.QuadPart));
        size_t offset = 0;
        bool ok = true;
        while (offset < data.size())
        {
            const DWORD request = static_cast<DWORD>((std::min)(data.size() - offset, static_cast<size_t>(16 * 1024 * 1024)));
            DWORD received = 0;
            if (!ReadFile(file, data.data() + offset, request, &received, nullptr) || received == 0)
            {
                ok = false;
                break;
            }
            offset += received;
        }
        CloseHandle(file);
        return ok && offset == data.size();
    }

#pragma pack(push, 1)
    struct DdsPixelFormat
    {
        uint32_t size;
        uint32_t flags;
        uint32_t fourcc;
        uint32_t rgb_bits;
        uint32_t r_mask;
        uint32_t g_mask;
        uint32_t b_mask;
        uint32_t a_mask;
    };

    struct DdsHeader
    {
        uint32_t size;
        uint32_t flags;
        uint32_t height;
        uint32_t width;
        uint32_t pitch_or_linear_size;
        uint32_t depth;
        uint32_t mip_count;
        uint32_t reserved[11];
        DdsPixelFormat pixel_format;
        uint32_t caps;
        uint32_t caps2;
        uint32_t caps3;
        uint32_t caps4;
        uint32_t reserved2;
    };

    struct DdsHeaderDx10
    {
        uint32_t format;
        uint32_t resource_dimension;
        uint32_t misc_flag;
        uint32_t array_size;
        uint32_t misc_flags2;
    };
#pragma pack(pop)

    static_assert(sizeof(DdsHeader) == 124, "Unexpected DDS header layout");

    constexpr uint32_t fourcc(char a, char b, char c, char d)
    {
        return static_cast<uint32_t>(static_cast<uint8_t>(a)) |
               (static_cast<uint32_t>(static_cast<uint8_t>(b)) << 8) |
               (static_cast<uint32_t>(static_cast<uint8_t>(c)) << 16) |
               (static_cast<uint32_t>(static_cast<uint8_t>(d)) << 24);
    }

    struct FormatLayout
    {
        D3DFORMAT d3d_format = D3DFMT_UNKNOWN;
        uint32_t dds_fourcc = 0;
        uint32_t bytes_per_pixel = 0;
        uint32_t block_bytes = 0;
        uint32_t r_mask = 0;
        uint32_t g_mask = 0;
        uint32_t b_mask = 0;
        uint32_t a_mask = 0;
    };

    bool describe_d3d_format(D3DFORMAT format, FormatLayout &layout)
    {
        layout = {};
        layout.d3d_format = format;
        switch (format)
        {
        case D3DFMT_DXT1:
            layout.dds_fourcc = fourcc('D', 'X', 'T', '1');
            layout.block_bytes = 8;
            return true;
        case D3DFMT_DXT2:
        case D3DFMT_DXT3:
            layout.dds_fourcc = fourcc('D', 'X', 'T', '3');
            layout.block_bytes = 16;
            layout.d3d_format = D3DFMT_DXT3;
            return true;
        case D3DFMT_DXT4:
        case D3DFMT_DXT5:
            layout.dds_fourcc = fourcc('D', 'X', 'T', '5');
            layout.block_bytes = 16;
            layout.d3d_format = D3DFMT_DXT5;
            return true;
        case D3DFMT_A8R8G8B8:
            layout.bytes_per_pixel = 4;
            layout.r_mask = 0x00FF0000u;
            layout.g_mask = 0x0000FF00u;
            layout.b_mask = 0x000000FFu;
            layout.a_mask = 0xFF000000u;
            return true;
        case D3DFMT_X8R8G8B8:
            layout.bytes_per_pixel = 4;
            layout.r_mask = 0x00FF0000u;
            layout.g_mask = 0x0000FF00u;
            layout.b_mask = 0x000000FFu;
            return true;
        default:
            return false;
        }
    }

    void tight_layout(const FormatLayout &format, uint32_t width, uint32_t height,
                      uint32_t &row_bytes, uint32_t &rows, uint32_t &slice_bytes)
    {
        if (format.block_bytes != 0)
        {
            row_bytes = ((width + 3) / 4) * format.block_bytes;
            rows = (std::max)(1u, (height + 3) / 4);
        }
        else
        {
            row_bytes = width * format.bytes_per_pixel;
            rows = height;
        }
        slice_bytes = row_bytes * rows;
    }

    bool write_dump_dds(const std::wstring &path, uint32_t width, uint32_t height,
                        const FormatLayout &format, const std::vector<uint8_t> &pixels)
    {
        uint32_t row_bytes = 0, rows = 0, slice_bytes = 0;
        tight_layout(format, width, height, row_bytes, rows, slice_bytes);
        if (slice_bytes == 0 || pixels.size() != slice_bytes)
            return false;

        CreateDirectoryW(join_path(g_game_dir, L"TT").c_str(), nullptr);
        CreateDirectoryW(g_dump_dir.c_str(), nullptr);
        const std::wstring temp = path + L".tmp";
        HANDLE file = CreateFileW(temp.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
                                  FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file == INVALID_HANDLE_VALUE)
            return false;

        const uint32_t magic = fourcc('D', 'D', 'S', ' ');
        DdsHeader header = {};
        header.size = sizeof(DdsHeader);
        header.flags = 0x1u | 0x2u | 0x4u | 0x1000u;
        header.height = height;
        header.width = width;
        header.pitch_or_linear_size = (format.block_bytes != 0) ? slice_bytes : row_bytes;
        header.flags |= (format.block_bytes != 0) ? 0x80000u : 0x8u;
        header.mip_count = 1;
        header.pixel_format.size = sizeof(DdsPixelFormat);
        if (format.dds_fourcc != 0)
        {
            header.pixel_format.flags = 0x4u;
            header.pixel_format.fourcc = format.dds_fourcc;
        }
        else
        {
            header.pixel_format.flags = 0x40u | (format.a_mask != 0 ? 0x1u : 0u);
            header.pixel_format.rgb_bits = format.bytes_per_pixel * 8;
            header.pixel_format.r_mask = format.r_mask;
            header.pixel_format.g_mask = format.g_mask;
            header.pixel_format.b_mask = format.b_mask;
            header.pixel_format.a_mask = format.a_mask;
        }
        header.caps = 0x1000u;

        DWORD written = 0;
        bool ok = WriteFile(file, &magic, sizeof(magic), &written, nullptr) && written == sizeof(magic);
        ok = ok && WriteFile(file, &header, sizeof(header), &written, nullptr) && written == sizeof(header);
        ok = ok && WriteFile(file, pixels.data(), static_cast<DWORD>(pixels.size()), &written, nullptr) &&
             written == pixels.size();
        if (ok)
            ok = FlushFileBuffers(file) != FALSE;
        CloseHandle(file);
        if (!ok)
        {
            DeleteFileW(temp.c_str());
            return false;
        }
        if (!MoveFileExW(temp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
        {
            DeleteFileW(temp.c_str());
            return false;
        }
        return true;
    }

    uint32_t natural_mip_count(uint32_t width, uint32_t height)
    {
        uint32_t count = 1;
        while (width > 1 || height > 1)
        {
            width = (std::max)(1u, width / 2);
            height = (std::max)(1u, height / 2);
            ++count;
        }
        return count;
    }

    struct ParsedDds
    {
        uint32_t width = 0;
        uint32_t height = 0;
        uint32_t mip_count = 0;
        FormatLayout format;
        size_t payload_offset = 0;
        std::vector<uint32_t> row_bytes;
        std::vector<uint32_t> rows;
        std::vector<uint32_t> slice_bytes;
    };

    bool parse_dds(const std::vector<uint8_t> &file, ParsedDds &dds, std::string &error)
    {
        if (file.size() < 4 + sizeof(DdsHeader))
        {
            error = "arquivo pequeno demais";
            return false;
        }
        uint32_t magic = 0;
        std::memcpy(&magic, file.data(), sizeof(magic));
        if (magic != fourcc('D', 'D', 'S', ' '))
        {
            error = "assinatura DDS invalida";
            return false;
        }

        DdsHeader header = {};
        std::memcpy(&header, file.data() + 4, sizeof(header));
        if (header.size != sizeof(DdsHeader) || header.pixel_format.size != sizeof(DdsPixelFormat))
        {
            error = "cabecalho DDS desconhecido";
            return false;
        }
        if (header.width == 0 || header.height == 0 || header.width > kMaxDimension || header.height > kMaxDimension)
        {
            error = "dimensoes DDS fora do limite";
            return false;
        }
        if ((header.caps2 & 0x00200200u) != 0)
        {
            error = "cubemap/volume nao suportado";
            return false;
        }

        dds.width = header.width;
        dds.height = header.height;
        dds.payload_offset = 4 + sizeof(DdsHeader);
        D3DFORMAT d3d_format = D3DFMT_UNKNOWN;

        if ((header.pixel_format.flags & 0x4u) != 0)
        {
            switch (header.pixel_format.fourcc)
            {
            case fourcc('D', 'X', 'T', '1'): d3d_format = D3DFMT_DXT1; break;
            case fourcc('D', 'X', 'T', '2'):
            case fourcc('D', 'X', 'T', '3'): d3d_format = D3DFMT_DXT3; break;
            case fourcc('D', 'X', 'T', '4'):
            case fourcc('D', 'X', 'T', '5'): d3d_format = D3DFMT_DXT5; break;
            case fourcc('D', 'X', '1', '0'):
            {
                if (file.size() < dds.payload_offset + sizeof(DdsHeaderDx10))
                {
                    error = "cabecalho DX10 truncado";
                    return false;
                }
                DdsHeaderDx10 dx10 = {};
                std::memcpy(&dx10, file.data() + dds.payload_offset, sizeof(dx10));
                dds.payload_offset += sizeof(dx10);
                if (dx10.array_size != 1 || dx10.resource_dimension != 3)
                {
                    error = "DDS DX10 nao e textura 2D simples";
                    return false;
                }
                switch (dx10.format)
                {
                case 71: case 72: d3d_format = D3DFMT_DXT1; break;
                case 74: case 75: d3d_format = D3DFMT_DXT3; break;
                case 77: case 78: d3d_format = D3DFMT_DXT5; break;
                case 87: case 91: d3d_format = D3DFMT_A8R8G8B8; break;
                default:
                    error = "formato DXGI nao suportado pelo D3D8";
                    return false;
                }
                break;
            }
            default:
                error = "FourCC nao suportado pelo D3D8";
                return false;
            }
        }
        else if ((header.pixel_format.flags & 0x40u) != 0 && header.pixel_format.rgb_bits == 32)
        {
            if (header.pixel_format.r_mask == 0x00FF0000u && header.pixel_format.b_mask == 0x000000FFu)
                d3d_format = header.pixel_format.a_mask != 0 ? D3DFMT_A8R8G8B8 : D3DFMT_X8R8G8B8;
        }
        if (d3d_format == D3DFMT_UNKNOWN || !describe_d3d_format(d3d_format, dds.format))
        {
            error = "pixel format DDS nao suportado";
            return false;
        }

        dds.mip_count = header.mip_count != 0 ? header.mip_count : 1;
        dds.mip_count = (std::min)(dds.mip_count, natural_mip_count(dds.width, dds.height));
        uint32_t width = dds.width;
        uint32_t height = dds.height;
        uint64_t total_payload = 0;
        for (uint32_t level = 0; level < dds.mip_count; ++level)
        {
            uint32_t row = 0, rows = 0, slice = 0;
            tight_layout(dds.format, width, height, row, rows, slice);
            if (row == 0 || rows == 0 || slice == 0)
            {
                error = "tamanho de mip invalido";
                return false;
            }
            dds.row_bytes.push_back(row);
            dds.rows.push_back(rows);
            dds.slice_bytes.push_back(slice);
            total_payload += slice;
            width = (std::max)(1u, width / 2);
            height = (std::max)(1u, height / 2);
        }
        if (dds.payload_offset + total_payload > file.size())
        {
            error = "payload DDS truncado";
            return false;
        }
        return true;
    }

    IDirect3DTexture8 *load_replacement(IDirect3DDevice8 *device, const std::wstring &path,
                                        uint64_t hash, uint64_t &resident_bytes)
    {
        resident_bytes = 0;
        std::vector<uint8_t> bytes;
        if (!read_file(path, bytes))
        {
            log_line("WARN", "Nao foi possivel ler replacement %s", format_hash(hash).c_str());
            return nullptr;
        }
        ParsedDds dds;
        std::string error;
        if (!parse_dds(bytes, dds, error))
        {
            log_line("WARN", "Replacement %s rejeitado: %s", format_hash(hash).c_str(), error.c_str());
            return nullptr;
        }

        IDirect3DTexture8 *texture = nullptr;
        g_inside_hook = true;
        HRESULT result = g_original_create_texture != nullptr
            ? g_original_create_texture(device, dds.width, dds.height, dds.mip_count, 0,
                                        dds.format.d3d_format, D3DPOOL_MANAGED, &texture)
            : device->CreateTexture(dds.width, dds.height, dds.mip_count, 0,
                                    dds.format.d3d_format, D3DPOOL_MANAGED, &texture);
        g_inside_hook = false;
        if (FAILED(result) || texture == nullptr)
        {
            log_line("WARN", "D3D8 recusou replacement %s (%ux%u, fmt=%lu, hr=0x%08lX)",
                     format_hash(hash).c_str(), dds.width, dds.height,
                     static_cast<unsigned long>(dds.format.d3d_format), static_cast<unsigned long>(result));
            return nullptr;
        }

        size_t offset = dds.payload_offset;
        bool upload_ok = true;
        for (uint32_t level = 0; level < dds.mip_count; ++level)
        {
            D3DLOCKED_RECT locked = {};
            if (FAILED(texture->LockRect(level, &locked, nullptr, 0)) || locked.pBits == nullptr ||
                locked.Pitch <= 0 || static_cast<uint32_t>(locked.Pitch) < dds.row_bytes[level])
            {
                upload_ok = false;
                break;
            }
            const uint8_t *source = bytes.data() + offset;
            auto *destination = static_cast<uint8_t *>(locked.pBits);
            for (uint32_t row = 0; row < dds.rows[level]; ++row)
                std::memcpy(destination + static_cast<size_t>(row) * locked.Pitch,
                            source + static_cast<size_t>(row) * dds.row_bytes[level],
                            dds.row_bytes[level]);
            texture->UnlockRect(level);
            offset += dds.slice_bytes[level];
        }
        if (!upload_ok)
        {
            texture->Release();
            log_line("WARN", "Falha ao enviar mips do replacement %s", format_hash(hash).c_str());
            return nullptr;
        }

        for (uint32_t slice : dds.slice_bytes)
            resident_bytes += slice;

        log_line("INFO", "Replacement carregado: %s.dds (%ux%u, %u mips, %.1f MiB)",
                 format_hash(hash).c_str(), dds.width, dds.height, dds.mip_count,
                 static_cast<double>(resident_bytes) / (1024.0 * 1024.0));
        return texture;
    }

    IDirect3DTexture8 *find_or_load_replacement(IDirect3DDevice8 *device, uint64_t hash)
    {
        if (!g_enable_injection || !g_runtime_enabled.load(std::memory_order_relaxed) || hash == 0)
            return nullptr;

        // Unsigned subtraction remains correct across GetTickCount's 49-day wraparound.
        const DWORD now = GetTickCount();
        {
            std::lock_guard<std::mutex> lock(g_state_mutex);
            auto existing = g_replacements.find(hash);
            auto last = g_last_file_check.find(hash);
            if (last != g_last_file_check.end() && now - last->second < g_check_interval_ms)
            {
                if (existing != g_replacements.end())
                    existing->second.last_used = ++g_use_sequence;
                return existing != g_replacements.end() ? existing->second.texture : nullptr;
            }
            g_last_file_check[hash] = now;
        }

        const std::wstring path = join_path(g_inject_dir, widen_ascii(format_hash(hash)) + L".dds");
        uint64_t file_size = 0;
        FILETIME write_time = {};
        if (!query_file_stamp(path, file_size, write_time))
        {
            IDirect3DTexture8 *removed = nullptr;
            {
                std::lock_guard<std::mutex> lock(g_state_mutex);
                auto existing = g_replacements.find(hash);
                if (existing != g_replacements.end())
                {
                    removed = existing->second.texture;
                    g_replacement_bytes = existing->second.resident_bytes > g_replacement_bytes
                        ? 0 : g_replacement_bytes - existing->second.resident_bytes;
                    g_replacements.erase(existing);
                }
            }
            if (removed != nullptr)
            {
                removed->Release();
                log_line("INFO", "Replacement removido; textura original restaurada: %s.dds",
                         format_hash(hash).c_str());
            }
            return nullptr;
        }

        {
            std::lock_guard<std::mutex> lock(g_state_mutex);
            auto existing = g_replacements.find(hash);
            if (existing != g_replacements.end() && existing->second.file_size == file_size &&
                same_filetime(existing->second.write_time, write_time))
            {
                existing->second.last_used = ++g_use_sequence;
                return existing->second.texture;
            }
        }

        uint64_t resident_bytes = 0;
        IDirect3DTexture8 *fresh = load_replacement(device, path, hash, resident_bytes);
        if (fresh == nullptr)
            return nullptr;

        IDirect3DTexture8 *old = nullptr;
        std::vector<IDirect3DTexture8 *> evicted;
        uint64_t resident_after = 0;
        {
            std::lock_guard<std::mutex> lock(g_state_mutex);
            auto &entry = g_replacements[hash];
            old = entry.texture;
            g_replacement_bytes = entry.resident_bytes > g_replacement_bytes
                ? 0 : g_replacement_bytes - entry.resident_bytes;
            entry.texture = fresh;
            entry.file_size = file_size;
            entry.write_time = write_time;
            entry.resident_bytes = resident_bytes;
            entry.last_used = ++g_use_sequence;
            g_replacement_bytes += resident_bytes;
            evicted = evict_to_budget_locked(hash);
            resident_after = g_replacement_bytes;
        }
        if (old != nullptr)
            old->Release();
        for (IDirect3DTexture8 *texture : evicted)
            texture->Release();
        if (!evicted.empty())
            log_line("INFO", "Limite de VRAM: %zu replacement(s) LRU liberado(s); residente %.1f MiB / %.1f MiB",
                     evicted.size(), static_cast<double>(resident_after) / (1024.0 * 1024.0),
                     static_cast<double>(g_vram_budget_bytes) / (1024.0 * 1024.0));
        ++g_injection_count;
        return fresh;
    }

    uint64_t capture_texture(IDirect3DTexture8 *texture)
    {
        D3DSURFACE_DESC description = {};
        if (FAILED(texture->GetLevelDesc(0, &description)) || description.Width == 0 || description.Height == 0)
            return 0;
        if (description.Width < g_min_dump_dimension || description.Height < g_min_dump_dimension ||
            description.Width > kMaxDimension || description.Height > kMaxDimension ||
            (description.Usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL | D3DUSAGE_DYNAMIC)) != 0)
            return 0;

        FormatLayout format;
        if (!describe_d3d_format(description.Format, format))
        {
            const uint64_t format_id = static_cast<uint32_t>(description.Format);
            std::lock_guard<std::mutex> lock(g_state_mutex);
            if (g_logged_unsupported_formats.insert(format_id).second)
                log_line("INFO", "Formato D3D8 nao capturado: %lu", static_cast<unsigned long>(description.Format));
            return 0;
        }

        uint32_t row_bytes = 0, rows = 0, slice_bytes = 0;
        tight_layout(format, description.Width, description.Height, row_bytes, rows, slice_bytes);
        if (slice_bytes == 0 || slice_bytes > kMaxFileBytes)
            return 0;

        D3DLOCKED_RECT locked = {};
        if (FAILED(texture->LockRect(0, &locked, nullptr, D3DLOCK_READONLY)) || locked.pBits == nullptr ||
            locked.Pitch <= 0 || static_cast<uint32_t>(locked.Pitch) < row_bytes)
            return 0;

        std::vector<uint8_t> pixels(slice_bytes);
        const auto *source = static_cast<const uint8_t *>(locked.pBits);
        for (uint32_t row = 0; row < rows; ++row)
            std::memcpy(pixels.data() + static_cast<size_t>(row) * row_bytes,
                        source + static_cast<size_t>(row) * locked.Pitch, row_bytes);
        texture->UnlockRect(0);

        const uint64_t hash = hash_bytes(pixels.data(), pixels.size());
        if (hash == 0)
            return 0;

        if (g_auto_dump)
        {
            const std::wstring path = join_path(g_dump_dir, widen_ascii(format_hash(hash)) + L".dds");
            uint64_t existing_size = 0;
            FILETIME existing_time = {};
            if (!query_file_stamp(path, existing_size, existing_time))
            {
                if (!write_dump_dds(path, description.Width, description.Height, format, pixels))
                {
                    log_line("WARN", "Falha ao gravar dump %s.dds", format_hash(hash).c_str());
                }
                else
                {
                    log_line("INFO", "Textura capturada: %s.dds (%ux%u, fmt=%lu)",
                             format_hash(hash).c_str(), description.Width, description.Height,
                             static_cast<unsigned long>(description.Format));
                    ++g_capture_count;
                }
            }
        }
        return hash;
    }

    uint64_t ensure_texture_hash(IDirect3DBaseTexture8 *base)
    {
        if (base == nullptr || base->GetType() != D3DRTYPE_TEXTURE)
            return 0;

        uint64_t known_hash = 0;
        bool verify_existing_dump = false;
        {
            std::lock_guard<std::mutex> lock(g_state_mutex);
            auto &record = g_textures[base];
            if (record.capture_attempted)
            {
                known_hash = record.hash;
                const DWORD now = GetTickCount();
                if (known_hash == 0 || !g_auto_dump ||
                    now - record.last_dump_check < g_check_interval_ms)
                    return known_hash;
                record.last_dump_check = now;
                verify_existing_dump = true;
            }
            if (record.capture_in_progress)
                return known_hash;
            if (!verify_existing_dump)
                record.capture_in_progress = true;
        }

        if (verify_existing_dump)
        {
            const std::wstring path = join_path(
                g_dump_dir, widen_ascii(format_hash(known_hash)) + L".dds");
            uint64_t size = 0;
            FILETIME write_time = {};
            if (query_file_stamp(path, size, write_time))
                return known_hash;
            std::lock_guard<std::mutex> lock(g_state_mutex);
            auto &record = g_textures[base];
            if (record.capture_in_progress)
                return known_hash;
            record.capture_in_progress = true;
        }

        uint64_t hash = capture_texture(static_cast<IDirect3DTexture8 *>(base));
        {
            std::lock_guard<std::mutex> lock(g_state_mutex);
            auto &record = g_textures[base];
            record.hash = hash;
            record.capture_attempted = true;
            record.capture_in_progress = false;
            record.last_dump_check = GetTickCount();
        }
        if (hash == 0)
            ++g_capture_failures;
        return hash;
    }

    void hook_device(IDirect3DDevice8 *device);
    void hook_d3d8_interface(IDirect3D8 *d3d8);

    IDirect3D8 *WINAPI hooked_direct3d_create8(UINT sdk_version)
    {
        IDirect3D8 *d3d8 = g_original_direct3d_create8 != nullptr
            ? g_original_direct3d_create8(sdk_version) : nullptr;
        if (d3d8 != nullptr)
        {
            try
            {
                hook_d3d8_interface(d3d8);
            }
            catch (...)
            {
                log_line("ERROR", "Excecao interna ao preparar interface D3D8; jogo preservado");
            }
        }
        return d3d8;
    }

    HRESULT STDMETHODCALLTYPE hooked_create_device(IDirect3D8 *self, UINT adapter, D3DDEVTYPE device_type,
                                                    HWND focus_window, DWORD behavior_flags,
                                                    D3DPRESENT_PARAMETERS *parameters,
                                                    IDirect3DDevice8 **returned_device)
    {
        const HRESULT result = g_original_create_device != nullptr
            ? g_original_create_device(self, adapter, device_type, focus_window, behavior_flags,
                                       parameters, returned_device)
            : D3DERR_INVALIDCALL;
        if (SUCCEEDED(result) && returned_device != nullptr && *returned_device != nullptr)
        {
            g_device_seen.store(true, std::memory_order_relaxed);
            try
            {
                hook_device(*returned_device);
                log_line("INFO", "Dispositivo Direct3D 8 interceptado");
            }
            catch (...)
            {
                log_line("ERROR", "Excecao interna ao preparar dispositivo D3D8; jogo preservado");
            }
        }
        return result;
    }

    HRESULT STDMETHODCALLTYPE hooked_create_texture(IDirect3DDevice8 *device, UINT width, UINT height,
                                                     UINT levels, DWORD usage, D3DFORMAT format,
                                                     D3DPOOL pool, IDirect3DTexture8 **texture)
    {
        const HRESULT result = g_original_create_texture != nullptr
            ? g_original_create_texture(device, width, height, levels, usage, format, pool, texture)
            : D3DERR_INVALIDCALL;
        if (!g_inside_hook && SUCCEEDED(result) && texture != nullptr && *texture != nullptr)
        {
            try
            {
                std::lock_guard<std::mutex> lock(g_state_mutex);
                // COM object addresses can be reused. Reset stale bookkeeping whenever D3D creates
                // a new texture at an address we have seen before.
                g_textures.erase(*texture);
                g_textures.emplace(*texture, TextureRecord{});
            }
            catch (...)
            {
                log_line("ERROR", "Excecao interna no registro de textura; textura original mantida");
            }
        }
        return result;
    }

    HRESULT STDMETHODCALLTYPE hooked_set_texture(IDirect3DDevice8 *device, DWORD stage,
                                                  IDirect3DBaseTexture8 *texture)
    {
        ++g_set_texture_calls;
        // Postal 2 uses later stages for lightmaps and other modulation layers. Upscaling or
        // replacing those layers changes the lighting of the whole scene instead of its detail.
        if (stage != 0)
            return g_original_set_texture != nullptr
                ? g_original_set_texture(device, stage, texture) : D3DERR_INVALIDCALL;

        IDirect3DBaseTexture8 *selected = texture;
        if (!g_inside_hook)
        {
            try
            {
                refresh_runtime_control();
                if (texture == nullptr)
                {
                    restore_texture_filtering(device);
                    return g_original_set_texture != nullptr
                        ? g_original_set_texture(device, stage, nullptr) : D3DERR_INVALIDCALL;
                }
                g_inside_hook = true;
                const uint64_t hash = ensure_texture_hash(texture);
                if (hash != 0 && g_runtime_enabled.load(std::memory_order_relaxed))
                {
                    if (IDirect3DTexture8 *replacement = find_or_load_replacement(device, hash))
                        selected = replacement;
                }
                g_inside_hook = false;
                if (selected != texture)
                    enhance_texture_filtering(device);
                else
                    restore_texture_filtering(device);
            }
            catch (...)
            {
                g_inside_hook = false;
                selected = texture;
                restore_texture_filtering(device);
                log_line("ERROR", "Excecao interna ao processar textura; original mantida");
            }
        }
        return g_original_set_texture != nullptr
            ? g_original_set_texture(device, stage, selected) : D3DERR_INVALIDCALL;
    }

    bool install_hook(void *target, void *detour, void **original, const char *name)
    {
        if (target == nullptr)
            return false;
        const MH_STATUS created = MH_CreateHook(target, detour, original);
        if (created != MH_OK && created != MH_ERROR_ALREADY_CREATED)
        {
            log_line("ERROR", "MH_CreateHook falhou para %s: %d", name, static_cast<int>(created));
            return false;
        }
        const MH_STATUS enabled = MH_EnableHook(target);
        if (enabled != MH_OK && enabled != MH_ERROR_ENABLED)
        {
            log_line("ERROR", "MH_EnableHook falhou para %s: %d", name, static_cast<int>(enabled));
            return false;
        }
        log_line("INFO", "Hook ativo: %s", name);
        return true;
    }

    void hook_d3d8_interface(IDirect3D8 *d3d8)
    {
        std::lock_guard<std::mutex> lock(g_state_mutex);
        if (g_d3d8_interface_hooked || d3d8 == nullptr)
            return;
        void **vtable = *reinterpret_cast<void ***>(d3d8);
        if (install_hook(vtable[15], reinterpret_cast<void *>(&hooked_create_device),
                         reinterpret_cast<void **>(&g_original_create_device),
                         "IDirect3D8::CreateDevice"))
            g_d3d8_interface_hooked = true;
    }

    void hook_device(IDirect3DDevice8 *device)
    {
        std::lock_guard<std::mutex> lock(g_state_mutex);
        if (g_device_hooked || device == nullptr)
            return;
        void **vtable = *reinterpret_cast<void ***>(device);
        const bool create_ok = install_hook(vtable[20], reinterpret_cast<void *>(&hooked_create_texture),
                                            reinterpret_cast<void **>(&g_original_create_texture),
                                            "IDirect3DDevice8::CreateTexture");
        const bool set_ok = install_hook(vtable[61], reinterpret_cast<void *>(&hooked_set_texture),
                                         reinterpret_cast<void **>(&g_original_set_texture),
                                         "IDirect3DDevice8::SetTexture");
        g_device_hooked = create_ok && set_ok;
    }

    DWORD WINAPI watchdog_thread(void *)
    {
        Sleep(20000);
        uint64_t resident_bytes = 0;
        size_t resident_count = 0;
        {
            std::lock_guard<std::mutex> lock(g_state_mutex);
            resident_bytes = g_replacement_bytes;
            resident_count = g_replacements.size();
        }
        log_line("INFO", "Status 20s: device=%u, SetTexture=%llu, capturadas=%llu, carregamentos=%llu, falhas/ignoradas=%llu, residentes=%zu (%.1f/%.1f MiB), injecao=%u",
                 g_device_seen.load(std::memory_order_relaxed) ? 1u : 0u,
                 static_cast<unsigned long long>(g_set_texture_calls.load(std::memory_order_relaxed)),
                 static_cast<unsigned long long>(g_capture_count.load(std::memory_order_relaxed)),
                 static_cast<unsigned long long>(g_injection_count.load(std::memory_order_relaxed)),
                 static_cast<unsigned long long>(g_capture_failures.load(std::memory_order_relaxed)),
                 resident_count, static_cast<double>(resident_bytes) / (1024.0 * 1024.0),
                 static_cast<double>(g_vram_budget_bytes) / (1024.0 * 1024.0),
                 g_runtime_enabled.load(std::memory_order_relaxed) ? 1u : 0u);
        if (!g_device_seen.load(std::memory_order_relaxed))
            log_line("WARN", "Nenhum dispositivo D3D8 apareceu. Confirme o executavel e o renderizador Direct3D 8.");
        else if (g_set_texture_calls.load(std::memory_order_relaxed) == 0)
            log_line("WARN", "D3D8 iniciou, mas nenhuma textura 2D passou por SetTexture.");
        return 0;
    }

    void initialize_paths()
    {
        wchar_t path[MAX_PATH] = {};
        GetModuleFileNameW(nullptr, path, MAX_PATH);
        std::wstring executable(path);
        const size_t slash = executable.find_last_of(L"\\/");
        g_game_dir = slash == std::wstring::npos ? L"." : executable.substr(0, slash);
        g_dump_dir = join_path(join_path(g_game_dir, L"TT"), L"dump");
        g_inject_dir = join_path(join_path(g_game_dir, L"TT"), L"inject");
        g_disable_path = join_path(join_path(g_game_dir, L"TT"), L"TextureFlow.disabled");
        g_log_path = join_path(g_game_dir, L"TextureFlowD3D8.log");

        const std::wstring ini = join_path(g_game_dir, L"TextureFlowD3D8.ini");
        using ProfileIntFn = UINT(WINAPI *)(LPCWSTR, LPCWSTR, INT, LPCWSTR);
        const HMODULE kernel32 = GetModuleHandleW(L"kernel32.dll");
        const auto profile_int = kernel32 != nullptr
            ? reinterpret_cast<ProfileIntFn>(GetProcAddress(kernel32, "GetPrivateProfileIntW")) : nullptr;
        if (profile_int != nullptr)
        {
            g_auto_dump = profile_int(L"TextureFlowD3D8", L"AutoDump", 1, ini.c_str()) != 0;
            g_enable_injection = profile_int(L"TextureFlowD3D8", L"EnableInjection", 1, ini.c_str()) != 0;
            g_check_interval_ms = static_cast<uint32_t>((std::max)(100u, profile_int(
                L"TextureFlowD3D8", L"CheckIntervalMs", 1000, ini.c_str())));
            g_min_dump_dimension = static_cast<uint32_t>((std::max)(1u, profile_int(
                L"TextureFlowD3D8", L"MinDumpDimension", 8, ini.c_str())));
            g_toggle_hotkey = static_cast<uint32_t>((std::min)(255u, profile_int(
                L"TextureFlowD3D8", L"ToggleHotKey", VK_HOME, ini.c_str())));
            const uint64_t budget_mb = static_cast<uint64_t>((std::max)(128u, (std::min)(16384u,
                profile_int(L"TextureFlowD3D8", L"VramBudgetMB", 2048, ini.c_str()))));
            g_vram_budget_bytes = budget_mb * 1024ull * 1024ull;
        }
        CreateDirectoryW(join_path(g_game_dir, L"TT").c_str(), nullptr);
        CreateDirectoryW(g_dump_dir.c_str(), nullptr);
        CreateDirectoryW(g_inject_dir.c_str(), nullptr);
        g_runtime_enabled.store(g_enable_injection && !file_exists(g_disable_path),
                                std::memory_order_relaxed);
    }

    bool initialize_hook()
    {
        initialize_paths();
        log_line("INFO", "TextureFlow D3D8 %s iniciando (x86)", kVersion);
        log_line("INFO", "AutoDump=%u EnableInjection=%u RuntimeEnabled=%u CheckIntervalMs=%u ToggleHotKey=%u VramBudgetMB=%llu",
                 g_auto_dump ? 1u : 0u, g_enable_injection ? 1u : 0u,
                 g_runtime_enabled.load(std::memory_order_relaxed) ? 1u : 0u,
                 g_check_interval_ms, g_toggle_hotkey,
                 static_cast<unsigned long long>(g_vram_budget_bytes / (1024ull * 1024ull)));

        const MH_STATUS initialized = MH_Initialize();
        if (initialized != MH_OK && initialized != MH_ERROR_ALREADY_INITIALIZED)
        {
            log_line("ERROR", "Nao foi possivel iniciar MinHook: %d", static_cast<int>(initialized));
            return false;
        }

        HMODULE d3d8 = GetModuleHandleW(L"d3d8.dll");
        if (d3d8 == nullptr)
        {
            wchar_t system_dir[MAX_PATH] = {};
            const UINT length = GetSystemDirectoryW(system_dir, MAX_PATH);
            if (length == 0 || length >= MAX_PATH)
            {
                log_line("ERROR", "GetSystemDirectory falhou");
                return false;
            }
            d3d8 = LoadLibraryW(join_path(system_dir, L"d3d8.dll").c_str());
        }
        if (d3d8 == nullptr)
        {
            log_line("ERROR", "d3d8.dll nao foi carregada");
            return false;
        }

        void *entry = reinterpret_cast<void *>(GetProcAddress(d3d8, "Direct3DCreate8"));
        if (!install_hook(entry, reinterpret_cast<void *>(&hooked_direct3d_create8),
                          reinterpret_cast<void **>(&g_original_direct3d_create8), "Direct3DCreate8"))
            return false;

        HANDLE watchdog = CreateThread(nullptr, 0, watchdog_thread, nullptr, 0, nullptr);
        if (watchdog != nullptr)
            CloseHandle(watchdog);
        return true;
    }
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID reserved)
{
    if (reason == DLL_PROCESS_ATTACH)
    {
        DisableThreadLibraryCalls(module);
#ifdef TEXTUREFLOW_WIBO_SMOKE
        initialize_paths();
        log_line("INFO", "TextureFlow D3D8 smoke core iniciando");
#else
        initialize_hook();
#endif
    }
    else if (reason == DLL_PROCESS_DETACH && reserved == nullptr)
    {
        // Explicit unload only. During process shutdown Windows owns all remaining COM objects,
        // and attempting to Release them under the loader lock can deadlock the game.
        MH_DisableHook(MH_ALL_HOOKS);
        MH_Uninitialize();
    }
    return TRUE;
}

#ifdef TEXTUREFLOW_WIBO_SMOKE
extern "C" __declspec(dllexport) IDirect3DBaseTexture8 *WINAPI
TextureFlowSmokeSelect(IDirect3DDevice8 *device, DWORD stage, IDirect3DBaseTexture8 *texture)
{
    if (stage != 0)
        return texture;
    if (device == nullptr || texture == nullptr)
    {
        restore_texture_filtering(device);
        return texture;
    }
    try
    {
        const uint64_t hash = ensure_texture_hash(texture);
        if (hash != 0)
        {
            if (IDirect3DTexture8 *replacement = find_or_load_replacement(device, hash))
            {
                enhance_texture_filtering(device);
                return replacement;
            }
        }
    }
    catch (...)
    {
        log_line("ERROR", "Excecao no smoke core; original mantida");
    }
    restore_texture_filtering(device);
    return texture;
}

extern "C" __declspec(dllexport) void WINAPI TextureFlowSmokeSetEnabled(BOOL enabled)
{
    set_runtime_enabled(enabled != FALSE, "smoke test");
}

extern "C" __declspec(dllexport) void WINAPI TextureFlowSmokeSetBudgetBytes(ULONGLONG bytes)
{
    std::vector<IDirect3DTexture8 *> evicted;
    {
        std::lock_guard<std::mutex> lock(g_state_mutex);
        g_vram_budget_bytes = static_cast<uint64_t>(bytes);
        evicted = evict_to_budget_locked(0);
    }
    for (IDirect3DTexture8 *texture : evicted)
        texture->Release();
}

extern "C" __declspec(dllexport) UINT WINAPI TextureFlowSmokeResidentCount()
{
    std::lock_guard<std::mutex> lock(g_state_mutex);
    return static_cast<UINT>(g_replacements.size());
}

extern "C" __declspec(dllexport) ULONGLONG WINAPI TextureFlowSmokeCaptureCount()
{
    return static_cast<ULONGLONG>(g_capture_count.load(std::memory_order_relaxed));
}
#endif
