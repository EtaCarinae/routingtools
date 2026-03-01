from __future__ import annotations

from routetools.address_cleaner.models import NormalizedAddress


def assign_cluster_ids(
    results: list[NormalizedAddress],
    decimal_places: int = 5,
) -> list[NormalizedAddress]:
    """Group results by rounded (lat, lon) and assign a cluster_id string.

    Addresses without coordinates are each placed in their own singleton cluster.
    """
    cluster_map: dict[str, str] = {}

    for result in results:
        geo = result.geocode
        if geo.lat is not None and geo.lon is not None:
            key = f"{geo.lat:.{decimal_places}f},{geo.lon:.{decimal_places}f}"
        else:
            # Unique key per address so they don't collapse together
            key = f"no_coords:{result.original}"

        if key not in cluster_map:
            cluster_map[key] = f"cluster_{len(cluster_map):04d}"

        result.dedupe_cluster_id = cluster_map[key]

    return results
