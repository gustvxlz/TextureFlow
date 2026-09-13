from __future__ import annotations

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from textureflow.core import (
    AppConfig, DdsInfo, PixelStats, TexturePipeline, UnsupportedTexture,
    classify_texture, inspect_texture_hook, install_texture_hook, parse_dds, read_pe_arch,
    preserve_png_appearance, read_png_stats, remove_texture_hook,
    runtime_injection_enabled, sensitive_texture_reason, set_runtime_injection,
)


def make_dds(path: Path, width: int = 256, height: int = 128, fourcc: bytes = b"DXT1", mips: int = 8,
             caps2: int = 0) -> None:
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000 | (0x20000 if mips > 1 else 0)
    pf = struct.pack("<II4sIIIII", 32, 0x4, fourcc, 0, 0, 0, 0, 0)
    caps = 0x1000 | (0x400008 if mips > 1 else 0)
    header = (
        b"DDS " + struct.pack("<IIIIII", 124, flags, height, width, max(8, width * height // 2), 0)
        + struct.pack("<I", mips) + b"\0" * 44 + pf
        + struct.pack("<IIIII", caps, caps2, 0, 0, 0)
    )
    path.write_bytes(header + b"\0" * 32)


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def make_png(path: Path, width: int, height: int, rgba: tuple[int, int, int, int],
             rgba2: tuple[int, int, int, int] | None = None) -> None:
    if rgba2 is None:
        row = bytes(rgba) * width
    else:
        row = b"".join(bytes(rgba if x % 3 else rgba2) for x in range(width))
    raw = b"".join(b"\0" + row for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr) + png_chunk(b"IDAT", zlib.compress(raw)) + png_chunk(b"IEND", b""))


def make_pe(path: Path, machine: int) -> None:
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x84, machine)
    path.write_bytes(data)


class DdsTests(unittest.TestCase):
    def test_parses_legacy_bc1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "A.dds"
            make_dds(path)
            info = parse_dds(path)
            self.assertEqual((info.width, info.height, info.mip_count), (256, 128, 8))
            self.assertEqual(info.format_name, "BC1")
            self.assertEqual(info.texconv_format, "BC1_UNORM")

    def test_rejects_unknown_fourcc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "A.dds"
            make_dds(path, fourcc=b"NOPE")
            with self.assertRaises(UnsupportedTexture):
                parse_dds(path)

    def test_marks_cubemap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "A.dds"
            make_dds(path, caps2=0x200)
            self.assertTrue(parse_dds(path).is_cubemap)


class PngAndClassifierTests(unittest.TestCase):
    def test_reads_rgba_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.png"
            make_png(path, 8, 8, (100, 120, 220, 128))
            stats = read_png_stats(path)
            self.assertEqual(stats.samples, 64)
            self.assertAlmostEqual(stats.mean_b, 220)
            self.assertAlmostEqual(stats.alpha_coverage, 1.0)

    def test_detects_normal_by_format(self) -> None:
        info = DdsInfo(256, 256, 9, "BC5", "BC5_UNORM", False, False, False, False, 128)
        stats = PixelStats(128, 128, 255, 255, 0, 20, 0, 100)
        self.assertEqual(classify_texture(info, stats).kind, "normal")

    def test_detects_material_mask(self) -> None:
        info = DdsInfo(256, 256, 9, "BC1", "BC1_UNORM", False, False, False, False, 128)
        stats = PixelStats(90, 90, 90, 255, 0, 20, 0, 100)
        self.assertEqual(classify_texture(info, stats).kind, "material")

    def test_allows_colored_diffuse(self) -> None:
        info = DdsInfo(256, 256, 9, "BC3", "BC3_UNORM", True, False, False, False, 128)
        stats = PixelStats(140, 80, 55, 255, 70, 28, 0, 100)
        self.assertEqual(classify_texture(info, stats).kind, "diffuse")

    def test_appearance_correction_removes_neural_brightness_shift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / "reference.png"
            enhanced = Path(tmp) / "enhanced.png"
            make_png(reference, 8, 8, (70, 90, 110, 255), (40, 60, 80, 255))
            make_png(enhanced, 32, 32, (145, 165, 185, 210), (115, 135, 155, 210))
            before = read_png_stats(enhanced)
            correction = preserve_png_appearance(reference, enhanced)
            target = read_png_stats(reference)
            after = read_png_stats(enhanced)
            self.assertGreater(before.mean_r - target.mean_r, 60)
            self.assertLess(abs(after.mean_r - target.mean_r), 1.5)
            self.assertLess(abs(after.mean_g - target.mean_g), 1.5)
            self.assertLess(abs(correction.enhanced_luma_after - correction.reference_luma), 1.5)
            self.assertAlmostEqual(after.mean_a, 210)

    def test_appearance_correction_matches_full_tonal_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / "reference.png"
            enhanced = Path(tmp) / "enhanced.png"
            make_png(reference, 12, 8, (220, 210, 200, 255), (20, 30, 40, 255))
            make_png(enhanced, 48, 32, (160, 150, 140, 255), (80, 90, 100, 255))
            target = read_png_stats(reference)
            before = read_png_stats(enhanced)
            preserve_png_appearance(reference, enhanced)
            after = read_png_stats(enhanced)
            self.assertGreater(target.luma_stddev - before.luma_stddev, 40)
            self.assertLess(abs(after.luma_stddev - target.luma_stddev), 2.0)

    def test_appearance_ignores_transparent_black_background(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / "reference.png"
            enhanced = Path(tmp) / "enhanced.png"
            make_png(reference, 12, 8, (100, 110, 120, 255), (0, 0, 0, 0))
            make_png(enhanced, 48, 32, (180, 190, 200, 255), (0, 0, 0, 0))
            preserve_png_appearance(reference, enhanced)
            after = read_png_stats(enhanced)
            self.assertLess(abs(after.mean_r - 100), 1.0)
            self.assertGreater(after.alpha_coverage, 0.2)

    def test_sensitive_classifier_protects_alpha_and_face_like_textures(self) -> None:
        info = DdsInfo(256, 256, 1, "BC3", "BC3_UNORM", True, False, False, False, 128)
        alpha_stats = PixelStats(120, 100, 80, 220, 35, 30, 0.2, 100)
        self.assertIn("transparencia", sensitive_texture_reason(info, alpha_stats) or "")
        face_stats = PixelStats(
            165, 115, 82, 255, 55, 28, 0.0, 100,
            edge_density=0.10, skin_coverage=0.62,
        )
        self.assertIn("rosto", sensitive_texture_reason(info, face_stats) or "")


class PipelinePolicyTests(unittest.TestCase):
    def test_safe_mode_rejects_npot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = TexturePipeline(Path(tmp), AppConfig(game_exe=str(Path(tmp) / "game.exe")), lambda _: None)
            info = DdsInfo(300, 256, 1, "BC1", "BC1_UNORM", False, False, False, False, 128)
            self.assertIn("power-of-two", pipeline._eligibility(info) or "")

    def test_signature_changes_with_scale(self) -> None:
        a = AppConfig(scale=2)
        b = AppConfig(scale=4)
        self.assertNotEqual(a.processing_signature(), b.processing_signature())

    def test_minimum_resolution_preserves_aspect_ratio(self) -> None:
        pipeline = TexturePipeline(Path("."), AppConfig(
            minimum_output_resolution=1024, anti_stretch_boost=False), lambda _: None)
        info = DdsInfo(256, 128, 1, "BC1", "BC1_UNORM", False, False, False, False, 128)
        self.assertEqual(pipeline.target_dimensions(info), (1024, 512, 4))

    def test_minimum_resolution_can_require_multiple_neural_passes(self) -> None:
        pipeline = TexturePipeline(Path("."), AppConfig(
            minimum_output_resolution=1024, anti_stretch_boost=False), lambda _: None)
        info = DdsInfo(128, 128, 1, "BC1", "BC1_UNORM", False, False, False, False, 128)
        self.assertEqual(pipeline.target_dimensions(info), (1024, 1024, 8))
        self.assertEqual(pipeline.neural_pass_scales(8), [4, 2])
        self.assertEqual(pipeline.neural_pass_scales(32), [4, 4, 2])

    def test_anti_stretch_adds_one_resolution_tier(self) -> None:
        pipeline = TexturePipeline(Path("."), AppConfig(minimum_output_resolution=1024), lambda _: None)
        info = DdsInfo(256, 128, 1, "BC1", "BC1_UNORM", False, False, False, False, 128)
        self.assertEqual(pipeline.target_dimensions(info), (2048, 1024, 8))

    def test_native_x4_model_never_receives_invalid_2x_scale(self) -> None:
        pipeline = TexturePipeline(Path("."), AppConfig(minimum_output_resolution=1024), lambda _: None)
        self.assertEqual(
            pipeline.neural_pass_plan(8),
            [("4xNomos8kSC", 4), ("RealESRGAN_General_x4_v3", 2)],
        )
        self.assertEqual(pipeline.neural_pass_plan(2), [("4xNomos8kSC", 4)])

    def test_texture_already_at_minimum_is_skipped(self) -> None:
        pipeline = TexturePipeline(Path("."), AppConfig(minimum_output_resolution=1024), lambda _: None)
        info = DdsInfo(2048, 1024, 1, "BC1", "BC1_UNORM", False, False, False, False, 128)
        self.assertIn("ja possui", pipeline._eligibility(info) or "")

    def test_signature_changes_with_minimum_resolution(self) -> None:
        self.assertNotEqual(
            AppConfig(minimum_output_resolution=1024).processing_signature(),
            AppConfig(minimum_output_resolution=2048).processing_signature(),
        )

    def test_pending_replacements_promote_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "game"
            pending = game / "TT" / "pending"
            inject = game / "TT" / "inject"
            pending.mkdir(parents=True)
            inject.mkdir(parents=True)
            (pending / "A.dds").write_bytes(b"new")
            (inject / "A.dds").write_bytes(b"old")
            pipeline = TexturePipeline(Path(tmp), AppConfig(game_exe=str(game / "game.exe")), lambda _: None)
            self.assertEqual(pipeline.pending_count(), 1)
            self.assertEqual(pipeline.activate_pending_replacements(), 1)
            self.assertEqual((inject / "A.dds").read_bytes(), b"new")
            self.assertEqual(pipeline.pending_count(), 0)

    def test_runtime_toggle_uses_reversible_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            game_exe = Path(tmp) / "Postal2.exe"
            game_exe.touch()
            self.assertTrue(runtime_injection_enabled(game_exe))
            set_runtime_injection(game_exe, False)
            self.assertFalse(runtime_injection_enabled(game_exe))
            set_runtime_injection(game_exe, True)
            self.assertTrue(runtime_injection_enabled(game_exe))

    def test_removed_cache_and_replacement_are_detected_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            game = Path(tmp) / "game"
            cache_path = root / "state" / "cache.json"
            cache_path.parent.mkdir(parents=True)
            cache_path.write_text('{"texture": {"status": "processed"}}', encoding="utf-8")
            replacement = game / "TT" / "inject" / "A.dds"
            replacement.parent.mkdir(parents=True)
            replacement.write_bytes(b"replacement")
            pipeline = TexturePipeline(root, AppConfig(game_exe=str(game / "game.exe")), lambda _: None)
            self.assertTrue(pipeline.has_output("A.dds"))
            replacement.unlink()
            self.assertFalse(pipeline.has_output("A.dds"))
            cache_path.unlink()
            self.assertTrue(pipeline.invalidate_cache_if_removed())
            self.assertEqual(pipeline.cache, {})

    def test_removed_empty_cache_is_detected_live_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            game = Path(tmp) / "game"
            cache_path = root / "state" / "cache.json"
            cache_path.parent.mkdir(parents=True)
            cache_path.write_text("{}", encoding="utf-8")
            pipeline = TexturePipeline(
                root, AppConfig(game_exe=str(game / "game.exe")), lambda _: None,
            )
            cache_path.unlink()
            self.assertTrue(pipeline.invalidate_cache_if_removed())
            self.assertFalse(pipeline.invalidate_cache_if_removed())

    def test_manual_ignore_marker_prevents_regeneration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            game = Path(tmp) / "game"
            source = game / "TT" / "dump" / "BADFACE.dds"
            ignored = game / "TT" / "ignore" / source.name
            source.parent.mkdir(parents=True)
            ignored.parent.mkdir(parents=True)
            source.write_bytes(b"not parsed because it is ignored")
            ignored.touch()
            pipeline = TexturePipeline(root, AppConfig(game_exe=str(game / "game.exe")), lambda _: None)
            result = pipeline.process_one(source)
            self.assertEqual(result.status, "skipped")
            self.assertEqual(result.classification, "ignored")

    def test_end_to_end_pipeline_and_cache_with_fake_tools(self) -> None:
        class FakePipeline(TexturePipeline):
            def _run(self, args, cwd):  # type: ignore[override]
                args = [str(value) for value in args]
                if "-ft" in args:
                    output_dir = Path(args[args.index("-o") + 1])
                    output_dir.mkdir(parents=True, exist_ok=True)
                    source = Path(args[-1])
                    make_png(output_dir / f"{source.stem}.png", 256, 128,
                             (140, 80, 45, 255), (70, 130, 170, 255))
                elif Path(args[0]).name == "realesrgan-ncnn-vulkan.exe":
                    output = Path(args[args.index("-o") + 1])
                    output.parent.mkdir(parents=True, exist_ok=True)
                    make_png(output, 1024, 512, (145, 85, 50, 255))
                else:
                    output_dir = Path(args[args.index("-o") + 1])
                    output_dir.mkdir(parents=True, exist_ok=True)
                    source = Path(args[-1])
                    width = int(args[args.index("-w") + 1])
                    height = int(args[args.index("-h") + 1])
                    make_dds(output_dir / f"{source.stem}.dds", width, height, b"DXT1", 10)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            game = Path(tmp) / "game"
            source = game / "TT" / "dump" / "ABCDEF0123456789.dds"
            source.parent.mkdir(parents=True)
            make_dds(source)
            config = AppConfig(
                game_exe=str(game / "bio4.exe"), minimum_output_resolution=1024,
                anti_stretch_boost=False, protect_sensitive_textures=False,
            )
            pipeline = FakePipeline(root, config, lambda _: None)
            first = pipeline.process_one(source)
            self.assertEqual(first.status, "processed", first.reason)
            self.assertTrue((game / "TT" / "pending" / source.name).exists())
            self.assertFalse((game / "TT" / "inject" / source.name).exists())
            second = pipeline.process_one(source)
            self.assertEqual(second.status, "cached")


class InstallerTests(unittest.TestCase):
    def test_pe_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "game.exe"
            make_pe(path, 0x14C)
            self.assertEqual(read_pe_arch(path), "x86")

    def test_installer_uses_second_proxy_without_overwriting_existing_loader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            game = Path(tmp) / "game"
            (root / "bin" / "hooks").mkdir(parents=True)
            game.mkdir()
            make_pe(game / "bio4.exe", 0x14C)
            (root / "bin" / "hooks" / "TextureToolkit-x86.asi").write_bytes(b"toolkit")
            (root / "bin" / "hooks" / "UltimateASILoader-x86.dll").write_bytes(b"loader")
            (game / "dinput8.dll").write_bytes(b"existing-user-file")
            actions = install_texture_hook(root, game / "bio4.exe", "RE4")
            self.assertEqual((game / "dinput8.dll").read_bytes(), b"existing-user-file")
            self.assertEqual((game / "d3d9.dll").read_bytes(), b"loader")
            self.assertTrue((game / "TextureFlow.TextureToolkit.asi").exists())
            self.assertTrue((game / "TT" / "dump").is_dir())
            manifest = json.loads((game / "TextureFlow.install.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["installed_loader"])
            self.assertEqual(manifest["loader_filename"], "d3d9.dll")
            self.assertTrue(any("d3d9.dll" in action for action in actions))

    def test_remove_only_deletes_files_installed_by_textureflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            game = Path(tmp) / "game"
            (root / "bin" / "hooks").mkdir(parents=True)
            game.mkdir()
            make_pe(game / "bio4.exe", 0x14C)
            (root / "bin" / "hooks" / "TextureToolkit-x86.asi").write_bytes(b"toolkit")
            (root / "bin" / "hooks" / "UltimateASILoader-x86.dll").write_bytes(b"loader")
            install_texture_hook(root, game / "bio4.exe", "RE4")
            remove_texture_hook(game / "bio4.exe")
            self.assertFalse((game / "TextureFlow.TextureToolkit.asi").exists())
            self.assertFalse((game / "dinput8.dll").exists())
            self.assertTrue((game / "TT").is_dir())

    def test_conflict_fails_without_partial_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            game = Path(tmp) / "game"
            (root / "bin" / "hooks").mkdir(parents=True)
            game.mkdir()
            make_pe(game / "bio4.exe", 0x14C)
            (root / "bin" / "hooks" / "TextureToolkit-x86.asi").write_bytes(b"toolkit")
            (root / "bin" / "hooks" / "UltimateASILoader-x86.dll").write_bytes(b"loader")
            (game / "dinput8.dll").write_bytes(b"mod-a")
            (game / "d3d9.dll").write_bytes(b"mod-b")
            with self.assertRaises(Exception):
                install_texture_hook(root, game / "bio4.exe", "RE4")
            self.assertFalse((game / "TextureFlow.TextureToolkit.asi").exists())

    def test_postal_installs_native_d3d8_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            game = Path(tmp) / "Postal2" / "System"
            (root / "bin" / "hooks").mkdir(parents=True)
            game.mkdir(parents=True)
            make_pe(game / "Postal2.exe", 0x14C)
            (root / "bin" / "hooks" / "TextureFlowD3D8-x86.asi").write_bytes(b"d3d8-native")
            (root / "bin" / "hooks" / "UltimateASILoader-x86.dll").write_bytes(b"loader")

            actions = install_texture_hook(root, game / "Postal2.exe", "Postal 2 (D3D8 nativo)")

            self.assertTrue((game / "TextureFlow.D3D8.asi").is_file())
            self.assertFalse((game / "TextureFlow.TextureToolkit.asi").exists())
            self.assertTrue((game / "TextureFlowD3D8.ini").is_file())
            self.assertIn("EnableInjection = 1", (game / "TextureFlowD3D8.ini").read_text(encoding="utf-8"))
            manifest = json.loads((game / "TextureFlow.install.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["plugin_filename"], "TextureFlow.D3D8.asi")
            self.assertEqual(manifest["loader_filename"], "d3d8.dll")
            self.assertIn(
                "MinDumpDimension = 64",
                (game / "TextureFlowD3D8.ini").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "ToggleHotKey = 36",
                (game / "TextureFlowD3D8.ini").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "VramBudgetMB = 2048",
                (game / "TextureFlowD3D8.ini").read_text(encoding="utf-8"),
            )
            self.assertTrue((game / "TT" / "pending").is_dir())
            self.assertTrue(any("Direct3D 8 nativo" in action for action in actions))
            healthy, findings = inspect_texture_hook(game / "Postal2.exe")
            self.assertTrue(healthy, findings)

    def test_postal_rejects_x64_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            game = Path(tmp) / "game"
            (root / "bin" / "hooks").mkdir(parents=True)
            game.mkdir()
            make_pe(game / "Postal2.exe", 0x8664)
            with self.assertRaisesRegex(Exception, "x86"):
                install_texture_hook(root, game / "Postal2.exe", "Postal 2")

    def test_postal_preserves_existing_d3d8_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            game = Path(tmp) / "game"
            (root / "bin" / "hooks").mkdir(parents=True)
            game.mkdir()
            make_pe(game / "Postal2.exe", 0x14C)
            (root / "bin" / "hooks" / "TextureFlowD3D8-x86.asi").write_bytes(b"d3d8-native")
            (root / "bin" / "hooks" / "UltimateASILoader-x86.dll").write_bytes(b"loader")
            (game / "d3d8.dll").write_bytes(b"existing-wrapper")

            install_texture_hook(root, game / "Postal2.exe", "Postal 2")

            self.assertEqual((game / "d3d8.dll").read_bytes(), b"existing-wrapper")
            self.assertEqual((game / "dinput8.dll").read_bytes(), b"loader")

    def test_diagnose_detects_changed_loader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            game = Path(tmp) / "game"
            (root / "bin" / "hooks").mkdir(parents=True)
            game.mkdir()
            make_pe(game / "Postal2.exe", 0x14C)
            (root / "bin" / "hooks" / "TextureFlowD3D8-x86.asi").write_bytes(b"d3d8-native")
            (root / "bin" / "hooks" / "UltimateASILoader-x86.dll").write_bytes(b"loader")
            install_texture_hook(root, game / "Postal2.exe", "Postal 2")
            (game / "d3d8.dll").write_bytes(b"changed-by-another-mod")

            healthy, findings = inspect_texture_hook(game / "Postal2.exe")

            self.assertFalse(healthy)
            self.assertTrue(any("ALTERADO: d3d8.dll" in line for line in findings), findings)

    def test_postal_remove_preserves_dumps_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            game = Path(tmp) / "game"
            (root / "bin" / "hooks").mkdir(parents=True)
            game.mkdir()
            make_pe(game / "Postal2.exe", 0x14C)
            (root / "bin" / "hooks" / "TextureFlowD3D8-x86.asi").write_bytes(b"d3d8-native")
            (root / "bin" / "hooks" / "UltimateASILoader-x86.dll").write_bytes(b"loader")
            install_texture_hook(root, game / "Postal2.exe", "Postal 2")
            (game / "TT" / "dump" / "sample.dds").write_bytes(b"keep")

            actions = remove_texture_hook(game / "Postal2.exe")

            self.assertFalse((game / "TextureFlow.D3D8.asi").exists())
            self.assertFalse((game / "d3d8.dll").exists())
            self.assertTrue((game / "TT" / "dump" / "sample.dds").exists())
            self.assertTrue((game / "TextureFlowD3D8.ini").exists())
            self.assertTrue(any("TextureFlow.D3D8.asi" in action for action in actions))

    def test_installer_safely_updates_owned_postal_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            game = Path(tmp) / "game"
            (root / "bin" / "hooks").mkdir(parents=True)
            game.mkdir()
            make_pe(game / "Postal2.exe", 0x14C)
            plugin_source = root / "bin" / "hooks" / "TextureFlowD3D8-x86.asi"
            plugin_source.write_bytes(b"old-hook")
            (root / "bin" / "hooks" / "UltimateASILoader-x86.dll").write_bytes(b"loader")
            install_texture_hook(root, game / "Postal2.exe", "Postal 2")

            plugin_source.write_bytes(b"new-hook")
            actions = install_texture_hook(root, game / "Postal2.exe", "Postal 2", vram_budget_mb=4096)

            self.assertEqual((game / "TextureFlow.D3D8.asi").read_bytes(), b"new-hook")
            self.assertTrue(any("atualizado TextureFlow.D3D8.asi" in action for action in actions))
            manifest = json.loads((game / "TextureFlow.install.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["installed_loader"])
            self.assertEqual(manifest["vram_budget_mb"], 4096)


if __name__ == "__main__":
    unittest.main()
