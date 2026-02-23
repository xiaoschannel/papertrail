from datetime import datetime, timedelta

from models import ReviewDecision

MAX_GAP = timedelta(minutes=5)


def parse_verdict_datetime(date_str: str, time_str: str) -> datetime | None:
    if not date_str or date_str.count("-") != 2:
        return None
    parts = date_str.split("-")
    if not all(p.isdigit() for p in parts):
        return None
    if not time_str or ":" not in time_str:
        return None
    tparts = time_str.split(":")
    if len(tparts) < 2 or not tparts[0].isdigit() or not tparts[1].isdigit():
        return None
    year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    hour, minute = int(tparts[0]), int(tparts[1])
    return datetime(year, month, day, hour, minute)


def _build_document_timeline(
    decisions: dict[str, ReviewDecision],
) -> list[tuple[str, datetime]]:
    documents: list[tuple[str, datetime]] = []
    for fn, dec in decisions.items():
        if dec.verdict == "tossed" or dec.document_type != "receipt":
            continue
        dt = parse_verdict_datetime(dec.date, dec.time)
        if dt is not None:
            documents.append((fn, dt))
    documents.sort(key=lambda x: x[1])
    return documents


def find_dedupe_clusters(
    decisions: dict[str, ReviewDecision],
) -> list[list[str]]:
    documents = _build_document_timeline(decisions)

    time_clusters: list[list[str]] = []
    current_cluster: list[str] = []
    prev_dt: datetime | None = None
    for fn, dt in documents:
        if prev_dt is not None and (dt - prev_dt) <= MAX_GAP:
            current_cluster.append(fn)
        else:
            if len(current_cluster) >= 2:
                time_clusters.append(current_cluster)
            current_cluster = [fn]
        prev_dt = dt
    if len(current_cluster) >= 2:
        time_clusters.append(current_cluster)

    clusters: list[list[str]] = []
    for tc in time_clusters:
        by_cost: dict[float, list[str]] = {}
        for fn in tc:
            by_cost.setdefault(decisions[fn].cost, []).append(fn)
        for group in by_cost.values():
            if len(group) >= 2:
                clusters.append(group)

    return clusters


def find_adjacent_documents(
    date_str: str,
    time_str: str,
    cost: float,
    decisions: dict[str, ReviewDecision],
    exclude_fn: str = "",
) -> list[str]:
    target_dt = parse_verdict_datetime(date_str, time_str)
    if target_dt is None:
        return []

    adjacent: list[tuple[str, datetime]] = []
    for fn, dec in decisions.items():
        if fn == exclude_fn:
            continue
        if dec.verdict == "tossed" or dec.document_type != "receipt":
            continue
        dt = parse_verdict_datetime(dec.date, dec.time)
        if dt is not None and abs(dt - target_dt) <= MAX_GAP and dec.cost == cost:
            adjacent.append((fn, dt))

    adjacent.sort(key=lambda x: abs(x[1] - target_dt))
    return [fn for fn, _ in adjacent]
