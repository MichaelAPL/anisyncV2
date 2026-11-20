"""Accounts that hide adult titles from other apps (Kitsu's "Adult Content" setting): detect it, tell the user, and
never turn what we can't see into deletions."""

import httpx
import pytest
import respx
from sqlalchemy import select

from anisync import sync
from anisync.models import LinkedAccount
from anisync.providers import kitsu
from anisync.providers.registry import unregister
from tests.fakes import FakeClient, register_fake
from tests.test_ownership import browser
from tests.test_ownership import make_user as make_web_user
from tests.test_sync import make_user, put, remote
from tests.test_sync_snapshots import title

HELP = "Open Settings → Adult Content"


@pytest.fixture
def filtered(fakes):
    """The fake providers, with "fb" now being one whose accounts can hide adult titles."""
    unregister("fb")
    register_fake("fb", score_step=10, adult_help=HELP)


async def account(session, user, provider) -> LinkedAccount:
    row = next(a for a in user.accounts if a.provider == provider)
    await session.refresh(row)
    return row


# ---- detecting it -----------------------------------------------------------------------------------------------


@respx.mock
async def test_the_kitsu_client_reads_the_account_filter():
    for flag in (True, False):
        respx.get(f"{kitsu.API}/users").mock(
            return_value=httpx.Response(
                200, json={"data": [{"id": "1", "attributes": {"name": "k", "sfwFilter": flag}}]}
            )
        )
        assert (await kitsu.KitsuClient("tok").whoami()).hides_adult is flag


async def test_only_providers_that_have_the_setting_carry_help_text(client):
    providers = {p["id"]: p for p in (await client.get("/api/providers")).json()}
    assert "Adult Content" in providers["kitsu"]["adult_help"]
    assert providers["mal"]["adult_help"] is None and providers["anilist"]["adult_help"] is None


@pytest.mark.usefixtures("fakes", "client")
async def test_me_tells_the_ui_which_accounts_hide_adult_titles(session):
    user, accounts, token = await make_web_user(session, "adult")
    first = next(iter(accounts))
    accounts[first].hides_adult = True
    await session.flush()
    async with browser(token) as c:
        flags = {a["provider"]: a["hides_adult"] for a in (await c.get("/api/me")).json()["accounts"]}
    assert flags[accounts[first].provider] is True and list(flags.values()).count(False) == len(flags) - 1


async def test_a_manual_sync_looks_again_so_turning_the_setting_off_takes_effect(session, filtered):
    user = await make_user(session)
    put("fa", title(1))
    FakeClient.HIDES_ADULT = {"fb"}
    await sync.run_sync(session, user.id, "manual")
    assert (await account(session, user, "fb")).hides_adult is True
    FakeClient.HIDES_ADULT = set()
    await sync.run_sync(session, user.id, "scheduled")  # a scheduled sync doesn't spend a request on it
    assert (await account(session, user, "fb")).hides_adult is True
    await sync.run_sync(session, user.id, "manual")
    assert (await account(session, user, "fb")).hides_adult is False


async def test_a_preview_also_looks_again(session, filtered):
    user = await make_user(session)
    put("fa", title(1))
    FakeClient.HIDES_ADULT = {"fb"}
    await sync.run_sync(session, user.id, "preview", dry_run=True)
    assert (await account(session, user, "fb")).hides_adult is True


async def test_a_provider_without_the_setting_costs_no_extra_request(session, fakes):
    user = await make_user(session)  # no adult_help on the fakes
    put("fa", title(1))
    FakeClient.HIDES_ADULT = {"fb"}  # would show up if anyone asked
    await sync.run_sync(session, user.id, "manual")
    assert (await account(session, user, "fb")).hides_adult is False


async def test_a_skipped_title_proves_the_account_hides_titles(session, fakes):
    user = await make_user(session)
    put("fa", title(1))
    FakeClient.SKIP_ON = {"b1"}
    await sync.run_sync(session, user.id, "scheduled")
    assert (await account(session, user, "fb")).hides_adult is True


# ---- never delete what we can't see -----------------------------------------------------------------------------


@pytest.fixture
def filtered_master(fakes):
    unregister("fa")
    register_fake("fa", score_step=1, incremental=True, adult_help=HELP)


async def test_a_master_that_hides_titles_never_causes_deletions_on_the_mirror(session, filtered_master):
    for hides, expected in ((False, ["b1"]), (True, ["b1", "b7"])):
        FakeClient.REMOTE.clear()
        FakeClient.HIDES_ADULT = {"fa"} if hides else set()
        user = await make_user(session, prune=True)
        put("fa", title(1))
        put("fb", title(7, "fb"))  # on the mirror only: is it removed from the master, or just hidden from us?
        await sync.run_sync(session, user.id, "manual")
        assert sorted(remote("fb")) == expected, f"master hides adult titles: {hides}"
        await session.delete(user)
        await session.flush()


async def test_a_removal_edit_is_not_copied_when_the_master_hides_titles(session, filtered_master):
    for hides, expected in ((False, []), (True, ["b1"])):
        FakeClient.REMOTE.clear()
        user = await make_user(session, prune=True)
        user.master.hides_adult = hides
        await session.flush()
        put("fb", title(1, "fb"))
        async with sync.IdMapper(session) as mapper:
            await mapper.remember({"fa": "a1", "fb": "b1"})
        await sync.push_entry(session, user.mirrors[0].id, "fa", "a1", None)
        assert sorted(remote("fb")) == expected, f"hides={hides}"
        await session.delete(user)
        await session.flush()


@pytest.mark.usefixtures("fakes")
async def test_the_accounts_still_load_for_users_linked_before_the_flag_existed(session):
    user = await make_user(session)
    rows = (await session.scalars(select(LinkedAccount).where(LinkedAccount.user_id == user.id))).all()
    assert rows and all(a.hides_adult is False for a in rows)
