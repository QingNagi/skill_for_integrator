"""Layout extraction with pdfplumber; render only requested raster regions with PDFium."""
from bisect import bisect_right
from contextlib import ExitStack
import hashlib
import math
import os
import re
from statistics import median

from .pdf_ocr import recognize_image, recognize_region, review_groups, save_png, scan_policy
from .read_other import _pdf_common_post, _span_runs, cjk_space


def _bbox(obj):
    return (obj['x0'], obj['top'], obj['x1'], obj['bottom'])


def _intersect(a, b):
    x, y = max(a[0], b[0]), max(a[1], b[1])
    r, s = min(a[2], b[2]), min(a[3], b[3])
    return (x, y, r, s) if x < r and y < s else None


def _visible_box(page):
    # PDF crop boxes are unrotated bottom-left coordinates. pdfplumber image
    # and glyph boxes refer to the rotated media box, with top-left origin.
    mx, my, mr, mt = page.page_obj.mediabox
    crop = _intersect((mx, my, mr, mt), page.page_obj.cropbox)
    if not crop:
        return None
    l, b, r, t = crop
    if page.rotation == 90:
        box = (b - my, l - mx, t - my, r - mx)
    elif page.rotation == 180:
        box = (mr - r, b - my, mr - l, t - my)
    elif page.rotation == 270:
        box = (mt - t, mr - r, mt - b, mr - l)
    else:
        box = (l - mx, mt - t, r - mx, mt - b)
    x, y = page.mediabox[:2]
    return (x + box[0], y + box[1], x + box[2], y + box[3])


def _contains(box, char):
    x = (char['x0'] + char['x1']) / 2
    y = (char['top'] + char['bottom']) / 2
    return box[0] <= x < box[2] and box[1] <= y < box[3]


def _coords(box):
    return [round(val, 2) for val in box]


def read_page_words(path, page_number):
    """Native word boxes in PDF points, top-left origin, original one-based page number."""
    from . import _bootstrap
    _bootstrap.activate_vendor()
    import pdfplumber
    if page_number < 1:
        raise ValueError('页码从 1 开始')
    with open(path, 'rb') as stream, pdfplumber.open(stream, pages=[page_number]) as doc:
        if not doc.pages:
            raise ValueError('PDF 没有第 %d 页' % page_number)
        page = doc.pages[0]
        visible = _visible_box(page)
        if not visible:
            return []
        view = page if visible == page.bbox else page.crop(visible)
        return [{'text': w['text'], 'bbox': _coords(_bbox(w)), 'page': page_number}
                for w in view.extract_words()]


def _cell_content(chars):
    from pdfplumber.utils import extract_text
    if not chars:
        return '', None
    # Preserve super/subscripts when different-sized glyphs share a baseline group.
    if min(c['size'] for c in chars) < 0.85 * max(c['size'] for c in chars):
        spans = [{'size': c['size'], 'text': c['text'], 'origin': (c['x0'], c['bottom'])} for c in chars]
        runs = _span_runs(spans)
        if any(flag for _, flag in runs):
            return ''.join(text for text, _ in runs), runs
    text = extract_text(chars).strip()
    # Join wrapped CJK phrases and preserve word boundaries between Latin words.
    return cjk_space(re.sub(r'(?<=[一-鿿])\n|\n(?=[一-鿿])', '', text).replace('\n', ' ')), None


def _table(table, chars):
    rects = table.cells
    xs = sorted({v for box in rects for v in (box[0], box[2])})
    ys = sorted({v for box in rects for v in (box[1], box[3])})
    xidx = {x: i for i, x in enumerate(xs)}
    yidx = {y: i for i, y in enumerate(ys)}
    cells = []
    # The row/column boundaries come from actual drawn cell edges, including
    # irregular grids where a None placeholder could mean either merge direction.
    row_chars = {}
    for ch in chars:
        if _contains(table.bbox, ch):
            r = bisect_right(ys, (ch['top'] + ch['bottom']) / 2) - 1
            row_chars.setdefault(r, []).append(ch)
    for box in sorted(rects, key=lambda b: (b[1], b[0])):
        r, c = yidx[box[1]], xidx[box[0]]
        rs, cs = yidx[box[3]] - r, xidx[box[2]] - c
        inside = [ch for rr in range(r, r + rs) for ch in row_chars.get(rr, ())
                  if _contains(box, ch)]
        text, runs = _cell_content(inside)
        cell = {'r': r, 'c': c, 'text': text, 'bbox': _coords(box)}
        if rs > 1:
            cell['rs'] = rs
        if cs > 1:
            cell['cs'] = cs
        if runs:
            cell['runs'] = runs
        cells.append(cell)
    nr, nc = len(ys) - 1, len(xs) - 1
    hdr = max((c.get('rs', 1) for c in cells if c['r'] == 0), default=1)
    result = {'t': 'tbl', 'bbox': _coords(table.bbox), 'cells': cells, 'nrows': nr, 'ncols': nc,
              'hdr': min(hdr, max(1, nr - 1))}
    last = [c for c in cells if c['r'] == nr - 1]
    if nr > 2 and len(last) == 1 and last[0].get('cs', 1) == nc and re.match(r'^(注|[a-z][\s.:：、)）]?)', last[0]['text']):
        result.update(foot=[last[0]['text']], foot_row=nr - 1)
    return result


def _line_parts(line):
    # pdfplumber's line extractor coalesces columns on a shared baseline.
    # Keep its text verbatim unless a visible gutter separates native glyphs.
    from pdfplumber.utils import extract_text, objects_to_bbox
    chars = line['chars']
    cuts = [(i, (cur['x0'] + prev['x1']) / 2) for i, (prev, cur) in enumerate(zip(chars, chars[1:]), 1)
            if cur['x0'] - prev['x1'] > 3 * max(cur['size'], prev['size'])]
    if not cuts:
        yield line['text'], _bbox(line), chars, []
        return
    boundaries = [0] + [i for i, _ in cuts] + [len(chars)]
    for lo, hi in zip(boundaries, boundaries[1:]):
        group = chars[lo:hi]
        yield extract_text(group), objects_to_bbox(group), group, [x for _, x in cuts]


def _native_paragraphs(page, table_boxes):
    if table_boxes:
        page = page.filter(lambda obj: obj['object_type'] != 'char' or
                           not any(_contains(box, obj) for box in table_boxes))
    items = []
    for line in page.extract_text_lines():
        for text, box, chars, cuts in _line_parts(line):
            if text.strip():
                items.append({'t': 'p', 'text': cjk_space(text.strip()), 'bbox': _coords(box),
                              'size': round(median(c['size'] for c in chars), 1),
                              'bold': any(re.search(r'Bold|Hei', c['fontname'], re.I) for c in chars),
                              '_gutters': cuts})
    return items


def _ordered_page(items):
    items = sorted(items, key=lambda b: (b['bbox'][1], b['bbox'][0]))
    gutters = [x for b in items for x in b.pop('_gutters', [])]
    if gutters:
        divider = median(gutters)
        ordered, left, right = [], [], []
        for b in items:
            box = b['bbox']
            if box[2] <= divider:
                left.append(b)
            elif box[0] >= divider:
                right.append(b)
            else:
                # A spanning heading, illustration or table interrupts both columns.
                ordered.extend(left + right + [b])
                left, right = [], []
        items = ordered + left + right

    paragraphs = []
    for b in items:
        prev = paragraphs[-1] if paragraphs else {}
        size = b.get('size')
        box = b['bbox']
        if (size and prev.get('size') and abs(prev['size'] - size) < 0.6 and prev['bold'] == b['bold'] and
                0 <= box[1] - prev['bbox'][3] < size * 0.45 and
                abs(box[0] - prev['bbox'][0]) <= size * 1.1 and
                not re.match(r'^(\d+[.)、\s]|[（(]\d+|[一二三四五六七八九十]+[、.]|表\s*\d|图\s*\d|注[:：])', b['text'])):
            prev['text'] += ('' if re.search(r'[一-鿿]$', prev['text']) else ' ') + b['text']
            prev['bbox'] = [min(box[0], prev['bbox'][0]), prev['bbox'][1], max(box[2], prev['bbox'][2]), box[3]]
        else:
            paragraphs.append(b)
    return paragraphs


def _scan_region(box, visible, chars, policy):
    native_count = sum(len(c['text'].strip()) for c in chars)
    if not native_count:
        return True
    x0, top, x1, bottom = visible
    height = bottom - top
    if native_count < policy["native_chars"]:
        return (box[2] - box[0]) * (box[3] - box[1]) >= (x1 - x0) * height * policy["sparse_area"]
    body = (x0, top + height * policy["body_margin"], x1, bottom - height * policy["body_margin"])
    interior = _intersect(box, body)
    if not interior or (interior[2] - interior[0]) * (interior[3] - interior[1]) < (x1 - x0) * height * policy["mixed_area"]:
        return False
    if any(c['text'].strip() and _contains(interior, c) for c in chars):
        return None
    return True


class _Renderer:
    def __init__(self, path, stack):
        self.path, self.stack, self.doc = path, stack, None
        self.page, self.pno = None, None

    def crop(self, pno, box, visible, dpi):
        import pypdfium2
        if self.doc is None:
            stream = self.stack.enter_context(open(self.path, 'rb'))
            self.doc = pypdfium2.PdfDocument(stream)
            self.stack.callback(self.doc.close)
        if self.pno != pno:
            if self.page is not None:
                self.page.close()
            self.page = self.doc[pno - 1]
            self.pno = pno
        page = self.page
        # Both coordinate systems now use the rotated visible page. Render
        # only the crop; the size limit applies to its longest dimension.
        w, h = page.get_size()
        sx, sy = w / (visible[2] - visible[0]), h / (visible[3] - visible[1])
        crop = ((box[0] - visible[0]) * sx, (visible[3] - box[3]) * sy,
                (visible[2] - box[2]) * sx, (box[1] - visible[1]) * sy)
        width, height = (box[2] - box[0]) * sx, (box[3] - box[1]) * sy
        scale = min(dpi / 72, 5500 / max(width, height), math.sqrt(16_000_000 / (width * height)),
                    (2 ** 30) / max(w, h))
        bitmap = page.render(scale=scale, crop=crop)
        try:
            return bitmap.to_pil().copy()
        finally:
            bitmap.close()


def _raster_items(page, pno, options, renderer, media_dir, first_id, errors):
    items = []
    visible = page.bbox
    chars = page.chars
    regions = {}
    for pic in page.root_page.images:
        raw = _bbox(pic)
        clipped = _intersect(raw, visible)
        if clipped:
            # Bounding boxes can rotate with the page or image matrix. Match
            # long and short sides to estimate a source-resolution ceiling.
            pw, ph = sorted(pic['srcsize'])
            w, h = sorted((raw[2] - raw[0], raw[3] - raw[1]))
            dpi = min(options.get('dpi', 300), 72 * pw / w, 72 * ph / h)
            regions[clipped] = max(regions.get(clipped, 0), dpi)
    for index, (box, dpi) in enumerate(regions.items()):
        iid = 'I%d' % (first_id + index)
        info = {'id': iid, 'ext': 'png', 'page': pno, 'bbox': _coords(box)}
        items.append({'t': 'img', 'info': info, 'bbox': info['bbox']})
        needed = _scan_region(box, visible, chars, options["scan_policy"])
        if needed is None:
            errors.append("原生文字与大幅图像重叠，图内是否另有数据待核")
        enabled = needed and options['available']
        if needed and not enabled:
            errors.append(options['hint'])
        if not (enabled or media_dir):
            continue
        try:
            image = renderer.crop(pno, box, visible, dpi if enabled else min(144, dpi))
            try:
                if media_dir:
                    os.makedirs(media_dir, exist_ok=True)
                    signature = hashlib.sha256(image.tobytes()).hexdigest()[:16]
                    dest = os.path.join(media_dir, '%s-%dx%d-%s.png' % (iid, image.width, image.height, signature))
                    save_png(image, dest)
                    info['file'] = dest
                if enabled:
                    from PIL import ImageDraw
                    sx, sy = image.width / (box[2] - box[0]), image.height / (box[3] - box[1])
                    # Remove digital glyphs that overlay the image (e.g. a footer).
                    draw = ImageDraw.Draw(image)
                    for ch in chars:
                        overlap = _intersect(box, _bbox(ch))
                        if overlap:
                            draw.rectangle([(sx * (overlap[0] - box[0]), sy * (overlap[1] - box[1])),
                                            (sx * (overlap[2] - box[0]), sy * (overlap[3] - box[1]))], fill='white')
                    lines = recognize_region(image, dict(options, dpi=max(1, round(min(sx, sy) * 72))), recognize_image)
                    if not lines:
                        errors.append('OCR 未能从扫描区域识别出文字')
                    for line in lines:
                        if media_dir:
                            x0, y0, x1, y1 = line['bbox']
                            pixels = [max(0, x0 - 4), max(0, y0 - 4), min(image.width, x1 + 4), min(image.height, y1 + 4)]
                            if pixels[0] < pixels[2] and pixels[1] < pixels[3]:
                                line['ocr_region'] = {'image': iid, 'pixels': pixels}
                        for target in [line] + line.get('words', []):
                            x0, top, x1, bottom = target['bbox']
                            target['bbox'] = _coords((box[0] + x0 / sx, box[1] + top / sy,
                                                      box[0] + x1 / sx, box[1] + bottom / sy))
                        items.append(dict(line, t='p', ocr=True))
            finally:
                image.close()
        except Exception as exc:
            errors.append('PDF 图像/OCR 读取失败：%s' % exc)
    return items


def _compare_cover_title(blocks, title):
    """Offer independent metadata when it differs only inside weak nonnumeric cover words."""
    cover = next((b for b in blocks if b['page'] == 1 and b.get('text')), {})
    weak = {(w['text'], w['confidence']) for w in dict(review_groups(cover))['文字']}
    if not weak or not title or len(title) > 200:
        return
    metadata = ''.join(title.split())
    text = ''.join(cover['text'].split())
    if len(text) != len(metadata):
        return
    changes = {i for i, (a, b) in enumerate(zip(text, metadata)) if a != b}
    if not changes or len(changes) * 5 > len(text) or any(metadata[i].isdigit() for i in changes):
        return
    offset, uncertain = 0, set()
    for word in cover['words']:
        end = offset + len(''.join(word['text'].split()))
        if (word['text'], word['confidence']) in weak:
            uncertain.update(range(offset, end))
        offset = end
    if changes <= uncertain:
        cover['metadata_title_hint'] = ' '.join(title.split())


def read_layout(path, media_dir, options):
    import pdfplumber
    if 'scan_policy' not in options:
        options = dict(options, scan_policy=scan_policy())
    blocks, figures, images, unread, recognized = [], [], {}, [], []
    np_ = nt = 0
    with ExitStack() as stack:
        stream = stack.enter_context(open(path, 'rb'))
        doc = stack.enter_context(pdfplumber.open(stream))
        renderer = _Renderer(path, stack)
        title = str((doc.metadata or {}).get('Title') or '')
        count = len(doc.pages)
        for pno, original in enumerate(doc.pages, 1):
            items, errors, boxes = [], [], []
            page = original
            try:
                visible = _visible_box(original)
                if not visible:
                    raise ValueError('页面裁切区域为空')
                page = original if visible == original.bbox else original.crop(visible)
                for table in page.find_tables():
                    items.append(_table(table, page.chars))
                    boxes.append(table.bbox)
            except Exception as exc:
                errors.append('表格读取失败：%s' % exc)
            try:
                items.extend(_native_paragraphs(page, boxes))
            except Exception as exc:
                errors.append('文本读取失败：%s' % exc)
            try:
                items.extend(_raster_items(page, pno, options, renderer, media_dir, len(images), errors))
            except Exception as exc:
                errors.append('图像读取失败：%s' % exc)
            finally:
                original.close()

            if not errors and not any(b.get('text') or any(c['text'] for c in b.get('cells', [])) for b in items):
                errors.append('没有提取到文本或表格文字')
            if errors:
                unread.append({'page': pno, 'why': '；'.join(dict.fromkeys(errors))})
            for block in _ordered_page(items):
                block['page'] = pno
                if block.get('ocr'):
                    recognized.append(block)
                elif block['t'] == 'tbl':
                    block['id'] = 'T%d' % nt
                    nt += 1
                else:
                    bid = 'P%d' % np_
                    block['id'] = bid
                    np_ += 1
                    if block['t'] == 'img':
                        info = block.pop('info')
                        iid, fid = info['id'], 'F%d' % len(figures)
                        images[iid] = info
                        block.update(t='p', text='', imgs=[iid], fig=fid, runs=[['', '@' + iid]])
                        figures.append({'id': fid, 'para': bid, 'imgs': [iid], 'cap': '', 'page': pno})
                blocks.append(block)
    for index, para in enumerate(recognized, start=np_):
        para['id'] = 'P%d' % index
    _compare_cover_title(blocks, title)
    result = _pdf_common_post(blocks, figures, images, title, path)
    result.update(unread=unread, ocr_pages=sorted({b['page'] for b in recognized}), pdf_backend='pdfplumber')
    result['stats']['pages'] = count
    return result
