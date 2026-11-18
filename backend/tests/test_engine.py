from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from anisync.engine import plan_sync
from anisync.providers.base import ListEntry, Status, quantize_score


def entry(media_id, status=Status.WATCHING, **kw):
    return ListEntry(media_id=str(media_id), status=status, **kw)


def test_quantize_score():
    assert quantize_score(None, 10) is None
    assert quantize_score(0, 10) is None
    assert quantize_score(84, 10) == 80
    assert quantize_score(85, 10) == 80  # banker's rounding: 8.5 -> 8
    assert quantize_score(87, 5) == 85
    assert quantize_score(3, 5) == 5  # minimum rating of a provider with step 5


def test_creates_missing_and_updates_changed():
    master = [entry(1, progress=5, score=80), entry(2, Status.COMPLETED)]
    mirror = [entry(101, progress=3, score=80, remote_entry_id="x")]
    plan = plan_sync(master, mirror, {"1": "101", "2": "102"}, score_step=1)
    by_id = {c.media_id: c for c in plan.changes}
    assert by_id["101"].op == "update" and by_id["101"].fields == ["progress"]
    assert by_id["101"].desired.remote_entry_id == "x"
    assert by_id["102"].op == "create"


def test_score_compared_at_mirror_precision():
    # Master 84 vs mirror 80: a mirror with step 10 can't store 84, so there is nothing to change.
    plan = plan_sync([entry(1, score=84)], [entry(1, score=80)], {"1": "1"}, score_step=10)
    assert plan.changes == []


def test_dates_only_pushed_when_master_has_them():
    master = [entry(1)]
    mirror = [entry(1, started_at=date(2020, 1, 1))]
    assert plan_sync(master, mirror, {"1": "1"}, 1).changes == []
    master = [entry(1, started_at=date(2021, 1, 1))]
    assert plan_sync(master, mirror, {"1": "1"}, 1).changes[0].fields == ["started_at"]


def test_unmapped_reported():
    plan = plan_sync([entry(1, title="Obscure OVA")], [], {"1": None}, 1)
    assert plan.changes == [] and plan.unmapped[0].title == "Obscure OVA"


def test_prune_only_when_enabled():
    mirror = [entry(500, external_ids={"a": "9"})]
    assert plan_sync([], mirror, {}, 1).changes == []
    assert plan_sync([], mirror, {}, 1, prune_missing=True).changes[0].op == "delete"


def test_prune_skips_possible_twin_of_unmapped_entry():
    # Master entry 9 couldn't be mapped, but the mirror entry says it is the same title (a:9): keep it.
    master = [entry(9, external_ids={"a": "9"})]
    mirror = [entry(500, external_ids={"a": "9"}), entry(501, external_ids={"a": "10"})]
    plan = plan_sync(master, mirror, {"9": None}, 1, prune_missing=True)
    assert [c.media_id for c in plan.changes] == ["501"]


def test_prune_skips_hintless_entries_when_anything_unmapped():
    plan = plan_sync([entry(9)], [entry(500)], {"9": None}, 1, prune_missing=True)
    assert plan.changes == []


def test_entry_roundtrip():
    e = entry(1, score=70, started_at=date(2022, 2, 3), title="x", external_ids={"a": "1", "b": "7"})
    assert ListEntry.from_dict(e.to_dict()) == e


# ---- property tests ----------------------------------------------------------------------------------------

STEPS = st.sampled_from([1, 5, 10])
dates = st.none() | st.dates(min_value=date(2000, 1, 1), max_value=date(2030, 1, 1))
entries = st.builds(
    ListEntry,
    media_id=st.integers(1, 12).map(str),
    status=st.sampled_from(Status),
    progress=st.integers(0, 30),
    score=st.none() | st.integers(0, 100),
    rewatching=st.booleans(),
    started_at=dates,
    finished_at=dates,
)
lists = st.lists(entries, max_size=12, unique_by=lambda e: e.media_id)


def apply(plan, mirror: list[ListEntry]) -> list[ListEntry]:
    """What the mirror holds after the plan's changes were written (minimal in-memory model of a provider)."""
    by_id = {e.media_id: e for e in mirror}
    for c in plan.changes:
        if c.op == "delete":
            by_id.pop(c.media_id, None)
        else:
            by_id[c.media_id] = c.desired
    return list(by_id.values())


@given(master=lists, mirror=lists, step=STEPS, prune=st.booleans())
def test_applying_a_plan_twice_changes_nothing(master, mirror, step, prune):
    identity = {e.media_id: e.media_id for e in master}
    plan = plan_sync(master, mirror, identity, step, prune)
    after = apply(plan, mirror)
    assert plan_sync(master, after, identity, step, prune).changes == []


@given(master=lists, mirror=lists, step=STEPS)
def test_after_applying_the_plan_the_mirror_matches_the_master(master, mirror, step):
    identity = {e.media_id: e.media_id for e in master}
    after = {e.media_id: e for e in apply(plan_sync(master, mirror, identity, step), mirror)}
    for wanted in master:
        got = after[wanted.media_id]
        assert (got.status, got.progress, got.rewatching) == (wanted.status, wanted.progress, wanted.rewatching)
        assert quantize_score(got.score, step) == quantize_score(wanted.score, step)


@given(master=lists, mirror=lists, step=STEPS)
def test_nothing_is_deleted_unless_pruning(master, mirror, step):
    identity = {e.media_id: e.media_id for e in master}
    assert all(c.op != "delete" for c in plan_sync(master, mirror, identity, step, False).changes)


@given(master=lists, mirror=lists, step=STEPS)
def test_pruning_never_deletes_a_wanted_entry(master, mirror, step):
    identity = {e.media_id: e.media_id for e in master}
    plan = plan_sync(master, mirror, identity, step, True)
    deleted = {c.media_id for c in plan.changes if c.op == "delete"}
    assert not deleted & {e.media_id for e in master}


@given(master=lists, step=STEPS)
def test_a_mirror_that_already_matches_needs_no_changes(master, step):
    identity = {e.media_id: e.media_id for e in master}
    assert plan_sync(master, list(master), identity, step, True).changes == []
