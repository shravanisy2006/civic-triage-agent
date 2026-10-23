"""Measures accuracy and latency of the analysis step on 15 labelled reports.
Run:  python -m eval.run_eval
Writes eval/results.md (paste the numbers into the README)."""
import csv
import statistics
import time
from pathlib import Path

from src import ai, security

HERE = Path(__file__).parent


def main():
    rows = list(csv.DictReader(open(HERE / "samples.csv", encoding="utf-8")))
    results = []
    for r in rows:
        text = security.redact_pii(r["description"])
        analysis, source, ms = ai.analyze(None, text)
        results.append({
            "text": r["description"][:60],
            "expected": r["expected_category"],
            "got": analysis.category.value,
            "cat_ok": analysis.category.value == r["expected_category"],
            "hazard_ok": analysis.hazard == (r["expected_hazard"] == "true"),
            "severity": analysis.severity,
            "source": source,
            "ms": ms,
        })
        print(f"{'OK ' if results[-1]['cat_ok'] else 'BAD'} {source:8} {ms:7.0f} ms  {r['description'][:55]}")
        time.sleep(1)  # stay well inside free-tier rate limits

    n = len(results)
    cat_acc = sum(x["cat_ok"] for x in results) / n
    haz_acc = sum(x["hazard_ok"] for x in results) / n
    lat = [x["ms"] for x in results]
    src = results[0]["source"] if len({x["source"] for x in results}) == 1 else "mixed"

    lines = [
        "# Evaluation results", "",
        f"Analyser: **{src}** · samples: **{n}**", "",
        "| Metric | Value |", "|---|---|",
        f"| Category accuracy | {cat_acc:.0%} |",
        f"| Hazard accuracy | {haz_acc:.0%} |",
        f"| Median latency | {statistics.median(lat):.0f} ms |",
        f"| Max latency | {max(lat):.0f} ms |", "",
        "| Report | Expected | Got | Severity | ms |", "|---|---|---|---|---|",
    ]
    lines += [f"| {x['text']} | {x['expected']} | {'✅' if x['cat_ok'] else '❌'} {x['got']} | "
              f"{x['severity']} | {x['ms']:.0f} |" for x in results]
    (HERE / "results.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nCategory accuracy {cat_acc:.0%} · hazard accuracy {haz_acc:.0%} · "
          f"median {statistics.median(lat):.0f} ms  ->  eval/results.md")


if __name__ == "__main__":
    main()
