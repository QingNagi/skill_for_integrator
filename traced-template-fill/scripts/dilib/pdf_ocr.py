"""Local Tesseract TSV, with word confidences and unmodified measurement text."""
import csv
import hashlib
import io
import json
import os
import re
import subprocess

# Tesseract confidence is on a 0–100 scale. This flags words for human review.
WORD_REVIEW_THRESHOLD = 80


# The OCR engine scores a whole token. A flagged decimal/sign inherits that
# token score; we do not claim a per-character confidence.
_UNITS = re.compile(r"(?:^|[0-9])(?:[fpnumµμkMGT]?(?:V|A|W|F|H|Hz|Ω|Ohm|s|S|m|g|J|Pa|rad|dB|bps|bit/s)"
                    r"(?:/[fpnumµμkMGT]?(?:V|A|Hz|s|m))?|°[CF]?|℃|[％%])$")
_DEFAULT_SCAN = {"sparse_area": 0.04, "mixed_area": 0.25, "native_chars": 15, "body_margin": 0.12}


def scan_policy():
    from .common import DIError
    settings = dict(_DEFAULT_SCAN)
    for key, env, upper in (("mixed_area", "DI_OCR_MIXED_MIN_AREA", 1),
                            ("sparse_area", "DI_OCR_SPARSE_MIN_AREA", 1),
                            ("body_margin", "DI_OCR_BODY_MARGIN", 0.49),
                            ("native_chars", "DI_OCR_NATIVE_CHARS", 100000)):
        raw = os.environ.get(env)
        if raw:
            try:
                value = int(raw) if key == "native_chars" else float(raw)
                if not 0 <= value <= upper:
                    raise ValueError
            except ValueError:
                raise DIError("%s 超过允许的配置范围 0 到 %s" % (env, upper))
            settings[key] = value
    return settings


def review_groups(block):
    groups = {key: [] for key in ("数字", "单位", "小数点", "正负号", "文字")}
    for word in block.get('numeric_warnings', []) + block.get('text_warnings', []):
        token = word["text"]
        numeric = bool(re.search(r"\d", token))
        tags = []
        if numeric:
            tags.append("数字")
        if _UNITS.search(token):
            tags.append("单位")
        if token in (".", "．") or re.search(r"[.．]\d|\d[.．]", token):
            tags.append("小数点")
        if token in ("+", "-", "−", "－", "＋", "±", "–") or re.search(r"[+−－＋±–-]\d", token):
            tags.append("正负号")
        for key in tags or ["文字"]:
            groups[key].append(word)
    return groups.items()


def recognize_region(image, options, recognize):
    """Persist recognizer output in pixel coordinates, before converting it to page coordinates."""
    from .common import read_json, write_json
    directory = options.get("ocr_cache_dir")
    identity = options.get("ocr_identity")
    if not directory or not identity:
        return recognize(image, options)
    with image.convert("L") as pixels:
        metadata = ["ocr-lines-v1", identity, options["language"], options["dpi"], pixels.size, WORD_REVIEW_THRESHOLD]
        digest = hashlib.sha256(json.dumps(metadata, separators=(",", ":")).encode("utf-8"))
        digest.update(pixels.tobytes())
        path = os.path.join(directory, digest.hexdigest() + ".json")
        try:
            lines = read_json(path)
            if isinstance(lines, list) and lines and all(isinstance(line, dict) and isinstance(line.get("text"), str)
                    and len(line.get("bbox") or []) == 4 for line in lines):
                return lines
        except (OSError, ValueError, TypeError):
            pass
        lines = recognize(pixels, options)
        if lines:
            try:
                write_json(path, lines, compact=True)
            except OSError:
                pass  # A read-only/full cache does not invalidate successfully recognized evidence.
        return lines


def parse_tsv(tsv):
    groups = {}
    rows = csv.DictReader(io.StringIO(tsv), delimiter='\t', quoting=csv.QUOTE_NONE)
    if not rows.fieldnames or not {'level', 'text', 'conf', 'left', 'top', 'width', 'height'} <= set(rows.fieldnames):
        raise ValueError('Tesseract 没有输出 TSV 词条')
    for row in rows:
        word = row['text'].strip()
        if row['level'] != '5' or not word:
            continue
        score = float(row['conf'])
        if not 0 <= score <= 100:
            continue
        key = tuple(row[k] for k in ('page_num', 'block_num', 'par_num', 'line_num'))
        x, y, w, h = (int(row[k]) for k in ('left', 'top', 'width', 'height'))
        groups.setdefault(key, []).append((word, score, (x, y, x + w, y + h)))
    lines = []
    for words in groups.values():
        text = ' '.join(w for w, _, _ in words)
        text = re.sub(r'(?<=[\u3400-\u9fff]) (?=[\u3400-\u9fff])', '', text)
        coords = list(zip(*(box for _, _, box in words)))
        numeric_warnings, text_warnings = [], []
        for word, score, _ in words:
            if score < WORD_REVIEW_THRESHOLD:
                warnings = numeric_warnings if re.search(r'\d', word) else text_warnings
                warnings.append({'text': word, 'confidence': round(score, 1)})
        lines.append({'text': text, 'bbox': [min(coords[0]), min(coords[1]), max(coords[2]), max(coords[3])],
                      'confidence': round(min(s for _, s, _ in words), 1),
                      'numeric_warnings': numeric_warnings, 'text_warnings': text_warnings,
                      'words': [{'text': w, 'confidence': round(s, 1), 'bbox': list(b)} for w, s, b in words]})
    return lines


def save_png(image, path):
    """Publish a complete PNG even if two drafts use the same raster region."""
    import tempfile
    staging = None
    try:
        with tempfile.NamedTemporaryFile(dir=os.path.dirname(path), suffix=".tmp", delete=False) as stream:
            staging = stream.name
            image.save(stream, format="PNG")
        os.replace(staging, path)
        staging = None
    finally:
        if staging:
            os.unlink(staging)


def snapshot_evidence(job, sources):
    """Copy each recognized line's original, unmasked pixels into the draft's evidence snapshots."""
    from pathlib import Path
    from .common import safe_name
    lines = []
    destination = Path(job.p("草稿", "源文件快照", "OCR"))
    for alias, model in sources.items():
        regions = {}
        for block in model.m.get("blocks", []):
            region = block.get("ocr_region")
            if region:
                regions.setdefault(region["image"], []).append((block, region["pixels"]))
        if not regions:
            continue
        from PIL import Image
        destination.mkdir(parents=True, exist_ok=True)
        for iid, items in regions.items():
            source = model.m["images"][iid]["file"]
            try:
                with Image.open(source) as original:
                    for block, pixels in items:
                        with original.crop(pixels) as crop:
                            digest = hashlib.sha256(crop.tobytes()).hexdigest()[:16]
                            filename = "%s-%s-%s.png" % (safe_name(alias), safe_name(block["id"]), digest)
                            path = destination / filename
                            save_png(crop, path)
                        label = "%s:%s 第 %d 页" % (alias, block["id"], block["page"])
                        relative = path.relative_to(job.dir).as_posix()
                        lines.append("- %s　![原页裁切图](<%s>)" % (label, relative))
            except OSError as exc:
                lines.append("- ⚠ %s 第 %d 页的区域 %s 裁切图生成失败（%s）" % (
                    alias, items[0][0]["page"], iid, exc))
    return ["", "## OCR 原页裁切图", ""] + lines if lines else []


def recognize_image(image, options):
    """Pipe grayscale PNM to the bundled CLI and read its UTF-8 TSV from stdout."""
    data = io.BytesIO()
    image.convert('L').save(data, format='PPM')
    # The child alone changes working directory. Narrow-path native model I/O
    # therefore uses ASCII even when the installation is under a Chinese path.
    command = [os.path.abspath(options['executable']), 'stdin', 'stdout', '--tessdata-dir', '.',
               '-l', options['language'], '--dpi', str(options.get('dpi', 300)),
               '-c', 'tessedit_create_tsv=1']
    proc = subprocess.run(command, cwd=options['tessdata'], input=data.getvalue(),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if proc.returncode:
        detail = proc.stderr.decode('utf-8', 'replace').strip()[-350:]
        raise ValueError('Tesseract (%d) %s' % (proc.returncode, detail))
    return parse_tsv(proc.stdout.decode('utf-8'))
