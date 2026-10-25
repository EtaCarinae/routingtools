"""Command line entry point: `routetools-clean direcciones.csv`."""

from __future__ import annotations

import argparse
import asyncio
import sys

from routetools.address_cleaner.errors import ProviderAuthError
from routetools.address_cleaner.ingestion.csv_ingester import CsvIngester
from routetools.address_cleaner.models import NormalizedAddress
from routetools.address_cleaner.pipeline import AddressCleanerPipeline
from routetools.address_cleaner.reporter import save_report


def _status(r: NormalizedAddress) -> str:
    if r.flagged:
        return "REVISAR"
    if r.escalated:
        return "ESCALADA"
    if r.arbiter_consulted:
        return "LLM OK"
    return "OK"


def _print_progress(r: NormalizedAddress) -> None:
    print(f"  {_status(r):<9} {r.confidence:.2f}  {r.original}", file=sys.stderr)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="routetools-clean",
        description="Normaliza, geocodifica y deduplica un CSV de direcciones.",
    )
    parser.add_argument("csv", help="CSV de entrada")
    parser.add_argument("--column", default="address", help="columna con la dirección")
    parser.add_argument("--output-dir", default="output", help="carpeta para el reporte")
    parser.add_argument(
        "--format",
        nargs="+",
        choices=["json", "csv"],
        default=["json", "csv"],
        help="formatos del reporte",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    pipeline = AddressCleanerPipeline(CsvIngester(args.csv, address_column=args.column))

    print(f"Procesando {args.csv}…", file=sys.stderr)
    try:
        report = asyncio.run(pipeline.run(on_result=_print_progress))
    except ProviderAuthError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"No se encontró el archivo: {exc.filename}", file=sys.stderr)
        return 2

    s = report.summary
    print(
        f"\n{s['total']} direcciones · {s['unique_clusters']} ubicaciones únicas · "
        f"{s['flagged']} para revisar · {s['escalated']} escaladas · "
        f"{s['arbiter_consulted']} con LLM · confianza media {s['average_confidence']:.2f}",
        file=sys.stderr,
    )
    for fmt, path in save_report(report, args.output_dir, formats=args.format).items():
        print(f"Reporte {fmt.upper()}: {path}", file=sys.stderr)
    print("Geodatos © OpenStreetMap contributors (ODbL)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
