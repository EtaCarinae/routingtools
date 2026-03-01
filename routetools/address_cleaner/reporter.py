from __future__ import annotations

import csv
import io
from pathlib import Path

from routetools.address_cleaner.models import CleaningReport, NormalizedAddress


def build_summary(results: list[NormalizedAddress]) -> dict:
    total = len(results)
    flagged = sum(1 for r in results if r.flagged)
    escalated = sum(1 for r in results if r.escalated_to_google)
    arbiter_consulted = sum(1 for r in results if r.arbiter_consulted)
    failed = sum(1 for r in results if r.geocode.source.value == "failed")
    sources: dict[str, int] = {}
    for r in results:
        src = r.geocode.source.value
        sources[src] = sources.get(src, 0) + 1

    avg_confidence = sum(r.confidence for r in results) / total if total else 0.0
    cluster_ids = {r.dedupe_cluster_id for r in results if r.dedupe_cluster_id}

    return {
        "total": total,
        "flagged": flagged,
        "escalated_to_google": escalated,
        "arbiter_consulted": arbiter_consulted,
        "geocode_failed": failed,
        "geocode_sources": sources,
        "average_confidence": round(avg_confidence, 4),
        "unique_clusters": len(cluster_ids),
    }


def report_to_json(report: CleaningReport) -> str:
    return report.model_dump_json(indent=2)


def report_to_csv(report: CleaningReport) -> str:
    output = io.StringIO()
    fieldnames = [
        "original",
        "pre_normalized",
        "formatted_address",
        "lat",
        "lon",
        "geocode_source",
        "quality_score",
        "confidence",
        "flagged",
        "flag_reason",
        "escalated_to_google",
        "arbiter_consulted",
        "arbiter_rationale",
        "dedupe_cluster_id",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for r in report.results:
        writer.writerow(
            {
                "original": r.original,
                "pre_normalized": r.pre_normalized,
                "formatted_address": r.geocode.formatted_address or "",
                "lat": r.geocode.lat if r.geocode.lat is not None else "",
                "lon": r.geocode.lon if r.geocode.lon is not None else "",
                "geocode_source": r.geocode.source.value,
                "quality_score": r.geocode.quality_score
                if r.geocode.quality_score is not None
                else "",
                "confidence": r.confidence,
                "flagged": r.flagged,
                "flag_reason": r.flag_reason or "",
                "escalated_to_google": r.escalated_to_google,
                "arbiter_consulted": r.arbiter_consulted,
                "arbiter_rationale": r.arbiter_rationale or "",
                "dedupe_cluster_id": r.dedupe_cluster_id or "",
            }
        )
    return output.getvalue()


def save_report(
    report: CleaningReport,
    output_dir: str | Path,
    formats: list[str] | None = None,
) -> dict[str, Path]:
    if formats is None:
        formats = ["json"]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: dict[str, Path] = {}
    if "json" in formats:
        path = output_dir / f"{report.run_id}.json"
        path.write_text(report_to_json(report), encoding="utf-8")
        saved["json"] = path
    if "csv" in formats:
        path = output_dir / f"{report.run_id}.csv"
        path.write_text(report_to_csv(report), encoding="utf-8")
        saved["csv"] = path
    return saved
