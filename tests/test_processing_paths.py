"""Regressions for the parser and dependency work avoided on warm commands."""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree.ElementTree import ParseError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "traced-template-fill" / "scripts"))
from dilib import _bootstrap, ooxml, safe_xml
from dilib.content import load_model


class ProcessingTests(unittest.TestCase):
    def test_cached_pdf_uses_verified_parse_without_activating_native_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tag = _bootstrap.platform_tag()
            if not tag or sys.version_info[:2] != (3, 14):
                self.skipTest('Embedded runtime profile requires CPython 3.14 on a supported platform')
            folder = root / 'wheels' / tag
            folder.mkdir(parents=True)
            wheel = folder / 'example.whl'
            wheel.write_bytes(b'example')
            spec = (wheel.name, 7, 'a' * 64, '')
            manifest = {'pure': {}, 'native': {tag: {'example': spec}}}
            source = root / 'sample.pdf'
            source.write_bytes(b'fixture: the PDF reader is instrumented')
            def parse(*args):
                _bootstrap._cache['activated'] = True
                return {'pdf_backend': 'pdfplumber', 'blocks': []}
            with patch.dict('os.environ', {'DI_OCR': 'off', 'DI_NO_VENDOR': ''}), \
                 patch.object(_bootstrap, '_vendor_root', return_value=root), \
                 patch.object(_bootstrap, 'wheel_manifest', return_value=manifest), \
                 patch.object(_bootstrap, '_cache', {}), \
                 patch.object(_bootstrap, 'activate_vendor', side_effect=AssertionError('unexpected import')), \
                 patch('dilib.read_other.read_pdf', side_effect=parse) as reader:
                first = load_model(str(source), tmp)
                _bootstrap._cache.clear()  # The next CLI command starts in a new interpreter.
                self.assertEqual(first, load_model(str(source), tmp))
                self.assertEqual(reader.call_count, 1)
                _bootstrap._cache.clear()
                manifest['native'][tag]['example'] = (wheel.name, 7, 'b' * 64, '')
                load_model(str(source), tmp)
                self.assertEqual(reader.call_count, 2)
                _bootstrap._cache.clear()
                manifest['native'][tag]['example'] = spec
                def fallback(*args):
                    _bootstrap._cache['activated'] = True
                    return {'pdf_backend': 'pypdf', 'blocks': []}
                reader.side_effect = fallback
                load_model(str(source), tmp)
                _bootstrap._cache.clear()
                reader.side_effect = parse
                self.assertEqual(load_model(str(source), tmp)['pdf_backend'], 'pdfplumber')
                self.assertEqual(reader.call_count, 4)

    def test_namespace_scopes_across_stream_chunks(self):
        data = ('<r xmlns="urn:root" xmlns:p="urn:root-p">' + 'a' * 67000
                + '<n xmlns="" xmlns:p="urn:inner"><p:v/>end</n></r>')
        first, root = {}, {}
        tree = safe_xml.parse_xml(io.BytesIO(data.encode("utf-16")), first, root)
        self.assertEqual(first, {"": "urn:root", "p": "urn:root-p"})
        self.assertEqual(root, first)
        self.assertEqual(tree[0].tag, "n")
        self.assertEqual(tree[0][0].tag, "{urn:inner}v")
        self.assertEqual(tree[0][0].tail, "end")
        with self.assertRaisesRegex(ParseError, "DOCTYPE"):
            safe_xml.parse_xml(io.BytesIO(b' ' * 67000 + b'<!DOCTYPE r><r/>'))

    def test_choice_and_ordinary_subtrees_keep_document_order(self):
        tags = '<r xmlns:mc="%s"><a/><mc:AlternateContent><mc:Choice><b/></mc:Choice>'
        tags += '<mc:Fallback><ignored/></mc:Fallback></mc:AlternateContent><c/></r>'
        root = safe_xml.parse_xml(tags % ooxml.NS["mc"])
        self.assertEqual([e.tag.split("}")[-1] for e in ooxml.iter_skip_fallback(root)],
                         ["r", "a", "AlternateContent", "Choice", "b", "c"])
        self.assertEqual(list(ooxml.iter_skip_fallback(root[1][0])), list(root[1][0].iter()))

    def test_vendor_import_stops_after_replaced_wheel_fails_digest(self):
        tag = "win_amd64"
        spec = _bootstrap.wheel_manifest()["native"][tag]["pypdfium2"]
        name, size, _, _ = spec
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp, "wheels", tag, name)
            wheel.parent.mkdir(parents=True)
            with wheel.open("wb") as stream:
                stream.truncate(size)
            cache = Path(tmp, "cache")
            with self.assertRaisesRegex(ValueError, "校验失败"):
                _bootstrap._unpack(wheel, spec, cache)
            self.assertFalse(cache.exists())
