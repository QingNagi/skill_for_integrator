"""Local Tesseract TSV, with word confidences and unmodified measurement text."""
import csv
import io
import os
import re
import subprocess

# Tesseract confidence is on a 0–100 scale. This flags words for human review.
WORD_REVIEW_THRESHOLD = 80


def review_groups(block):
    return (('数字', block.get('numeric_warnings', [])), ('文字', block.get('text_warnings', [])))


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
