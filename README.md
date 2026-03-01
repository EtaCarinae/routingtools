# routetools

Herramientas de ruteo para reparto. Primer módulo: **`address_cleaner`** — limpia, normaliza, geocodifica y deduplica listas de direcciones españolas.

## Cómo funciona

```
Dirección original
  → normalizer      strip · lowercase · abreviaciones ES · NFC · espacios
  → Nominatim       geocodificación gratuita (countrycodes=es, 1 req/s)
  → quality.score() puntuación 0.0–1.0

  ≥ 0.80  → acepta Nominatim
  0.50–0.80 → LLM árbitro (OpenRouter) → acepta / escala / flag
  < 0.50  → escala a OpenCage

  → deduplicador    agrupa por (lat, lon) a 5 decimales
  → CleaningReport  JSON y/o CSV con mapeo original → normalizada
```

## Instalación

Requiere [uv](https://docs.astral.sh/uv/).

```bash
git clone git@github.com:EtaCarinae/routingtools.git
cd routingtools
uv sync --extra dev
```

## Configuración

```bash
cp .env.example .env
```

Edita `.env`:

```env
NOMINATIM_USER_AGENT=routetools/1.0 (tuemail@ejemplo.com)   # obligatorio
OPENCAGE_API_KEY=...        # escalado (2.500 req/día gratis, sin tarjeta)
OPENROUTER_API_KEY=...      # árbitro LLM (tier gratuito disponible)
```

Obtener claves:
- **OpenCage**: [opencagedata.com/users/sign_up](https://opencagedata.com/users/sign_up)
- **OpenRouter**: [openrouter.ai](https://openrouter.ai) → Keys → Create Key

Ajustes adicionales en `config/settings.yaml` (umbrales, modelo LLM, abreviaciones).

## Uso

### CSV

El CSV debe tener una columna con las direcciones (por defecto `address`):

```csv
address
"C/ Mayor 10, Madrid"
"Avda. de la Constitución 5, Sevilla"
```

### Ejecutar el pipeline

```python
import asyncio
from routetools.address_cleaner.pipeline import AddressCleanerPipeline
from routetools.address_cleaner.ingestion.csv_ingester import CsvIngester
from routetools.address_cleaner.reporter import report_to_json, save_report

async def main():
    pipeline = AddressCleanerPipeline(
        CsvIngester("direcciones.csv", address_column="address")
    )
    report = await pipeline.run()

    # Imprimir JSON
    print(report_to_json(report))

    # Guardar JSON + CSV
    save_report(report, output_dir="output", formats=["json", "csv"])

    # Mapeo original → resultado
    mapping = {r.original: r for r in report.results}

asyncio.run(main())
```

```bash
uv run python mi_script.py
```

### Output

El `CleaningReport` incluye por cada dirección:

| Campo | Descripción |
|---|---|
| `original` | Dirección tal como vino |
| `pre_normalized` | Tras normalización textual |
| `geocode.formatted_address` | Dirección geocodificada |
| `geocode.lat` / `lon` | Coordenadas |
| `geocode.source` | `nominatim` · `opencage` · `failed` |
| `confidence` | Score final 0.0–1.0 |
| `flagged` | Necesita revisión humana |
| `flag_reason` | Por qué fue flaggeada |
| `escalated_to_google` | Si se usó OpenCage como escalado |
| `arbiter_consulted` | Si intervino el LLM árbitro |
| `dedupe_cluster_id` | ID de cluster de duplicados |

## Desarrollo

```bash
uv run ruff check .      # lint
uv run pytest tests/ -v  # tests (22)
```

## Estructura

```
routetools/
├── config/settings.yaml          # umbrales, modelo LLM, abreviaciones ES
├── routetools/address_cleaner/
│   ├── models.py                 # tipos Pydantic
│   ├── config.py                 # settings (.env + YAML)
│   ├── normalizer.py             # normalización textual
│   ├── pipeline.py               # orquestación completa
│   ├── deduplicator.py
│   ├── reporter.py               # JSON / CSV output
│   ├── ingestion/
│   │   ├── base.py               # AddressIngester ABC
│   │   └── csv_ingester.py
│   ├── geocoding/
│   │   ├── base.py               # Geocoder ABC
│   │   ├── quality.py            # scoring ponderado
│   │   ├── nominatim.py          # rate-limited, countrycodes=es
│   │   └── opencage.py           # escalado
│   └── arbiter/
│       └── llm_arbiter.py        # OpenRouter, retry + fallback
└── tests/
```

## Scoring de calidad

| Componente | Peso |
|---|---|
| `importance` (Nominatim) | 0.35 |
| Tipo de resultado (house/road/suburb/city) | 0.30 |
| Área del bounding box | 0.20 |
| Número de portal resuelto | 0.10 |
| Penalización por ambigüedad (>1 candidato) | −0.15 |

Score máximo alcanzable: **0.95**.
