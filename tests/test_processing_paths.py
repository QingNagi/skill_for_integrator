"""Regressions for the parser and dependency work avoided on warm commands."""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree.ElementTree import ParseError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dilib import _bootstrap, ooxml, safe_xml


class ProcessingTests(unittest.TestCase):
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
        name, size, _, _ = _bootstrap.wheel_manifest()["pymupdf"][tag]
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp, "wheels", tag, name)
            wheel.parent.mkdir(parents=True)
            with wheel.open("wb") as stream:
                stream.truncate(size)
            with patch.object(_bootstrap, "_VENDOR", tmp), patch.object(_bootstrap, "platform_tag", return_value=tag), \
                    patch.object(_bootstrap, "_import_first") as imported:
                self.assertIsNone(_bootstrap._from_vendor())
                imported.assert_not_called()
