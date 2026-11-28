"""Regression coverage for XML inputs in office documents and workbooks."""
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree.ElementTree import ParseError

import test_selfcheck as fixtures
from dilib import ooxml, program_manifest, read_other, selfcheck


class OfficeXmlTests(unittest.TestCase):
    def test_docx_part_rejects_doctype_in_utf8_and_utf16(self):
        xml = '<!DOCTYPE w:document>' + fixtures.doc_xml().replace(fixtures.HEAD, '')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'source.docx')
            for encoding in ('utf-8', 'utf-16'):
                with self.subTest(encoding=encoding):
                    fixtures.make_docx(path, {'word/document.xml': xml.encode(encoding)})
                    with self.assertRaisesRegex(ParseError, 'DOCTYPE'):
                        ooxml.Package(path).xml('word/document.xml')
                    self.assertIn('xml-bad', selfcheck.check_file(path).codes())
                    self.assertIsNone(selfcheck._snapshot_info(path)[0])

    def test_relationship_and_spreadsheet_readers_reject_doctype(self):
        rels = fixtures.ROOT_RELS.replace(fixtures.HEAD, '<!DOCTYPE Relationships>')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'source.xlsx')
            fixtures.make_xlsx(path, {'xl/_rels/workbook.xml.rels': rels})
            with self.assertRaisesRegex(ParseError, 'DOCTYPE'):
                read_other.read_xlsx(str(path))
            with self.assertRaisesRegex(program_manifest.DIError, 'DOCTYPE'):
                program_manifest.workbook_rows(path, None)
            with self.assertRaisesRegex(ParseError, 'DOCTYPE'):
                ooxml.Package(path).rels('xl/workbook.xml')

    def test_round_trip_preserves_namespaces_mixed_text_and_predefined_entities(self):
        xml = ('<root xmlns="urn:main" xmlns:unused="urn:unused">'
               '<p xmlns:a="urn:other">before<a:t a:label="A&amp;B"/>after</p>'
               '<p><![CDATA[<!DOCTYPE is just text>]]></p></root>')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'source.docx')
            out = Path(directory, 'out.docx')
            fixtures.make_docx(path, {'word/item.xml': xml})
            pkg = ooxml.Package(path)
            root = pkg.xml('word/item.xml')
            self.assertEqual(root[0].tag, '{urn:main}p')
            self.assertEqual(root[0].text, 'before')
            self.assertEqual(root[0][0].tail, 'after')
            self.assertEqual(root[0][0].get('{urn:other}label'), 'A&B')
            self.assertEqual(root[1].text, '<!DOCTYPE is just text>')
            pkg.save(out)
            with zipfile.ZipFile(out) as archive:
                data = archive.read('word/item.xml')
                self.assertIn(b'xmlns:unused="urn:unused"', data)
                self.assertIn(b'xmlns:a="urn:other"', data)


if __name__ == '__main__':
    unittest.main()
