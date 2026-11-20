"""Pure diff logic: given the master list and a mirror list, decide what to write to the mirror.

No I/O here so it's trivial to unit test.
"""

from dataclasses import dataclass, field, replace
from typing import Literal

from anisync.providers.base import ListEntry, quantize_score

SYNCED_FIELDS = ("status", "progress", "score", "rewatching", "started_at", "finished_at")


@dataclass
class Change:
    op: Literal["create", "update", "delete"]
    media_id: str  # ID on the mirror provider
    desired: ListEntry | None
    current: ListEntry | None
    fields: list[str] = field(default_factory=list)


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    unmapped: list[ListEntry] = field(default_factory=list)  # master entries with no ID on the mirror
    adult_skipped: int = 0  # adult master entries left out because the mirror asked for that

    def counts(self) -> dict[str, int]:
        out = {"create": 0, "update": 0, "delete": 0}
        for c in self.changes:
            out[c.op] += 1
        out["unmapped"] = len(self.unmapped)
        return out


def changed_fields(master: ListEntry, mirror: ListEntry, score_step: int) -> list[str]:
    diffs = []
    if master.status != mirror.status:
        diffs.append("status")
    if master.progress != mirror.progress:
        diffs.append("progress")
    if quantize_score(master.score, score_step) != quantize_score(mirror.score, score_step):
        diffs.append("score")
    if master.rewatching != mirror.rewatching:
        diffs.append("rewatching")
    # Dates are only pushed when the master has them (providers disagree on partial dates).
    if master.started_at and master.started_at != mirror.started_at:
        diffs.append("started_at")
    if master.finished_at and master.finished_at != mirror.finished_at:
        diffs.append("finished_at")
    return diffs


def plan_sync(
    master: list[ListEntry],
    mirror: list[ListEntry],
    id_map: dict[str, str | None],
    score_step: int,
    prune_missing: bool = False,
    skip_adult: bool = False,
    protect_adult: bool = False,
) -> Plan:
    """id_map: master media_id -> mirror media_id (None = the title has no ID on the mirror's provider)."""
    plan = Plan()
    mirror_by_id = {e.media_id: e for e in mirror}
    wanted: set[str] = set()

    for entry in master:
        target_id = id_map.get(entry.media_id)
        if skip_adult and entry.adult:
            # Not copied, but never a reason to delete the mirror's own copy.
            plan.adult_skipped += 1
            if target_id is not None:
                wanted.add(target_id)
            continue
        if target_id is None:
            plan.unmapped.append(entry)
            continue
        wanted.add(target_id)
        current = mirror_by_id.get(target_id)
        desired = replace(entry, media_id=target_id, remote_entry_id=current.remote_entry_id if current else None)
        if current is None:
            plan.changes.append(Change("create", target_id, desired, None, list(SYNCED_FIELDS)))
        elif diffs := changed_fields(entry, current, score_step):
            plan.changes.append(Change("update", target_id, desired, current, diffs))

    if prune_missing:
        # Never delete a mirror entry that might be the twin of an unmapped master entry.
        master_hints = {h for e in master for h in _hints(e)}
        for media_id, current in mirror_by_id.items():
            if media_id in wanted or master_hints & _hints(current):
                continue
            if protect_adult and current.adult:
                continue  # the master ignores adult titles: their absence there says nothing about them
            if plan.unmapped and not _hints(current):
                continue
            plan.changes.append(Change("delete", media_id, None, current))
    return plan


def _hints(e: ListEntry) -> set[tuple[str, str]]:
    """(provider, title ID) pairs an entry is known by; two entries sharing one are the same title."""
    return set(e.external_ids.items())
