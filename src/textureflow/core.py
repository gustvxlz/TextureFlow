from __future__ import annotations

import configparser
import bisect
import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence


APP_VERSION = "0.4.1-alpha.1"
RUNTIME_DISABLE_FILENAME = "TextureFlow.disabled"
DEFAULT_QUALITY_MODEL = "4xNomos8kSC"
FALLBACK_FLEXIBLE_MODEL = "RealESRGAN_General_x4_v3"


class TextureFlowError(RuntimeError):
    pass


class UnsupportedTexture(TextureFlowError):
    pass


@dataclass(frozen=True)
class DdsInfo:
    width: int
    height: int
    mip_count: int
    format_name: str
    texconv_format: str
    has_alpha: bool
    is_srgb: bool
    is_cubemap: bool
    is_volume: bool
    header_size: int


_DXGI_FORMATS: dict[int, tuple[str, str, bool, bool]] = {
    28: ("RGBA8", "R8G8B8A8_UNORM", True, False),
    29: ("RGBA8_SRGB", "R8G8B8A8_UNORM_SRGB", True, True),
    71: ("BC1", "BC1_UNORM", False, False),
    72: ("BC1_SRGB", "BC1_UNORM_SRGB", False, True),
    74: ("BC2", "BC2_UNORM", True, False),
    75: ("BC2_SRGB", "BC2_UNORM_SRGB", True, True),
    77: ("BC3", "BC3_UNORM", True, False),
    78: ("BC3_SRGB", "BC3_UNORM_SRGB", True, True),
    80: ("BC4", "BC4_UNORM", False, False),
    83: ("BC5", "BC5_UNORM", False, False),
    84: ("BC5_SNORM", "BC5_SNORM", False, False),
    87: ("BGRA8", "B8G8R8A8_UNORM", True, False),
    91: ("BGRA8_SRGB", "B8G8R8A8_UNORM_SRGB", True, True),
    98: ("BC7", "BC7_UNORM", True, False),
    99: ("BC7_SRGB", "BC7_UNORM_SRGB", True, True),
}

_FOURCC_FORMATS: dict[bytes, tuple[str, str, bool]] = {
    b"DXT1": ("BC1", "BC1_UNORM", False),
    b"DXT3": ("BC2", "BC2_UNORM", True),
    b"DXT5": ("BC3", "BC3_UNORM", True),
    b"ATI1": ("BC4", "BC4_UNORM", False),
    b"BC4U": ("BC4", "BC4_UNORM", False),
    b"ATI2": ("BC5", "BC5_UNORM", False),
    b"BC5U": ("BC5", "BC5_UNORM", False),
}


def parse_dds(path: Path) -> DdsInfo:
    data = path.read_bytes()[:148]
    if len(data) < 128 or data[:4] != b"DDS ":
        raise UnsupportedTexture("arquivo nao e um DDS valido")
    if struct.unpack_from("<I", data, 4)[0] != 124:
        raise UnsupportedTexture("cabecalho DDS desconhecido")

    height, width = struct.unpack_from("<II", data, 12)
    mip_count = max(1, struct.unpack_from("<I", data, 28)[0])
    pf_size, pf_flags = struct.unpack_from("<II", data, 76)
    fourcc = data[84:88]
    rgb_bits = struct.unpack_from("<I", data, 88)[0]
    rmask, gmask, bmask, amask = struct.unpack_from("<IIII", data, 92)
    caps2 = struct.unpack_from("<I", data, 112)[0]
    is_cubemap = bool(caps2 & 0x00000200)
    is_volume = bool(caps2 & 0x00200000)

    if width <= 0 or height <= 0 or width > 32768 or height > 32768:
        raise UnsupportedTexture(f"dimensoes DDS invalidas: {width}x{height}")

    ddpf_fourcc = 0x4
    ddpf_rgb = 0x40
    if pf_flags & ddpf_fourcc:
        if fourcc == b"DX10":
            if len(data) < 148:
                raise UnsupportedTexture("cabecalho DX10 truncado")
            dxgi = struct.unpack_from("<I", data, 128)[0]
            try:
                name, texconv, alpha, srgb = _DXGI_FORMATS[dxgi]
            except KeyError as exc:
                raise UnsupportedTexture(f"formato DXGI {dxgi} nao suportado na alfa") from exc
            return DdsInfo(width, height, mip_count, name, texconv, alpha, srgb,
                           is_cubemap, is_volume, 148)
        try:
            name, texconv, alpha = _FOURCC_FORMATS[fourcc]
        except KeyError as exc:
            shown = fourcc.decode("ascii", "replace")
            raise UnsupportedTexture(f"FourCC {shown!r} nao suportado na alfa") from exc
        return DdsInfo(width, height, mip_count, name, texconv, alpha, False,
                       is_cubemap, is_volume, 128)

    if pf_flags & ddpf_rgb and rgb_bits == 32:
        if (rmask, gmask, bmask) == (0x00FF0000, 0x0000FF00, 0x000000FF):
            texconv = "B8G8R8A8_UNORM"
            name = "BGRA8"
        elif (rmask, gmask, bmask) == (0x000000FF, 0x0000FF00, 0x00FF0000):
            texconv = "R8G8B8A8_UNORM"
            name = "RGBA8"
        else:
            raise UnsupportedTexture("mascaras RGB de 32 bits desconhecidas")
        return DdsInfo(width, height, mip_count, name, texconv, bool(amask), False,
                       is_cubemap, is_volume, 128)

    raise UnsupportedTexture(f"pixel format DDS nao suportado (flags=0x{pf_flags:X}, bits={rgb_bits})")


def read_pe_arch(path: Path) -> str:
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            return "unknown"
        stream.seek(0x3C)
        raw = stream.read(4)
        if len(raw) != 4:
            return "unknown"
        stream.seek(struct.unpack("<I", raw)[0])
        if stream.read(4) != b"PE\0\0":
            return "unknown"
        machine = struct.unpack("<H", stream.read(2))[0]
    return {0x14C: "x86", 0x8664: "x64", 0xAA64: "arm64"}.get(machine, "unknown")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class PixelStats:
    mean_r: float
    mean_g: float
    mean_b: float
    mean_a: float
    channel_spread: float
    luma_stddev: float
    alpha_coverage: float
    samples: int
    edge_density: float = 0.0
    skin_coverage: float = 0.0


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


@dataclass(frozen=True)
class PngImage:
    width: int
    height: int
    rgba: bytearray


@dataclass(frozen=True)
class AppearanceCorrection:
    reference_luma: float
    enhanced_luma_before: float
    enhanced_luma_after: float
    contrast_gain: float


def _decode_png_rgba(path: Path) -> PngImage:
    """Decode the non-interlaced 8-bit PNGs emitted by texconv/Real-ESRGAN."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise UnsupportedTexture("texconv nao produziu PNG valido")
    offset = 8
    compressed = bytearray()
    width = height = bit_depth = color_type = interlace = None
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        offset += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            break
    if not width or not height or bit_depth != 8 or interlace != 0:
        raise UnsupportedTexture("PNG interlacado ou com bit depth nao suportado")
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise UnsupportedTexture(f"PNG color type {color_type} nao suportado")
    raw = zlib.decompress(bytes(compressed))
    stride = width * channels
    expected = height * (stride + 1)
    if len(raw) != expected:
        raise UnsupportedTexture("dados PNG truncados")

    rows: list[bytearray] = []
    pos = 0
    prior = bytearray(stride)
    for _ in range(height):
        filter_type = raw[pos]
        pos += 1
        scan = bytearray(raw[pos:pos + stride])
        pos += stride
        for i in range(stride):
            left = scan[i - channels] if i >= channels else 0
            up = prior[i]
            upper_left = prior[i - channels] if i >= channels else 0
            if filter_type == 1:
                scan[i] = (scan[i] + left) & 0xFF
            elif filter_type == 2:
                scan[i] = (scan[i] + up) & 0xFF
            elif filter_type == 3:
                scan[i] = (scan[i] + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                scan[i] = (scan[i] + _paeth(left, up, upper_left)) & 0xFF
            elif filter_type != 0:
                raise UnsupportedTexture(f"filtro PNG {filter_type} desconhecido")
        rows.append(scan)
        prior = scan

    rgba = bytearray(width * height * 4)
    for y, row in enumerate(rows):
        destination = y * width * 4
        for x in range(width):
            source = x * channels
            if color_type == 0:
                r = g = b = row[source]
                a = 255
            elif color_type == 2:
                r, g, b = row[source:source + 3]
                a = 255
            elif color_type == 4:
                r = g = b = row[source]
                a = row[source + 1]
            else:
                r, g, b, a = row[source:source + 4]
            rgba[destination:destination + 4] = bytes((r, g, b, a))
            destination += 4
    return PngImage(width, height, rgba)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return (struct.pack(">I", len(payload)) + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))


def _write_png_rgba(path: Path, image: PngImage) -> None:
    stride = image.width * 4
    raw = bytearray((stride + 1) * image.height)
    destination = 0
    for y in range(image.height):
        raw[destination] = 0
        destination += 1
        source = y * stride
        raw[destination:destination + stride] = image.rgba[source:source + stride]
        destination += stride
    ihdr = struct.pack(">IIBBBBB", image.width, image.height, 8, 6, 0, 0, 0)
    encoded = (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr)
               + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
               + _png_chunk(b"IEND", b""))
    path.write_bytes(encoded)


def _pixel_stats(image: PngImage, max_samples: int = 4096) -> PixelStats:
    total_pixels = image.width * image.height
    step = max(1, total_pixels // max_samples)
    sampled_indices = list(range(0, total_pixels, step))[:max_samples]
    samples = [tuple(image.rgba[index * 4:index * 4 + 4]) for index in sampled_indices]
    alphas = [pixel[3] for pixel in samples]
    # Transparent atlas backgrounds frequently contain arbitrary black RGB. Excluding those pixels
    # prevents a font, face or foliage atlas from pulling the measured exposure toward black.
    visible = [pixel for pixel in samples if pixel[3] >= 16] or samples
    rs = [pixel[0] for pixel in visible]
    gs = [pixel[1] for pixel in visible]
    bs = [pixel[2] for pixel in visible]
    n = len(visible)
    means = [sum(values) / n for values in (rs, gs, bs)]
    mean_a = sum(alphas) / len(alphas)
    spread = sum(abs(r - g) + abs(g - b) for r, g, b in zip(rs, gs, bs)) / n
    lumas = [(54 * r + 183 * g + 19 * b) / 256 for r, g, b in zip(rs, gs, bs)]
    mean_luma = sum(lumas) / n
    variance = sum((value - mean_luma) ** 2 for value in lumas) / n
    alpha_coverage = sum(1 for alpha in alphas if alpha < 250) / len(alphas)

    edge_hits = 0
    edge_pairs = 0
    pixels = image.rgba
    for linear in sampled_indices:
        base = linear * 4
        r, g, b, alpha = pixels[base:base + 4]
        if alpha < 16:
            continue
        luma = (54 * r + 183 * g + 19 * b) / 256
        x, y = linear % image.width, linear // image.width
        for neighbor in ((linear + 1) if x + 1 < image.width else -1,
                         (linear + image.width) if y + 1 < image.height else -1):
            if neighbor < 0:
                continue
            other = neighbor * 4
            nr, ng, nb, na = pixels[other:other + 4]
            if na < 16:
                continue
            other_luma = (54 * nr + 183 * ng + 19 * nb) / 256
            edge_pairs += 1
            edge_hits += abs(luma - other_luma) >= 48

    skin_pixels = sum(
        1 for r, g, b in zip(rs, gs, bs)
        if r > 60 and g > 30 and b > 15 and r > g + 10 and r > b + 15
        and max(r, g, b) - min(r, g, b) > 20
    )
    return PixelStats(
        means[0], means[1], means[2], mean_a, spread, variance ** 0.5,
        alpha_coverage, n, edge_hits / edge_pairs if edge_pairs else 0.0,
        skin_pixels / n,
    )


def read_png_stats(path: Path, max_samples: int = 4096) -> PixelStats:
    return _pixel_stats(_decode_png_rgba(path), max_samples)


def _rgb_histograms(image: PngImage, max_samples: int = 262144) -> tuple[list[list[int]], int]:
    histograms = [[0] * 256 for _ in range(3)]
    total_pixels = image.width * image.height
    step = max(1, total_pixels // max_samples)
    count = 0
    for linear in range(0, total_pixels, step):
        base = linear * 4
        red, green, blue, alpha = image.rgba[base:base + 4]
        if alpha < 16:
            continue
        histograms[0][red] += 1
        histograms[1][green] += 1
        histograms[2][blue] += 1
        count += 1
        if count >= max_samples:
            break
    if count == 0:
        # A fully transparent texture is not normally processed, but retain deterministic behavior.
        for linear in range(0, total_pixels, step):
            base = linear * 4
            for channel in range(3):
                histograms[channel][image.rgba[base + channel]] += 1
            count += 1
            if count >= max_samples:
                break
    return histograms, count


def _histogram_mapping(source: list[int], target: list[int]) -> bytes:
    source_total = sum(source)
    target_total = sum(target)
    if source_total == 0 or target_total == 0:
        return bytes(range(256))
    target_cdf: list[float] = []
    cumulative = 0
    for count in target:
        cumulative += count
        target_cdf.append(cumulative / target_total)

    mapping = bytearray(256)
    cumulative = 0
    for value, count in enumerate(source):
        before = cumulative
        cumulative += count
        quantile = (before + count * 0.5) / source_total if count else cumulative / source_total
        mapped = bisect.bisect_left(target_cdf, quantile)
        mapping[value] = min(255, mapped)
    return bytes(mapping)


def preserve_png_appearance(reference_path: Path, enhanced_path: Path) -> AppearanceCorrection:
    """Match neural output to the original texture without discarding reconstructed detail.

    ESRGAN models can shift exposure, contrast or white balance. In a game that changes lighting
    for an entire material and can look like a global brightness filter. Per-channel histogram
    matching preserves the authored tonal distribution while keeping the new high-frequency detail.
    """
    reference = _decode_png_rgba(reference_path)
    enhanced = _decode_png_rgba(enhanced_path)
    before = _pixel_stats(enhanced)
    target = _pixel_stats(reference)

    contrast_gain = (target.luma_stddev / before.luma_stddev
                     if before.luma_stddev > 0.5 else 1.0)
    target_histograms, _ = _rgb_histograms(reference)
    source_histograms, _ = _rgb_histograms(enhanced)
    tables = [
        _histogram_mapping(source_histograms[channel], target_histograms[channel])
        for channel in range(3)
    ]

    pixels = enhanced.rgba
    red, green, blue = tables
    for index in range(0, len(pixels), 4):
        # Preserve invisible RGB to avoid coloured fringes when alpha is filtered by the game.
        if pixels[index + 3] >= 16:
            pixels[index] = red[pixels[index]]
            pixels[index + 1] = green[pixels[index + 1]]
            pixels[index + 2] = blue[pixels[index + 2]]

    temporary = enhanced_path.with_name(enhanced_path.stem + ".appearance.tmp.png")
    _write_png_rgba(temporary, enhanced)
    os.replace(temporary, enhanced_path)
    after = _pixel_stats(enhanced)
    reference_luma = (54 * target.mean_r + 183 * target.mean_g + 19 * target.mean_b) / 256
    before_luma = (54 * before.mean_r + 183 * before.mean_g + 19 * before.mean_b) / 256
    after_luma = (54 * after.mean_r + 183 * after.mean_g + 19 * after.mean_b) / 256
    return AppearanceCorrection(reference_luma, before_luma, after_luma, contrast_gain)


@dataclass(frozen=True)
class Classification:
    kind: str
    confidence: float
    reason: str


def classify_texture(info: DdsInfo, stats: PixelStats) -> Classification:
    # Conservative adaptation of texup's MIT-licensed color-statistics classifier.
    if info.format_name.startswith("BC5"):
        return Classification("normal", 1.0, "BC5 e normalmente usado para normal maps")
    normal_score = 0.0
    if stats.mean_b > 180 and 85 < stats.mean_r < 175 and 85 < stats.mean_g < 175:
        normal_score += 0.65
    if stats.mean_b > stats.mean_r + 35 and stats.mean_b > stats.mean_g + 35:
        normal_score += 0.25
    if abs(stats.mean_r - stats.mean_g) < 25:
        normal_score += 0.10
    if normal_score >= 0.65:
        return Classification("normal", min(1.0, normal_score), "perfil de cor azul de normal map")
    if stats.channel_spread < 8:
        return Classification("material", 0.75, "canais quase monocromaticos; pode ser mascara/material")
    if stats.luma_stddev < 3:
        return Classification("flat", 0.85, "textura quase uniforme")
    return Classification("diffuse", 0.55, "textura colorida sem sinais fortes de normal/mask")


def sensitive_texture_reason(info: DdsInfo, stats: PixelStats) -> str | None:
    """Conservatively protect assets where generative detail is more harmful than blur."""
    if stats.alpha_coverage >= 0.03:
        return "transparencia relevante; possivel texto, interface, cabelo ou folhagem"
    ratio = max(info.width, info.height) / min(info.width, info.height)
    if (max(info.width, info.height) <= 1024 and ratio <= 2.0
            and stats.skin_coverage >= 0.45 and stats.edge_density <= 0.24
            and stats.channel_spread <= 110):
        return "padrao de pele/rosto detectado; preservado sem IA generativa"
    return None


@dataclass
class AppConfig:
    game_exe: str = ""
    profile: str = "DirectX 9 - Resident Evil 4 (2005)"
    scale: int = 2
    minimum_output_resolution: int = 1024
    anti_stretch_boost: bool = True
    preserve_appearance: bool = True
    protect_sensitive_textures: bool = True
    defer_new_replacements: bool = True
    vram_budget_mb: int = 2048
    safe_mode: bool = True
    min_dimension: int = 64
    max_dimension: int = 2048
    max_output_dimension: int = 4096
    poll_seconds: float = 1.0
    stable_seconds: float = 1.0
    tile_size: int = 256
    model_name: str = DEFAULT_QUALITY_MODEL
    skip_alpha_heavy: bool = False
    verbose_tools: bool = False

    @property
    def game_dir(self) -> Path:
        return Path(self.game_exe).expanduser().resolve().parent if self.game_exe else Path()

    @property
    def dump_dir(self) -> Path:
        return self.game_dir / "TT" / "dump"

    @property
    def inject_dir(self) -> Path:
        return self.game_dir / "TT" / "inject"

    @property
    def pending_dir(self) -> Path:
        return self.game_dir / "TT" / "pending"

    @property
    def ignore_dir(self) -> Path:
        return self.game_dir / "TT" / "ignore"

    @property
    def runtime_disable_path(self) -> Path:
        return self.game_dir / "TT" / RUNTIME_DISABLE_FILENAME

    def processing_signature(self) -> str:
        relevant = {
            "version": APP_VERSION, "scale": self.scale,
            "minimum_output_resolution": self.minimum_output_resolution,
            "anti_stretch": self.anti_stretch_boost,
            "preserve_appearance": self.preserve_appearance,
            "protect_sensitive": self.protect_sensitive_textures,
            "safe": self.safe_mode,
            "min": self.min_dimension, "max": self.max_dimension,
            "max_out": self.max_output_dimension, "tile": self.tile_size,
            "model": self.model_name, "skip_alpha": self.skip_alpha_heavy,
        }
        return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()[:16]


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        return AppConfig()
    values = json.loads(path.read_text(encoding="utf-8"))
    # The model was not user-selectable in 0.3, so migrate that default automatically.
    if values.get("model_name") == FALLBACK_FLEXIBLE_MODEL:
        values["model_name"] = DEFAULT_QUALITY_MODEL
    allowed = AppConfig.__dataclass_fields__.keys()
    return AppConfig(**{key: value for key, value in values.items() if key in allowed})


def save_config(path: Path, config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(asdict(config), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def runtime_injection_enabled(game_exe: Path) -> bool:
    return not (game_exe.resolve().parent / "TT" / RUNTIME_DISABLE_FILENAME).exists()


def set_runtime_injection(game_exe: Path, enabled: bool) -> str:
    """Toggle the native D3D8 replacements through a file the hook polls live."""
    marker = game_exe.resolve().parent / "TT" / RUNTIME_DISABLE_FILENAME
    marker.parent.mkdir(parents=True, exist_ok=True)
    if enabled:
        marker.unlink(missing_ok=True)
        return "texturas aprimoradas ativadas; a troca ocorre nos proximos binds"
    temporary = marker.with_suffix(".tmp")
    temporary.write_text("TextureFlow runtime disabled\n", encoding="ascii")
    os.replace(temporary, marker)
    return "texturas aprimoradas desativadas; originais restauradas sem reiniciar"


def is_game_running(game_exe: Path) -> bool:
    """Return whether the selected Windows executable is currently running."""
    if os.name != "nt" or not game_exe:
        return False
    name = game_exe.name
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, errors="replace", timeout=5,
            creationflags=0x08000000,
        )
    except (OSError, subprocess.SubprocessError):
        # Conservatively assume it is running: activating pending DDS while uncertain
        # would reintroduce the exact mid-session pop-in this guard is meant to prevent.
        return True
    if completed.returncode != 0:
        return True
    return name.casefold() in completed.stdout.casefold()


@dataclass
class ProcessResult:
    source: str
    status: str
    reason: str
    classification: str = ""
    elapsed_seconds: float = 0.0
    output: str = ""


class TexturePipeline:
    def __init__(self, root: Path, config: AppConfig, log: Callable[[str], None] = print):
        self.root = root
        self.config = config
        self.log = log
        self.bin_dir = root / "bin"
        self.texconv = self.bin_dir / "texconv.exe"
        self.upscaler = self.bin_dir / "realesrgan" / "realesrgan-ncnn-vulkan.exe"
        self.models = self.bin_dir / "realesrgan" / "models"
        self.state_dir = root / "state"
        self.cache_path = self.state_dir / "cache.json"
        self.work_root = self.state_dir / "work"
        self._cache_file_observed = self.cache_path.exists()
        self.cache: dict[str, dict] = self._load_cache()
        self._cache_lock = threading.Lock()

    def _load_cache(self) -> dict[str, dict]:
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save_cache(self) -> None:
        with self._cache_lock:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            temp = self.cache_path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.cache, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.cache_path)
            self._cache_file_observed = True

    def validate(self) -> list[str]:
        missing = []
        model_names = {self.config.model_name}
        if self.config.model_name == DEFAULT_QUALITY_MODEL:
            model_names.add(FALLBACK_FLEXIBLE_MODEL)
        required = [self.texconv, self.upscaler]
        for model_name in sorted(model_names):
            required.extend((self.models / f"{model_name}.param",
                             self.models / f"{model_name}.bin"))
        for path in required:
            if not path.exists():
                missing.append(str(path))
        if not self.config.game_exe or not Path(self.config.game_exe).is_file():
            missing.append("executavel do jogo")
        return missing

    def _run(self, args: Sequence[str], cwd: Path) -> None:
        flags = 0x08000000 if os.name == "nt" and not self.config.verbose_tools else 0
        completed = subprocess.run(list(map(str, args)), cwd=str(cwd), capture_output=True,
                                   text=True, errors="replace", creationflags=flags, timeout=900)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()[-2000:]
            raise TextureFlowError(f"ferramenta retornou {completed.returncode}: {detail}")

    @staticmethod
    def _find_output(folder: Path, stem: str, suffix: str) -> Path:
        for candidate in folder.iterdir():
            if candidate.stem.lower() == stem.lower() and candidate.suffix.lower() == suffix.lower():
                return candidate
        raise TextureFlowError(f"saida {stem}{suffix} nao foi criada")

    def target_dimensions(self, info: DdsInfo) -> tuple[int, int, int]:
        """Return power-of-two enlargement dimensions and their scale factor."""
        source_long_edge = max(info.width, info.height)
        minimum = max(0, int(self.config.minimum_output_resolution))
        if minimum:
            if source_long_edge >= minimum:
                return info.width, info.height, 1
            requested_scale = (minimum + source_long_edge - 1) // source_long_edge
        else:
            requested_scale = max(1, int(self.config.scale))

        scale = 1
        while scale < requested_scale:
            scale *= 2
        # A tiny source that is stretched across a large wall or floor still looks coarse when it
        # merely reaches the selected minimum. Give these assets one extra 2x tier when the output
        # ceiling allows it. This cannot infer UV density, but it addresses the common old-game case
        # without increasing already-large textures or breaking their aspect ratio.
        if (self.config.anti_stretch_boost and source_long_edge <= 512 and scale > 1
                and source_long_edge * scale * 2 <= self.config.max_output_dimension):
            scale *= 2
        return info.width * scale, info.height * scale, scale

    @staticmethod
    def neural_pass_scales(target_scale: int) -> list[int]:
        """Compose an exact power-of-two enlargement without oversized intermediates."""
        if target_scale < 1 or target_scale & (target_scale - 1):
            raise TextureFlowError("a escala neural precisa ser uma potencia de dois")
        passes: list[int] = []
        remaining = target_scale
        while remaining > 1:
            step = 4 if remaining >= 4 else 2
            passes.append(step)
            remaining //= step
        return passes

    def neural_pass_plan(self, target_scale: int) -> list[tuple[str, int]]:
        """Choose valid model/scale pairs for the requested power-of-two enlargement.

        Nomos8kSC is a native 4x RRDB model. Passing ``-s 2`` does not turn it into a 2x model and
        can corrupt dimensions. A 2x-only remainder therefore uses the bundled flexible General v3
        model; a standalone 2x request runs Nomos at 4x and is downsampled by texconv for cleaner
        anti-aliasing.
        """
        scales = self.neural_pass_scales(target_scale)
        if self.config.model_name != DEFAULT_QUALITY_MODEL:
            return [(self.config.model_name, scale) for scale in scales]
        if target_scale == 2:
            return [(DEFAULT_QUALITY_MODEL, 4)]
        return [
            (DEFAULT_QUALITY_MODEL if scale == 4 else FALLBACK_FLEXIBLE_MODEL, scale)
            for scale in scales
        ]

    def _existing_output(self, name: str) -> Path | None:
        for candidate in (self.config.pending_dir / name, self.config.inject_dir / name):
            if candidate.is_file():
                return candidate
        return None

    def has_output(self, name: str) -> bool:
        return self._existing_output(name) is not None

    def invalidate_cache_if_removed(self) -> bool:
        """Notice a cache.json deletion performed while the monitor is still running."""
        with self._cache_lock:
            exists = self.cache_path.exists()
            if exists:
                self._cache_file_observed = True
                return False
            if not self._cache_file_observed:
                return False
            self.cache.clear()
            self._cache_file_observed = False
            return True

    def activate_pending_replacements(self) -> int:
        """Atomically promote work from the prior session before the game starts."""
        pending = self.config.pending_dir
        if not pending.is_dir():
            return 0
        self.config.inject_dir.mkdir(parents=True, exist_ok=True)
        activated = 0
        for source in sorted(pending.glob("*.dds")):
            if not source.is_file():
                continue
            os.replace(source, self.config.inject_dir / source.name)
            activated += 1
        return activated

    def pending_count(self) -> int:
        if not self.config.pending_dir.is_dir():
            return 0
        return sum(1 for path in self.config.pending_dir.glob("*.dds") if path.is_file())

    def _eligibility(self, info: DdsInfo) -> str | None:
        cfg = self.config
        if info.is_cubemap or info.is_volume:
            return "cubemap/volume ignorado por seguranca"
        if min(info.width, info.height) < cfg.min_dimension:
            return f"menor que {cfg.min_dimension}px"
        if max(info.width, info.height) > cfg.max_dimension:
            return f"maior que {cfg.max_dimension}px"
        ratio = max(info.width, info.height) / min(info.width, info.height)
        if cfg.safe_mode and ratio > 4:
            return "proporcao extrema; possivel UI/atlas"
        if cfg.safe_mode and (info.width & (info.width - 1) or info.height & (info.height - 1)):
            return "dimensao nao power-of-two em modo seguro"
        target_w, target_h, target_scale = self.target_dimensions(info)
        if target_scale == 1:
            if cfg.minimum_output_resolution:
                return f"ja possui pelo menos {cfg.minimum_output_resolution}px no lado maior"
            return "escala configurada nao aumenta a textura"
        if max(target_w, target_h) > cfg.max_output_dimension:
            return f"saida passaria de {cfg.max_output_dimension}px"
        return None

    def process_one(self, source: Path) -> ProcessResult:
        started = time.monotonic()
        source = source.resolve()
        try:
            if (self.config.ignore_dir / source.name).exists():
                return ProcessResult(
                    str(source), "skipped", "bloqueada manualmente em TT\\ignore",
                    "ignored", time.monotonic() - started,
                )
            info = parse_dds(source)
            reason = self._eligibility(info)
            if reason:
                return ProcessResult(str(source), "skipped", reason,
                                     elapsed_seconds=time.monotonic() - started)
            fingerprint = sha256_file(source)
            cache_key = str(source).lower()
            cached = self.cache.get(cache_key)
            output_dir = self.config.pending_dir if self.config.defer_new_replacements else self.config.inject_dir
            output = output_dir / source.name
            existing_output = self._existing_output(source.name)
            if cached and cached.get("sha256") == fingerprint and cached.get("signature") == self.config.processing_signature() and existing_output:
                return ProcessResult(str(source), "cached", "ja processada com a configuracao atual",
                                     cached.get("classification", ""), time.monotonic() - started,
                                     str(existing_output))

            target_w, target_h, target_scale = self.target_dimensions(info)

            self.work_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="textureflow-", dir=self.work_root) as work_text:
                work = Path(work_text)
                decoded_dir = work / "decoded"; decoded_dir.mkdir()
                ai_dir = work / "ai"; ai_dir.mkdir()
                encoded_dir = work / "encoded"; encoded_dir.mkdir()

                self._run([self.texconv, "-ft", "png", "-y", "-o", decoded_dir, source], work)
                png = self._find_output(decoded_dir, source.stem, ".png")
                stats = read_png_stats(png)
                classification = classify_texture(info, stats)
                if self.config.protect_sensitive_textures:
                    sensitive_reason = sensitive_texture_reason(info, stats)
                    if sensitive_reason:
                        return ProcessResult(str(source), "skipped", sensitive_reason,
                                             "sensitive", time.monotonic() - started)
                if classification.kind != "diffuse":
                    return ProcessResult(str(source), "skipped", classification.reason,
                                         classification.kind, time.monotonic() - started)
                if self.config.skip_alpha_heavy and stats.alpha_coverage > 0.05:
                    return ProcessResult(str(source), "skipped", "alpha relevante; opcao de seguranca ativa",
                                         "alpha", time.monotonic() - started)

                # Every pass uses a scale supported by its model. Nomos supplies the high-quality
                # 4x reconstruction; General v3 handles an exact 2x remainder when needed.
                ai_png = png
                for pass_index, (pass_model, pass_scale) in enumerate(
                        self.neural_pass_plan(target_scale), 1):
                    pass_dir = ai_dir / f"pass-{pass_index}"
                    pass_dir.mkdir()
                    pass_output = pass_dir / f"{source.stem}.png"
                    self._run([
                        self.upscaler, "-i", ai_png, "-o", pass_output,
                        "-m", self.models, "-n", pass_model,
                        "-s", str(pass_scale), "-t", str(self.config.tile_size),
                        "-j", "1:1:1", "-f", "png"
                    ], work)
                    if not pass_output.exists():
                        raise TextureFlowError(f"upscaler nao criou a saida do passe {pass_index}")
                    ai_png = pass_output

                correction = None
                if self.config.preserve_appearance:
                    correction = preserve_png_appearance(png, ai_png)
                    shift = correction.enhanced_luma_before - correction.reference_luma
                    if abs(shift) >= 1.0:
                        self.log(
                            f"Aparencia preservada: desvio de luminancia {shift:+.1f} corrigido"
                        )

                self._run([
                    self.texconv, "-f", info.texconv_format, "-m", "0",
                    "-w", str(target_w), "-h", str(target_h), "-y",
                    "-o", encoded_dir, ai_png
                ], work)
                encoded = self._find_output(encoded_dir, source.stem, ".dds")
                encoded_info = parse_dds(encoded)
                if (encoded_info.width, encoded_info.height) != (target_w, target_h):
                    raise TextureFlowError("dimensoes da saida DDS nao conferem")
                if encoded_info.mip_count <= 1 and max(target_w, target_h) > 1:
                    raise TextureFlowError("saida DDS foi criada sem mipmaps")

                output_dir.mkdir(parents=True, exist_ok=True)
                temp_output = output.with_suffix(".dds.part")
                shutil.copy2(encoded, temp_output)
                os.replace(temp_output, output)

            self.cache[cache_key] = {
                "sha256": fingerprint,
                "signature": self.config.processing_signature(),
                "classification": classification.kind,
                "output": str(output),
                "processed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source": {"width": info.width, "height": info.height, "format": info.format_name},
                "output_size": {"width": target_w, "height": target_h},
                "appearance_preserved": self.config.preserve_appearance,
            }
            self._save_cache()
            destination_note = ("; preparada para a proxima abertura" if self.config.defer_new_replacements
                                else "; aplicada ao vivo")
            return ProcessResult(str(source), "processed",
                                 f"{info.width}x{info.height} -> {target_w}x{target_h}{destination_note}",
                                 classification.kind, time.monotonic() - started, str(output))
        except UnsupportedTexture as exc:
            return ProcessResult(str(source), "skipped", str(exc), elapsed_seconds=time.monotonic() - started)
        except Exception as exc:
            return ProcessResult(str(source), "error", str(exc), elapsed_seconds=time.monotonic() - started)

    def scan(self) -> Iterable[Path]:
        if not self.config.dump_dir.exists():
            return []
        return sorted(self.config.dump_dir.glob("*.dds"), key=lambda p: p.stat().st_mtime_ns)


def install_texture_hook(root: Path, game_exe: Path, profile: str, *,
                         vram_budget_mb: int = 2048) -> list[str]:
    game_exe = game_exe.resolve()
    if not game_exe.is_file():
        raise TextureFlowError("selecione o executavel real do jogo")
    arch = read_pe_arch(game_exe)
    if arch not in {"x86", "x64"}:
        raise TextureFlowError(f"arquitetura do executavel nao suportada: {arch}")
    game_dir = game_exe.parent
    is_postal = "Postal 2" in profile
    if is_postal and arch != "x86":
        raise TextureFlowError("o capturador nativo do Postal 2 requer o executavel x86 (32 bits)")

    manifest_path = game_dir / "TextureFlow.install.json"
    existing_manifest: dict = {}
    if manifest_path.is_file():
        try:
            loaded_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded_manifest, dict):
                existing_manifest = loaded_manifest
        except (OSError, json.JSONDecodeError):
            pass

    plugin_source = (root / "bin" / "hooks" / "TextureFlowD3D8-x86.asi" if is_postal
                     else root / "bin" / "hooks" / f"TextureToolkit-{arch}.asi")
    loader_source = root / "bin" / "hooks" / f"UltimateASILoader-{arch}.dll"
    if not plugin_source.exists() or not loader_source.exists():
        raise TextureFlowError("binarios de hook nao encontrados no pacote")

    if "RE4" in profile:
        loader_candidates = ("dinput8.dll", "d3d9.dll")
    elif is_postal:
        # This proxy is guaranteed to load when the requested D3D8 renderer is active.
        # dinput8 remains a non-destructive fallback for installations with a wrapper.
        loader_candidates = ("d3d8.dll", "dinput8.dll")
    else:
        loader_candidates = ("dinput8.dll", "d3d9.dll", "dxgi.dll")
    matching_loader = next((game_dir / name for name in loader_candidates
                            if (game_dir / name).exists()
                            and sha256_file(game_dir / name) == sha256_file(loader_source)), None)
    loader_name = next((name for name in loader_candidates if not (game_dir / name).exists()), None)
    if matching_loader is None and loader_name is None:
        occupied = ", ".join(name for name in loader_candidates if (game_dir / name).exists())
        raise TextureFlowError(
            f"nao ha nome de proxy livre ({occupied}). Os arquivos existentes foram preservados; "
            "remova wrappers conflitantes ou use um ASI Loader que ja carregue o plugin"
        )

    actions: list[str] = []
    plugin_target = game_dir / ("TextureFlow.D3D8.asi" if is_postal else "TextureFlow.TextureToolkit.asi")
    source_plugin_hash = sha256_file(plugin_source)
    if plugin_target.exists() and sha256_file(plugin_target) != source_plugin_hash:
        expected_old_hash = (existing_manifest.get("plugin_sha256") or
                             existing_manifest.get("toolkit_sha256"))
        if expected_old_hash and sha256_file(plugin_target) == expected_old_hash:
            shutil.copy2(plugin_source, plugin_target)
            actions.append(f"atualizado {plugin_target.name}")
        else:
            raise TextureFlowError(f"ja existe um arquivo diferente: {plugin_target.name}")
    elif not plugin_target.exists():
        shutil.copy2(plugin_source, plugin_target)
        actions.append(f"instalado {plugin_target.name}")
    else:
        actions.append(f"mantido {plugin_target.name} existente")

    installed_loader = False
    loader_target: Path | None = matching_loader
    if loader_target is not None:
        installed_loader = bool(
            existing_manifest.get("installed_loader")
            and existing_manifest.get("loader_filename") == loader_target.name
            and existing_manifest.get("loader_sha256") == sha256_file(loader_target)
        )
        actions.append(f"ASI Loader ja estava instalado como {loader_target.name}")
    if loader_target is None and loader_name is not None:
        loader_target = game_dir / loader_name
        shutil.copy2(loader_source, loader_target)
        installed_loader = True
        actions.append(f"instalado {loader_name} (Ultimate ASI Loader)")
    assert loader_target is not None

    config_path = game_dir / ("TextureFlowD3D8.ini" if is_postal else "TextureToolkit.ini")
    parser = configparser.ConfigParser()
    parser.optionxform = str
    if config_path.exists():
        parser.read(config_path, encoding="utf-8")
    section = "TextureFlowD3D8" if is_postal else "TextureToolkit"
    if not parser.has_section(section):
        parser.add_section(section)
    if is_postal:
        values = {
            "AutoDump": "1", "EnableInjection": "1", "CheckIntervalMs": "750",
            "MinDumpDimension": "64", "ToggleHotKey": "36",  # HOME, igual ao Texture Toolkit.
            "VramBudgetMB": str(max(128, min(16384, int(vram_budget_mb)))),
        }
    else:
        values = {
            "HotKey": "0x24",  # HOME; useful on compact keyboards without Insert.
            "ResourceRoot": "TT", "EnableInjection": "1", "AutoDump": "1",
            "FilterSmallTextures": "1", "ShowCurrentFrameOnly": "1",
            "AcceptSpecialKNames": "1", "ShowOSDBanner": "1", "Verbose": "0",
        }
    for key, value in values.items():
        parser.set(section, key, value)
    with config_path.open("w", encoding="utf-8") as stream:
        parser.write(stream)
    actions.append("captura automatica e injecao por hash ativadas")
    (game_dir / "TT" / "dump").mkdir(parents=True, exist_ok=True)
    (game_dir / "TT" / "inject").mkdir(parents=True, exist_ok=True)
    (game_dir / "TT" / "pending").mkdir(parents=True, exist_ok=True)
    (game_dir / "TT" / "ignore").mkdir(parents=True, exist_ok=True)

    manifest = {
        "textureflow_version": APP_VERSION, "profile": profile, "game_exe": str(game_exe),
        "architecture": arch, "installed_loader": installed_loader,
        "plugin_filename": plugin_target.name,
        "plugin_sha256": sha256_file(plugin_target),
        # Kept for backward-compatible removal of 0.1 installations.
        "toolkit_sha256": sha256_file(plugin_target) if not is_postal else "",
        "config_filename": config_path.name,
        "loader_filename": loader_target.name,
        "loader_sha256": sha256_file(loader_target),
        "vram_budget_mb": max(128, min(16384, int(vram_budget_mb))),
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    actions.append(f"perfil {profile} configurado ({arch})")
    if is_postal:
        actions.append("capturador Direct3D 8 nativo pronto; acompanhe TextureFlowD3D8.log")
        actions.append("HOME ou o botao do manager alternam originais/replacements durante o jogo")
    return actions


def remove_texture_hook(game_exe: Path) -> list[str]:
    game_exe = game_exe.resolve()
    game_dir = game_exe.parent
    manifest_path = game_dir / "TextureFlow.install.json"
    if not manifest_path.exists():
        raise TextureFlowError("manifesto de instalacao nao encontrado; nada foi removido")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TextureFlowError("manifesto de instalacao invalido; remocao cancelada") from exc

    actions: list[str] = []
    plugin_name = manifest.get("plugin_filename", "TextureFlow.TextureToolkit.asi")
    plugin = game_dir / plugin_name
    expected_plugin = manifest.get("plugin_sha256") or manifest.get("toolkit_sha256")
    if plugin.exists():
        if expected_plugin and sha256_file(plugin) == expected_plugin:
            plugin.unlink()
            actions.append(f"removido {plugin.name}")
        else:
            actions.append("plugin modificado foi preservado")

    loader = game_dir / manifest.get("loader_filename", "dinput8.dll")
    if manifest.get("installed_loader") and loader.exists():
        expected_loader = manifest.get("loader_sha256")
        if expected_loader and sha256_file(loader) == expected_loader:
            loader.unlink()
            actions.append(f"removido {loader.name} instalado pelo TextureFlow")
        else:
            actions.append(f"{loader.name} modificado foi preservado")

    manifest_path.unlink()
    config_name = manifest.get("config_filename", "TextureToolkit.ini")
    actions.append(f"TT, cache, dumps, replacements e {config_name} foram preservados")
    return actions


def inspect_texture_hook(game_exe: Path) -> tuple[bool, list[str]]:
    """Validate an installation without modifying the game directory."""
    game_exe = game_exe.resolve()
    game_dir = game_exe.parent
    findings: list[str] = []
    healthy = True
    if not game_exe.is_file():
        return False, ["executavel do jogo nao encontrado"]

    arch = read_pe_arch(game_exe)
    findings.append(f"executavel: {game_exe.name} ({arch})")
    manifest_path = game_dir / "TextureFlow.install.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, findings + ["captura ainda nao foi instalada"]
    except (OSError, json.JSONDecodeError):
        return False, findings + ["manifesto de instalacao invalido"]

    plugin = game_dir / manifest.get("plugin_filename", "TextureFlow.TextureToolkit.asi")
    expected_plugin = manifest.get("plugin_sha256") or manifest.get("toolkit_sha256")
    if not plugin.is_file():
        findings.append(f"FALTA: {plugin.name}")
        healthy = False
    elif expected_plugin and sha256_file(plugin) != expected_plugin:
        findings.append(f"ALTERADO: {plugin.name}")
        healthy = False
    else:
        findings.append(f"plugin: {plugin.name} OK")

    loader_name = manifest.get("loader_filename", "dinput8.dll")
    loader = game_dir / loader_name
    if not loader.is_file():
        findings.append(f"FALTA: {loader_name}")
        healthy = False
    elif manifest.get("loader_sha256") and sha256_file(loader) != manifest["loader_sha256"]:
        findings.append(f"ALTERADO: {loader_name}")
        healthy = False
    else:
        findings.append(f"ASI Loader: {loader_name} presente")

    for folder_name in ("dump", "inject", "pending", "ignore"):
        folder = game_dir / "TT" / folder_name
        if folder.is_dir():
            count = sum(1 for path in folder.glob("*.dds") if path.is_file())
            findings.append(f"TT\\{folder_name}: {count} DDS")
        else:
            findings.append(f"FALTA: TT\\{folder_name}")
            healthy = False

    state = "ativadas" if runtime_injection_enabled(game_exe) else "desativadas"
    findings.append(f"texturas aprimoradas: {state}")

    if "Postal 2" in manifest.get("profile", ""):
        hook_log = game_dir / "TextureFlowD3D8.log"
        if hook_log.is_file():
            try:
                tail = hook_log.read_text(encoding="utf-8", errors="replace").splitlines()[-3:]
                findings.append("log D3D8 encontrado")
                findings.extend(f"  {line}" for line in tail)
            except OSError:
                findings.append("log D3D8 existe, mas nao pode ser lido")
        else:
            findings.append("log D3D8 ainda nao existe; ele aparece quando o jogo abre")
    return healthy, findings


def wait_until_stable(path: Path, seconds: float, stop: threading.Event) -> bool:
    previous: tuple[int, int] | None = None
    stable_since = time.monotonic()
    while not stop.is_set():
        try:
            stat = path.stat()
            current = (stat.st_size, stat.st_mtime_ns)
        except FileNotFoundError:
            return False
        if current != previous:
            previous = current
            stable_since = time.monotonic()
        elif time.monotonic() - stable_since >= seconds:
            return True
        stop.wait(min(0.25, seconds))
    return False
