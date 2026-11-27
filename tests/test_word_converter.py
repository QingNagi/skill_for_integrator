"""Regressions for the same-token Microsoft Office legacy Word converter fallback."""
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "traced-template-fill" / "scripts"))
from dilib import word_converter
from test_selfcheck import CT, make_docx


class WordConverterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "文 档.DOC"
        self.source.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"synthetic")
        self.dest = self.root / "result.docx"
        self.dest.write_bytes(b"previous destination")
        for patcher in (patch.object(word_converter, "_installed_converter", return_value=str(self.root / "Wordconv.exe")),
                        patch("dilib.environment._progid_ver", return_value=14),
                        patch.object(word_converter, "_file_block_configured", return_value=False)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_valid_docx_is_published_after_success(self):
        def convert(args, **options):
            self.assertEqual(args[1:4], ["-oice", "-nme", str(self.source)])
            self.assertNotEqual(args[-1], str(self.dest))
            self.assertEqual(Path(options["env"]["TEMP"]), Path(args[-1]).parent)
            self.assertEqual(Path(options["env"]["TMP"]), Path(args[-1]).parent)
            self.assertEqual(self.dest.read_bytes(), b"previous destination")
            make_docx(args[-1])
            return subprocess.CompletedProcess(args, 0)
        with patch.object(subprocess, "run", side_effect=convert):
            self.assertTrue(word_converter.try_convert_doc(self.source, self.dest))
        self.assertEqual(self.dest.read_bytes()[:2], b"PK")
        self.assertEqual(set(self.root.iterdir()), {self.source, self.dest})

    def test_invalid_output_timeout_and_failure_preserve_destination(self):
        for case in ("wrong format", "macro main part", "macro binary", "timeout", "nonzero"):
            def convert(args, **options):
                if case == "timeout":
                    raise subprocess.TimeoutExpired(args, 1)
                if case == "macro main part":
                    make_docx(args[-1], {"[Content_Types].xml": CT.replace(
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
                        "application/vnd.ms-word.document.macroEnabled.main+xml")})
                elif case == "macro binary":
                    make_docx(args[-1], {"word/vbaProject.bin": b"synthetic"})
                else:
                    Path(args[-1]).write_bytes(b"wrong format")
                return subprocess.CompletedProcess(args, 4 if case == "nonzero" else 0)
            with self.subTest(case=case), patch.object(subprocess, "run", side_effect=convert):
                self.assertFalse(word_converter.try_convert_doc(self.source, self.dest))
                self.assertEqual(self.dest.read_bytes(), b"previous destination")
                self.assertEqual(set(self.root.iterdir()), {self.source, self.dest})

    def test_standard_word_shortcut_excludes_encrypted_and_older_binary_formats(self):
        data = bytearray(1536)
        data[:8] = bytes.fromhex('D0CF11E0A1B11AE1')
        struct.pack_into('<H', data, 30, 9)
        struct.pack_into('<I', data, 48, 1)
        data[1104:1120] = bytes.fromhex('0609020000000000c000000000000046')
        entry = 1152
        name = 'WordDocument'.encode('utf-16-le')
        data[entry:entry+len(name)] = name
        data[entry+66] = 2
        struct.pack_into('<IQ', data, entry+116, 0, 8192)
        for version, flags, accepted in ((0xc1, 0, True), (0xc1, 0x100, False),
                                         (0xc1, 1, False), (0x65, 0, False)):
            struct.pack_into('<HH', data, 512, 0xa5ec, version)
            struct.pack_into('<H', data, 522, flags)
            self.source.write_bytes(data)
            with self.subTest(version=version, flags=flags):
                self.assertEqual(word_converter._standard_word_binary(self.source), accepted)

    @unittest.skipUnless(os.name == 'nt', 'Office registry policies')
    def test_file_block_delegates_to_word_without_running_converter(self):
        with patch.object(word_converter, '_file_block_configured', return_value=True), \
                patch.object(subprocess, 'run') as launch:
            self.assertFalse(word_converter.try_convert_doc(self.source, self.dest))
            launch.assert_not_called()

    def test_only_local_legacy_word_to_docx_is_attempted(self):
        with patch.object(subprocess, "run") as launch:
            self.assertFalse(word_converter.try_convert_doc(self.source, self.dest.with_suffix(".pdf")))
            self.assertFalse(word_converter.try_convert_doc(self.dest, self.source))
            self.source.write_bytes(b"HTML disguised as a legacy file")
            self.assertFalse(word_converter.try_convert_doc(self.source, self.dest))
            launch.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "NTFS zone metadata")
    def test_internet_mark_is_preserved_and_converter_not_launched(self):
        zone = Path(str(self.source) + ":Zone.Identifier")
        for data in (b"[ZoneTransfer]\r\nZoneId=3\r\n", b"[Unknown]\nZoneId=0\n",
                     b"[ZoneTransfer]\nZoneId=2\n" + b"x" * 18000):
            with self.subTest(marker=data[:30]):
                zone.write_bytes(data)
                with patch.object(subprocess, "run") as launch:
                    self.assertFalse(word_converter.try_convert_doc(self.source, self.dest))
                    launch.assert_not_called()
                self.assertEqual(zone.read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
