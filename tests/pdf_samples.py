"""Small, synthetic PDF fixtures with explicit geometry in PDF user units."""
import zlib
from pathlib import Path


def make_pdf(path, pages, with_image=False):
    """Write (content stream, optional page dictionary entries) for 420x320 pages."""
    def stream(data, attrs=b''):
        return (b'<< /Length %d ' % len(data) + attrs + b' >>\nstream\n' + data + b'\nendstream')

    ids = [5 + i * 2 for i in range(len(pages))]
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Count %d /Kids [' % len(pages) +
               b' '.join(b'%d 0 R' % i for i in ids) + b'] >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    if with_image:
        from PIL import Image
        asset = Path(__file__).resolve().parents[1] / 'traced-template-fill/assets/offline-ocr.png'
        with Image.open(asset) as im:
            data = im.crop((0, 0, 700, 250)).convert('L')
            objects.append(stream(zlib.compress(data.tobytes()), b'/Type /XObject /Subtype /Image '
                                  b'/Width %d /Height %d ' % data.size +
                                  b'/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode'))
    else:
        objects.append(b'null')
    for i, (commands, extra) in zip(ids, pages):
        resources = b'/Font << /F1 3 0 R >>' + (b' /XObject << /Im 4 0 R >>' if with_image else b'')
        objects += [b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 420 320] /Resources << ' +
                    resources + b' >> /Contents %d 0 R ' % (i + 1) + extra.encode() + b' >>',
                    stream(commands.encode('ascii'))]
    with open(path, 'wb') as out:
        out.write(b'%PDF-1.4\n')
        offsets = []
        for i, obj in enumerate(objects, 1):
            offsets.append(out.tell())
            out.write(b'%d 0 obj\n' % i + obj + b'\nendobj\n')
        xref = out.tell()
        out.write(b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1))
        for offset in offsets:
            out.write(b'%010d 00000 n \n' % offset)
        out.write(b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objects) + 1, xref))


def make_scan(path):
    scan = 'q 350 0 0 125 30 64 cm /Im Do Q'
    make_pdf(path, [(scan, ''), (scan + '\nBT /F1 13 Tf 12 295 Td (Native header for mixed page) Tj ET', ''),
                    ('BT /F1 18 Tf 12 240 Td (Native content only) Tj ET', '')], with_image=True)


def make_table(path):
    # Row zero spans the first two columns. Column three spans the first two rows.
    lines = ['20 240 m 320 240 l', '20 200 m 220 200 l', '20 160 m 320 160 l', '20 120 m 320 120 l',
             '20 120 m 20 240 l', '120 120 m 120 200 l', '220 120 m 220 240 l', '320 120 m 320 240 l']
    texts = [(30, 275, 'Table 1 Results'), (30, 217, 'Measured limits'), (235, 215, 'Unit'),
             (30, 177, 'Low'), (135, 177, 'High'), (30, 137, '-0.125'), (135, 137, '1.250'), (235, 137, 'mV')]
    commands = ' '.join(lines) + ' S\n' + '\n'.join(
        'BT /F1 12 Tf %d %d Td (%s) Tj ET' % item for item in texts)
    make_pdf(path, [(commands, ''), ('', '')])
