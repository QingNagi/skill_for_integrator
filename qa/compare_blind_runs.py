"""Compare paired, offline-rescored blind table runs against their sealed answers."""
import argparse
import json
from pathlib import Path
from zipfile import BadZipFile

from blind_variants import matrix, read_key


def indexed(rows, required):
    by_key = {}
    for row in rows:
        key = row["case"], row["repeat"]
        if key in by_key or row.get("returncode") != 0 or row.get("altered_inputs"):
            raise ValueError(f"Duplicate, interrupted or altered run: {key}")
        counts = [row.get(field) for field in ("correct_fills", "correct_holds", "wrong_fills", "missed_fills")]
        if (any(type(n) is not int or n < 0 for n in counts)
                or sum(counts) != row.get("total") or not 0 < row["expected_fills"] <= sum(counts)
                or not counts[0] + counts[3] <= row["expected_fills"] <= counts[0] + counts[3] + counts[2]
                or len(row["differences"]) != counts[2] + counts[3]):
            raise ValueError(f"Incomplete scoring: {key}")
        by_key[key] = row
    if by_key.keys() != required:
        raise ValueError("Case/repeat coverage differs from the sealed cohort.")
    return by_key


def compare(before, old_rows, after, new_rows):
    for field in ("expected", "cases", "repeats", "model", "scorer"):
        if before[field] != after[field]:
            raise ValueError(f"Unpaired runs: {field} differs.")
    if not before["expected"] or before["cases"] != len(before["expected"]) or before["repeats"] < 1:
        raise ValueError("Empty or inconsistent cohort.")
    keys = {(case, repeat) for case in before["expected"] for repeat in range(1, before["repeats"] + 1)}
    old, new = indexed(old_rows, keys), indexed(new_rows, keys)
    paired = []
    for key in sorted(keys):
        a, b = old[key], new[key]
        if (a["total"], a["expected_fills"]) != (b["total"], b["expected_fills"]):
            raise ValueError(f"Different scoring coverage: {key}")
        paired.append(dict(case=key[0], repeat=key[1], baseline_differences=a["differences"],
                           differences=b["differences"], baseline_seconds=a["seconds"],
                           candidate_seconds=b["seconds"]))
    return {"quality_passed": all(not pair["differences"] for pair in paired), "paired": paired}


def unexpected_changes(first_key, second_key, first_values, second_values):
    if not (first_key.keys() == second_key.keys() == first_values.keys() == second_values.keys()):
        raise ValueError("The transformations must use identical target positions.")
    return [pos for pos, expected in first_key.items()
            if expected == second_key[pos] and first_values[pos] != second_values[pos]]


def preserved_positions(folder, cohort, runs, vault, pairs):
    rows = {(row["case"], row["repeat"]): row for row in runs}
    results = []
    for first, second in pairs:
        keys = []
        for case in (first, second):
            if case not in cohort["expected"] or Path(case).name != case:
                raise ValueError(f"Unknown case: {case}")
            digest = vault / (case + ".sha256")
            if digest.read_text("ascii").strip() != cohort["expected"][case]:
                raise ValueError(f"Different answer commitment: {case}")
            keys.append(read_key(vault / (case + ".json"), digest))
        if keys[0]["samples"] != keys[1]["samples"]:
            raise ValueError("This transformation changes target sample columns.")
        expected = [{(c["row"], c["column"]): c["value"] for c in key["cells"]} for key in keys]
        for repeat in range(1, cohort["repeats"] + 1):
            values = []
            for case, positions, key in zip((first, second), expected, keys):
                doc = folder / rows[case, repeat]["draft"]
                grid, _, samples = matrix(doc)
                if samples != key["samples"]:
                    raise ValueError(f"Unexpected target sample labels: {doc}")
                values.append({(r, c): grid[r][c] for r, c in positions})
            changed = unexpected_changes(*expected, *values)
            results.append(dict(cases=[first, second], repeat=repeat, changed_positions=changed))
    return results


def load_run(folder):
    def read(name):
        return json.loads((folder / name).read_text(encoding="utf-8"))
    return read("commitments.json"), read("rescored-summary.json")["runs"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--vault", type=Path, help="Sealed answer directory for paired transformations")
    parser.add_argument("--transform", nargs=2, action="append", default=[], metavar=("ORIGINAL", "VARIANT"),
                        help="Require unchanged target cells to keep their exact displayed text")
    args = parser.parse_args()
    if args.transform and not args.vault:
        parser.error("--transform requires --vault")
    try:
        baseline = load_run(args.baseline)
        candidate = load_run(args.candidate)
        result = compare(*baseline, *candidate)
        if args.transform:
            for label, directory, run in (("baseline", args.baseline, baseline), ("candidate", args.candidate, candidate)):
                result[label + "_transformations"] = preserved_positions(directory, *run, args.vault, args.transform)
            result["quality_passed"] &= all(not item["changed_positions"] for item in result["candidate_transformations"])
    except (KeyError, IndexError, OSError, TypeError, ValueError, BadZipFile) as error:
        parser.exit(2, f"Incomplete comparison: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(not result["quality_passed"])


if __name__ == "__main__":
    raise SystemExit(main())
