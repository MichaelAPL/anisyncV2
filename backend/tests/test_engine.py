from datetime import date

from anisync.engine import plan_sync
from anisync.services.base import ListEntry, Status, quantize_score


def entry(media_id, status=Status.WATCHING, **kw):
    return ListEntry(media_id=media_id, status=status, **kw)


def test_quantize_score():
    assert quantize_score(None, 10) is None
    assert quantize_score(0, 10) is None
    assert quantize_score(84, 10) == 80
    assert quantize_score(85, 10) == 80  # banker's rounding: 8.5 -> 8
    assert quantize_score(87, 5) == 85
    assert quantize_score(3, 5) == 5  # Kitsu minimum rating


def test_creates_missing_and_updates_changed():
    master = [entry(1, progress=5, score=80), entry(2, Status.COMPLETED)]
    mirror = [entry(101, progress=3, score=80, remote_entry_id="x")]
    plan = plan_sync(master, mirror, {1: 101, 2: 102}, score_step=1)
    by_id = {c.media_id: c for c in plan.changes}
    assert by_id[101].op == "update" and by_id[101].fields == ["progress"]
    assert by_id[101].desired.remote_entry_id == "x"
    assert by_id[102].op == "create"


def test_score_compared_at_mirror_precision():
    # AniList master 84 vs MAL mirror 8 (=80): MAL can't store 84, so no change.
    plan = plan_sync([entry(1, score=84)], [entry(1, score=80)], {1: 1}, score_step=10)
    assert plan.changes == []


def test_dates_only_pushed_when_master_has_them():
    master = [entry(1)]
    mirror = [entry(1, started_at=date(2020, 1, 1))]
    assert plan_sync(master, mirror, {1: 1}, 1).changes == []
    master = [entry(1, started_at=date(2021, 1, 1))]
    assert plan_sync(master, mirror, {1: 1}, 1).changes[0].fields == ["started_at"]


def test_unmapped_reported():
    plan = plan_sync([entry(1, title="Obscure OVA")], [], {1: None}, 1)
    assert plan.changes == [] and plan.unmapped[0].title == "Obscure OVA"


def test_prune_only_when_enabled():
    mirror = [entry(500, mal_id=9)]
    assert plan_sync([], mirror, {}, 1).changes == []
    assert plan_sync([], mirror, {}, 1, prune_missing=True).changes[0].op == "delete"


def test_prune_skips_possible_twin_of_unmapped_entry():
    # Master MAL entry 9 couldn't be mapped, but the mirror entry says it is MAL 9: keep it.
    master = [entry(9, mal_id=9)]
    mirror = [entry(500, mal_id=9), entry(501, mal_id=10)]
    plan = plan_sync(master, mirror, {9: None}, 1, prune_missing=True)
    assert [c.media_id for c in plan.changes] == [501]


def test_prune_skips_hintless_entries_when_anything_unmapped():
    plan = plan_sync([entry(9)], [entry(500)], {9: None}, 1, prune_missing=True)
    assert plan.changes == []


def test_entry_roundtrip():
    e = entry(1, score=70, started_at=date(2022, 2, 3), title="x")
    assert ListEntry.from_dict(e.to_dict()) == e
