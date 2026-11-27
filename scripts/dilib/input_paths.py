"""Resolve file arguments, batch source lists and workspace filename searches."""
import csv
import glob
from io import StringIO
import os

from .common import bigrams, out, token_scores
from .job import DOC_TYPES


def split_paths(value):
    """An existing literal path wins over the legacy semicolon list syntax."""
    if not value or not value.strip():
        return []
    if (';' not in value and '"' not in value) or os.path.exists(value):
        return [value]
    return [p for p in next(csv.reader(StringIO(value, newline=""), delimiter=";")) if p.strip()]


def join_paths(paths):
    stream = StringIO(newline="")
    csv.writer(stream, delimiter=";", lineterminator="\n").writerow(paths)
    return stream.getvalue()[:-1]


def expand_pattern(pattern):
    return [pattern] if os.path.isfile(pattern) else glob.glob(pattern)


def cmd_find(a):
    home = os.path.expanduser("~")
    dirs = [os.getcwd(), os.path.join(home, "Desktop"), os.path.join(home, "桌面"),
            os.path.join(home, "OneDrive", "Desktop"), os.path.join(home, "OneDrive", "桌面")] + (a.dir or [])
    if a.exact:
        dirs = a.dir or [os.getcwd()]
    depth = a.depth if a.depth is not None else (None if a.exact else 2)
    names = {q.casefold() for q in a.query}
    fuzzy_names, fuzzy_stems, queries = set(), set(), []
    if not a.exact:
        for q in a.query:
            stem, ext = os.path.splitext(q)
            fuzzy_names.add(q.lower())
            if ext.lower() in DOC_TYPES:
                fuzzy_stems.add(stem.lower())
            queries.append(bigrams(stem if ext.lower() in DOC_TYPES else q))

    seen, results = set(), []
    excluded = {"整合输出", "_缓存", ".git", "node_modules", ".claude", ".agents", ".codex"}
    for directory in dict.fromkeys(os.path.abspath(d) for d in dirs):
        if not os.path.isdir(directory):
            continue
        base_level = directory.rstrip(os.sep).count(os.sep)
        for root, subdirs, files in os.walk(directory):
            if depth is not None and root.rstrip(os.sep).count(os.sep) - base_level >= depth:
                subdirs.clear()
            else:
                subdirs[:] = [d for d in subdirs if d not in excluded]
            for name in files:
                if name.startswith("~$") or (a.exact and name.casefold() not in names):
                    continue
                stem, ext = os.path.splitext(name)
                if ext.lower() not in DOC_TYPES:
                    continue
                path = os.path.join(root, name)
                key = os.path.normcase(path)
                if key in seen:
                    continue
                seen.add(key)
                score = 1.0
                if not a.exact and name.lower() not in fuzzy_names and stem.lower() not in fuzzy_stems:
                    tokens = bigrams(stem)
                    score = 0.0
                    for query in queries:
                        coverage, similarity = token_scores(query, tokens)
                        score = max(score, coverage * 0.7 + similarity * 0.3)
                if score >= 0.35:
                    results.append((score, path))

    results.sort(key=lambda item: (-item[0], item[1].casefold(), item[1]))
    if not results:
        out("没找到匹配文件。请让用户提供完整路径。")
    limit = a.top if a.top is not None else (None if a.exact else 6)
    for score, path in results[:limit]:
        out("%.2f  %s" % (score, path))
    if a.exact:
        matches = [os.path.basename(path).casefold() for _, path in results]
        if len(matches) != len(set(matches)):
            out("有同名文件。请列出对应路径，请用户确认需要哪份。")
    elif len(results) > 1 and results[0][0] - results[1][0] < 0.1:
        out("（前几个匹配度接近：如不确定用哪个，请向用户确认）")
