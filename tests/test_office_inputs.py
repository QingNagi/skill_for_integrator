"""Round-trip input regressions, using inert Office packages."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_selfcheck as fx
from dilib import office, ooxml, plan_xlsx, read_docx, read_other
from dilib.common import DIError

S = read_other.S[1:-1]


class OfficeInputs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_com_script_enforces_security_for_every_progid_and_restores_on_exit(self):
        ps = []
        def record(command, **kw):
            ps.append((command, Path(command[command.index("-File") + 1]).read_text(encoding="utf-8-sig")))
            return office.subprocess.CompletedProcess(command, 0)
        with patch.object(office, "detect", return_value={"powershell": "ps"}), patch.object(office.subprocess, "run", side_effect=record):
            office._com_batch("KWps.Application", "word", [("document.doc", str(self.root / "out.docx"), 16)])
        command, script = ps[0]
        self.assertNotIn("-ExecutionPolicy", command)
        self.assertTrue(Path(command[command.index("-File") + 1]).is_file())
        self.assertNotIn("if ($progid -in", script)
        self.assertLess(script.index("$app.AutomationSecurity = 3"), script.index(".Documents.Open"))
        self.assertIn("$app.AutomationSecurity = $previousSecurity", script)
        self.assertIn("Workbooks.Open($src, 0, $true)", script)

    def test_uninspectable_spreadsheet_never_opens_in_com(self):
        for ext in (".xls", ".xlsb", ".et", ".xlsx"):
            src, dst = self.root / ("old" + ext), self.root / "out.pdf"
            src.write_bytes(b"unidentified input")
            with self.subTest(ext=ext), patch.object(office, "_engines", return_value=[("com", "Excel.Application")]), patch.object(office, "_com_batch") as com:
                result = office.convert_many([(str(src), str(dst), "pdf")])
                self.assertFalse(com.called)
                self.assertIn("Excel 4.0", result[str(dst)])

    def test_biff_directory_and_substream_types_must_agree(self):
        import struct
        from dilib.legacy_excel import ordinary_biff
        def record(tag, data=b""):
            return struct.pack("<HH", tag, len(data)) + data
        def bof(kind):
            return record(0x0809, struct.pack("<HHHH", 0x0600, kind, 0, 0))
        def book(sheet_type=0, kind=0x0010, extra=b"", offset_delta=0):
            # Only inert record headers and a sheet name, no formulas or executable content.
            header, eof = bof(5), record(0x000A)
            directory = struct.pack("<IBBB", 0, 0, sheet_type, 1) + b"\0A"
            pos = len(header + record(0x0085, directory) + extra + eof) + offset_delta
            return header + record(0x0085, struct.pack("<I", pos) + directory[4:]) + extra + eof + bof(kind) + eof
        self.assertTrue(ordinary_biff(book()))
        for sample in (book(sheet_type=1), book(kind=0x0040), book(offset_delta=1),
                       book(extra=record(0x002F)), book(extra=record(0x0018)), book()[:-1]):
            self.assertFalse(ordinary_biff(sample))

    @unittest.skipUnless(office.detect().get("excel"), "requires desktop Excel")
    def test_plain_xls_round_trip_on_installed_excel_without_other_backends(self):
        source, legacy, restored = [self.root / name for name in ("source.xlsx", "old.xls", "readback.xlsx")]
        fx.make_xlsx(source, {"xl/worksheets/sheet1.xml": f'<worksheet xmlns="{S}"><sheetData><row r="1">'
                              '<c r="A1"><v>17.8</v></c></row></sheetData></worksheet>'})
        with patch.object(office, "_engines", return_value=[("com", "Excel.Application")]):
            self.assertEqual(office.convert_many([(source, legacy, "xls")]), {legacy: None})
            self.assertEqual(office.convert_many([(legacy, restored, "xlsx")]), {restored: None})
        self.assertEqual(read_other.read_xlsx(restored)["sheets"][0]["rows"][0][0], 17.8)

    def test_modern_workbook_with_xlm_name_is_routed_away_from_com(self):
        src = self.root / "names.xlsx"
        wb = (f'<workbook xmlns="{S}" xmlns:r="{fx.R}"><sheets><sheet name="data" r:id="rId1"/></sheets>'
              '<definedNames><definedName name="Example" xlm="1">1</definedName></definedNames></workbook>')
        fx.make_xlsx(src, {"xl/workbook.xml": wb})
        self.assertFalse(office._com_workbook(src))

    def test_valid_modern_workbook_remains_convertible_in_com(self):
        src, dst = self.root / "sheet.xlsx", self.root / "out.pdf"
        fx.make_xlsx(src)
        with patch.object(office, "_engines", return_value=[("com", "Excel.Application")]), patch.object(office, "_com_batch", return_value={}) as com:
            office.convert_many([(str(src), str(dst), "pdf")])
            com.assert_called_once()

    @unittest.skipUnless(office.os.name == "nt", "Windows file sharing modes")
    def test_validated_excel_source_stays_read_only_until_com_finishes(self):
        src, dst = self.root / "unchanged.xlsx", self.root / "out.pdf"
        fx.make_xlsx(src)
        def check(*args):
            with self.assertRaises(PermissionError):
                with src.open("r+b"):
                    pass
            return {}
        with patch.object(office, "_engines", return_value=[("com", "Excel.Application")]), patch.object(office, "_com_batch", side_effect=check):
            office.convert_many([(src, dst, "pdf")])
        with src.open("r+b"):
            pass

    def test_symbols_are_read_by_typeface_or_unicode(self):
        p = self.root / "spec.docx"
        chars = [("Symbol", "F0A3"), ("Symbol", "F0B1"), ("Symbol", "F06D"),
                 ("Symbol", "F06E"), ("Wingdings", "F06E"), ("Arial", "2265")]
        sym = "".join('<w:sym w:font="%s" w:char="%s"/>' % item for item in chars)
        fx.make_docx(p, {"word/document.xml": fx.doc_xml("<w:p><w:r>" + sym + "<w:t>0.5</w:t></w:r></w:p>")})
        self.assertEqual(read_docx.read_docx(p)["blocks"][0]["text"], "≤±μν■≥0.5")

    def test_numbering_is_scoped_to_instance_and_honors_level_overrides(self):
        path = self.root / "spec.docx"
        def para(n, i):
            return ('<w:p><w:pPr><w:numPr><w:ilvl w:val="%d"/><w:numId w:val="%d"/></w:numPr>'
                    '<w:outlineLvl w:val="%d"/></w:pPr><w:r><w:t>Heading</w:t></w:r></w:p>') % (i,n,i)
        nums = ('<w:numbering xmlns:w="%s"><w:abstractNum w:abstractNumId="0">'
                '<w:lvl w:ilvl="0"><w:start w:val="3"/><w:lvlText w:val="%%1"/></w:lvl>'
                '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:lvlText w:val="%%1.%%2"/></w:lvl>'
                '</w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/>'
                '<w:lvlOverride w:ilvl="1"><w:startOverride w:val="2"/><w:lvl w:ilvl="1">'
                '<w:numFmt w:val="decimalZero"/><w:lvlText w:val="%%1.%%2"/><w:lvlRestart w:val="0"/>'
                '</w:lvl></w:lvlOverride></w:num><w:num w:numId="2"><w:abstractNumId w:val="0"/>'
                '</w:num></w:numbering>') % fx.W
        body = "".join(para(n, i) for n, i in [(1,0),(1,1),(2,0),(1,0),(1,1)])
        fx.make_docx(path, {"word/document.xml": fx.doc_xml(body), "word/numbering.xml": nums})
        self.assertEqual([s["title"] for s in read_docx.read_docx(path)["sections"]],
                         [n + " Heading" for n in ("3", "3.02", "3", "4", "4.03")])

    def test_visible_sheet_coordinates_dates_and_missing_formula_cache(self):
        p = self.root / "data.xlsx"
        wb = (f'<workbook xmlns="{S}" xmlns:r="{fx.R}"><workbookPr date1904="1"/><sheets>'
              '<sheet name="visible" sheetId="1" r:id="rId1"/><sheet name="old" sheetId="2" state="veryHidden" r:id="rId2"/>'
              '</sheets></workbook>')
        sheet = (f'<worksheet xmlns="{S}"><cols><col min="2" max="2" hidden="1"/></cols><sheetData>'
                 '<row r="1"><c r="A1" s="1"><v>0</v></c><c r="B1"><v>444</v></c><c r="C1" t="b"><f>TRUE()</f></c></row>'
                 '<row r="2" hidden="1"><c r="A2"><v>555</v></c></row><row r="3"><c r="C3"><f>1+1</f><v>2</v></c></row>'
                 '</sheetData></worksheet>')
        rels = (f'<Relationships xmlns="{fx.PR}">' + fx.rel("rId1","worksheet","worksheets/sheet1.xml")
                + fx.rel("rId2","worksheet","worksheets/sheet2.xml") + '</Relationships>')
        fx.make_xlsx(p, {"xl/workbook.xml": wb, "xl/worksheets/sheet1.xml": sheet, "xl/worksheets/sheet2.xml": sheet,
                         "xl/_rels/workbook.xml.rels": rels,
                         "xl/styles.xml": f'<styleSheet xmlns="{S}"><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>'})
        model = read_other.read_xlsx(p)
        self.assertEqual([s["name"] for s in model["sheets"]], ["visible"])
        self.assertEqual(model["sheets"][0]["rows"], [["1904-01-01", None, None],[None]*3,[None,None,2]])
        self.assertIn("visible!C1", str(model["unread"]))
        self.assertIn("old", str(model["read_warnings"]))

    def test_non_worksheet_and_phonetic_annotations_are_not_measurement_evidence(self):
        p = self.root / "labels.xlsx"
        shared = f'<sst xmlns="{S}"><si><t>電流</t><rPh sb="0" eb="2"><t>でんりゅう</t></rPh></si></sst>'
        wb = (f'<workbook xmlns="{S}" xmlns:r="{fx.R}"><sheets><sheet name="main" r:id="rId1"/>'
              '<sheet name="graph" r:id="rId2"/></sheets></workbook>')
        rels = (f'<Relationships xmlns="{fx.PR}">' + fx.rel("rId1","worksheet","worksheets/sheet1.xml") +
                fx.rel("rId2","chartsheet","chartsheets/view.xml") + '</Relationships>')
        sheet = f'<worksheet xmlns="{S}"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>'
        fx.make_xlsx(p, {"xl/workbook.xml": wb, "xl/_rels/workbook.xml.rels": rels,
                         "xl/chartsheets/view.xml": f'<chartsheet xmlns="{S}"/>',
                         "xl/sharedStrings.xml": shared, "xl/worksheets/sheet1.xml": sheet})
        model = read_other.read_xlsx(p)
        self.assertEqual([sh["name"] for sh in model["sheets"]], ["main"])
        self.assertEqual(model["sheets"][0]["rows"], [["電流"]])
        self.assertIn("graph", str(model["read_warnings"]))

    def test_passive_xls_reader_preserves_visibility_and_date_system(self):
        from types import SimpleNamespace
        import sys
        visible = SimpleNamespace(name="current", visibility=0, nrows=3, ncols=2, merged_cells=[],
                                  rowinfo_map={1: SimpleNamespace(hidden=True)},
                                  colinfo_map={1: SimpleNamespace(hidden=True)},
                                  cell=lambda r, c: SimpleNamespace(ctype=3 if r == 0 else 2, value=r))
        old = SimpleNamespace(name="old", visibility=2, nrows=0, ncols=0, merged_cells=[])
        book = SimpleNamespace(datemode=1, sheets=lambda: [visible, old])
        fake = SimpleNamespace(open_workbook=lambda *args, **opts: book, XL_CELL_DATE=3, XL_CELL_NUMBER=2)
        with patch.dict(sys.modules, xlrd=fake):
            m = read_other.read_xls(self.root / "sample.xls")
        self.assertEqual([sh["name"] for sh in m["sheets"]], ["current"])
        self.assertEqual(m["sheets"][0]["rows"], [["1904-01-01", None], [None, None], [2, None]])
        self.assertEqual(m["sheets"][0]["hidden_rows"], [2])
        self.assertIn("old", str(m["read_warnings"]))

    def test_source_structure_inventory_and_visible_text(self):
        p = self.root / "structures.docx"
        body = ('<w:sdt><w:sdtContent><w:p><w:r><w:t>Visible</w:t></w:r>'
                '<w:del><w:r><w:delText>Deleted</w:delText></w:r></w:del>'
                '<w:moveFrom><w:r><w:t>Moved away</w:t></w:r></w:moveFrom>'
                '<w:ins><w:r><w:t>New</w:t></w:r></w:ins>'
                '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText>DATE</w:instrText></w:r>'
                '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>Result</w:t></w:r>'
                '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
                '<w:r><w:pict><w:txbxContent><w:p><w:r><w:t>Floating</w:t></w:r></w:p></w:txbxContent></w:pict></w:r>'
                '<w:r><w:footnoteReference w:id="1"/></w:r></w:p></w:sdtContent></w:sdt>')
        fx.make_docx(p, {"word/document.xml": fx.doc_xml(body), "word/header1.xml": b"<hdr/>",
                         "word/footnotes.xml": b"<notes/>"})
        m = read_docx.read_docx(p)
        self.assertEqual(m["blocks"][0]["text"], "VisibleNewResult")
        notes = str(m["unread"]) + str(m["read_warnings"])
        for label in ("文本框", "页眉", "脚注", "修订", "域"):
            self.assertIn(label, notes)

    def test_unicode_measurement_limits_units_and_range_sign(self):
        from decimal import Decimal
        from dilib import testdata, verify
        self.assertEqual(testdata.measurement_number("−1.20"), Decimal("-1.20"))
        self.assertEqual(testdata.parse_limit("≤ –2.5 µA", "hi", "μA"), Decimal("-2.5"))
        self.assertIsNone(testdata.parse_limit("1–2"))
        self.assertEqual(verify._numeric_tokens("−3.0–−1.5", "~"), ["-3.0", "-1.5"])
        self.assertEqual(verify._numeric_tokens("–1.2", "~"), ["-1.2"])

    def test_hidden_row_breaks_measurement_label_inheritance(self):
        from dilib import testdata
        sheet = {"name": "samples", "rows": [["item", 1], ["R", 1], [None, None], [None, 9], ["T", 2]],
                 "hidden_rows": [3]}
        parsed = testdata.parse_sheet(sheet, {})
        self.assertEqual(parsed["blocks"][0]["values"]["1"], [1])

    def test_records_encoding_without_changing_evidence_text(self):
        for read, name in ((read_other.read_csv, "a.csv"), (read_other.read_txt, "a.txt")):
            path = self.root / name
            for enc in ("utf-8-sig", "utf-8", "gb18030"):
                path.write_bytes("名称,测试\n".encode(enc))
                with self.subTest(name=name, enc=enc):
                    self.assertEqual(read(path).get("encoding"), enc)

    def test_oversize_zip_refused_by_document_and_both_workbook_readers(self):
        p = self.root / "sheet.xlsx"
        fx.make_xlsx(p)
        with patch.object(ooxml, "MAX_OFFICE_PART_BYTES", 10, create=True):
            for fn in (read_other.read_xlsx, plan_xlsx.read_workbook, ooxml.Package):
                with self.subTest(fn=fn.__name__), self.assertRaisesRegex(DIError, "大小"):
                    fn(str(p))
