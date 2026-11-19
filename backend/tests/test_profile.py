"""Editable display name and profile picture (picked from a linked provider, never following the master)."""

import httpx
import pytest
import respx
from sqlalchemy import select

from anisync.models import User
from anisync.providers import anilist, get_provider, kitsu, mal
from tests.fakes import FakeClient
from tests.test_auth_and_api import query
from tests.test_ownership import browser, make_user

# make_user links the fake providers "fa" and "fb"; `client` points the app at the rolled-back test session.
usesfakes = pytest.mark.usefixtures("fakes", "client")
PIC_A = "https://img.example.test/a/me.png"
PIC_B = "https://img.example.test/b/me.png"


# ---- which image URLs are accepted ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://s4.anilist.co/file/anilistcdn/user/avatar/large/b1.png", True),
        ("https://anilist.co/a.png", True),
        ("http://s4.anilist.co/a.png", False),  # not https
        ("https://anilist.co.evil.example/a.png", False),  # the provider's name as a prefix of someone else's domain
        ("https://evilanilist.co/a.png", False),  # a lookalike without the dot
        ("https://evil.example/anilist.co/a.png", False),
        ("https://user@evil.example/a.png", False),
        ("javascript:alert(1)", False),
        ("data:image/png;base64,AAAA", False),
        ("//s4.anilist.co/a.png", False),
        ("https://s4.anilist.co/" + "a" * 600, False),  # too long to store
        ("", False),
        (None, False),
    ],
)
def test_only_https_urls_on_the_providers_own_domains_are_accepted(url, ok):
    provider = get_provider("anilist")
    assert provider.safe_image_url(url) == (url if ok else None)


def test_every_real_provider_declares_its_image_domains():
    for provider_id in ("mal", "anilist", "kitsu"):
        assert get_provider(provider_id).image_hosts, provider_id


# ---- what the real clients read -------------------------------------------------------------------------------


@respx.mock
async def test_the_clients_read_the_profile_picture_from_each_providers_response():
    respx.get(f"{mal.API}/users/@me").mock(
        side_effect=[
            httpx.Response(200, json={"id": 1, "name": "m", "picture": "https://cdn.myanimelist.net/u/1.jpg"}),
            httpx.Response(200, json={"id": 1, "name": "m"}),  # no picture set
        ]
    )
    client = get_provider("mal").client("token")
    assert (await client.whoami()).avatar_url == "https://cdn.myanimelist.net/u/1.jpg"
    assert (await client.whoami()).avatar_url is None

    def viewer(avatar):
        return httpx.Response(200, json={"data": {"Viewer": {"id": 5, "name": "a", "avatar": avatar}}})

    respx.post(anilist.GRAPHQL_URL).mock(
        side_effect=[
            viewer({"large": "https://s4.anilist.co/file/anilistcdn/user/avatar/large/b5.png"}),
            viewer({"large": "https://s4.anilist.co/file/anilistcdn/user/avatar/large/default.png"}),
            viewer(None),
        ]
    )
    client = get_provider("anilist").client("token")
    assert (await client.whoami()).avatar_url.endswith("/large/b5.png")
    assert (await client.whoami()).avatar_url is None  # AniList's placeholder isn't a picture
    assert (await client.whoami()).avatar_url is None

    def person(avatar):
        return httpx.Response(200, json={"data": [{"id": "9", "attributes": {"name": "k", "avatar": avatar}}]})

    respx.get(f"{kitsu.API}/users").mock(
        side_effect=[person({"large": "https://media.kitsu.app/users/avatars/9/large.jpeg"}), person(None)]
    )
    client = get_provider("kitsu").client("token")
    assert (await client.whoami()).avatar_url == "https://media.kitsu.app/users/avatars/9/large.jpeg"
    assert (await client.whoami()).avatar_url is None


# ---- sign-in ---------------------------------------------------------------------------------------------------


async def test_a_new_user_starts_with_the_picture_of_the_account_they_signed_in_with(client, session):
    login = await client.get("/auth/mal/login")
    state = query(login)["state"][0]
    with respx.mock:
        respx.post(mal.TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "t", "refresh_token": "r", "expires_in": 3600})
        )
        respx.get(f"{mal.API}/users/@me").mock(
            return_value=httpx.Response(
                200, json={"id": 77, "name": "kaito", "picture": "https://cdn.myanimelist.net/images/77.jpg"}
            )
        )
        await client.get(f"/auth/mal/callback?code=abc&state={state}")
    me = (await client.get("/api/me")).json()
    assert (me["avatar_url"], me["avatar_provider"]) == ("https://cdn.myanimelist.net/images/77.jpg", "mal")
    assert me["accounts"][0]["avatar_url"] == me["avatar_url"]


async def test_a_picture_from_another_domain_is_never_stored(client, session):
    login = await client.get("/auth/mal/login")
    state = query(login)["state"][0]
    with respx.mock:
        respx.post(mal.TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "t", "refresh_token": "r", "expires_in": 3600})
        )
        respx.get(f"{mal.API}/users/@me").mock(
            return_value=httpx.Response(200, json={"id": 78, "name": "x", "picture": "https://tracker.example/p.gif"})
        )
        await client.get(f"/auth/mal/callback?code=abc&state={state}")
    me = (await client.get("/api/me")).json()
    assert me["avatar_url"] is None and me["accounts"][0]["avatar_url"] is None


# ---- display name ----------------------------------------------------------------------------------------------


@usesfakes
async def test_the_user_can_rename_themselves(session):
    user, _, token = await make_user(session, "n")
    async with browser(token) as c:
        response = await c.patch("/api/me", json={"display_name": "  Kaito   the  Great "})
    assert response.status_code == 200 and response.json()["display_name"] == "Kaito the Great"
    await session.refresh(user)
    assert user.display_name == "Kaito the Great"


@pytest.mark.parametrize("name", ["", "   ", "x" * 41, "tab\there", "line\nbreak", "bell\x07", "zero​width"])
@usesfakes
async def test_bad_names_are_refused(session, name):
    user, _, token = await make_user(session, "n")
    async with browser(token) as c:
        assert (await c.patch("/api/me", json={"display_name": name})).status_code == 422
    await session.refresh(user)
    assert user.display_name == "user-n"


@usesfakes
async def test_renaming_is_rate_limited(session):
    _, _, token = await make_user(session, "n")
    async with browser(token) as c:
        codes = [(await c.patch("/api/me", json={"display_name": f"name {i}"})).status_code for i in range(22)]
    assert codes[:20] == [200] * 20 and codes[20] == 429


@usesfakes
async def test_renaming_needs_a_signed_in_user(client):
    assert (await client.patch("/api/me", json={"display_name": "x"})).status_code == 401


# ---- choosing the picture ---------------------------------------------------------------------------------------


async def start(session, **avatars):
    FakeClient.AVATARS.update(avatars)
    user, accounts, token = await make_user(session, "p")
    return user, accounts, token


@usesfakes
async def test_choosing_an_account_copies_its_current_picture_from_the_provider(session):
    user, accounts, token = await start(session, fa=PIC_A, fb=PIC_B)
    async with browser(token) as c:
        me = (await c.put("/api/me/avatar", json={"account_id": accounts["fb"].id})).json()
    assert (me["avatar_url"], me["avatar_provider"]) == (PIC_B, "fb")
    assert {a["provider"]: a["avatar_url"] for a in me["accounts"]}["fb"] == PIC_B


@usesfakes
async def test_the_picture_does_not_follow_a_change_of_master(session):
    user, accounts, token = await start(session, fa=PIC_A, fb=PIC_B)
    async with browser(token) as c:
        await c.put("/api/me/avatar", json={"account_id": accounts["fa"].id})
        await c.put("/api/me/master", json={"account_id": accounts["fb"].id})
        me = (await c.get("/api/me")).json()
    assert me["master_account_id"] == accounts["fb"].id
    assert (me["avatar_url"], me["avatar_provider"]) == (PIC_A, "fa")


@usesfakes
async def test_choosing_the_master_again_picks_up_a_picture_changed_on_the_provider(session):
    user, accounts, token = await start(session, fa=PIC_A)
    async with browser(token) as c:
        await c.put("/api/me/avatar", json={"account_id": accounts["fa"].id})
        FakeClient.AVATARS["fa"] = "https://img.example.test/a/new.png"
        me = (await c.put("/api/me/avatar", json={"account_id": accounts["fa"].id})).json()
    assert me["avatar_url"] == "https://img.example.test/a/new.png"


@usesfakes
async def test_an_account_without_a_picture_changes_nothing(session):
    user, accounts, token = await start(session, fa=PIC_A, fb=None)
    async with browser(token) as c:
        await c.put("/api/me/avatar", json={"account_id": accounts["fa"].id})
        response = await c.put("/api/me/avatar", json={"account_id": accounts["fb"].id})
        me = (await c.get("/api/me")).json()
    assert response.status_code == 422 and me["avatar_url"] == PIC_A


@usesfakes
async def test_a_picture_on_a_foreign_domain_from_a_provider_is_refused(session):
    user, accounts, token = await start(session, fa="https://tracker.example/pixel.gif")
    async with browser(token) as c:
        assert (await c.put("/api/me/avatar", json={"account_id": accounts["fa"].id})).status_code == 422
    await session.refresh(user)
    assert user.avatar_url is None


@usesfakes
async def test_other_peoples_accounts_cannot_be_used(session):
    _, _, token = await start(session, fa=PIC_A)
    other, other_accounts, _ = await make_user(session, "other")
    async with browser(token) as c:
        assert (await c.put("/api/me/avatar", json={"account_id": other_accounts["fa"].id})).status_code == 404
        assert (await c.put("/api/me/avatar", json={"account_id": 999999})).status_code == 404
    assert (await session.scalar(select(User).where(User.id == other.id))).avatar_url is None


@usesfakes
async def test_a_provider_that_needs_relinking_is_reported(session):
    user, accounts, token = await start(session, fa=PIC_A)
    FakeClient.REJECT_TOKENS = True
    async with browser(token) as c:
        response = await c.put("/api/me/avatar", json={"account_id": accounts["fa"].id})
    assert response.status_code == 409


@usesfakes
async def test_the_picture_can_be_removed(session):
    user, accounts, token = await start(session, fa=PIC_A)
    async with browser(token) as c:
        await c.put("/api/me/avatar", json={"account_id": accounts["fa"].id})
        me = (await c.delete("/api/me/avatar")).json()
    assert me["avatar_url"] is None and me["avatar_provider"] is None
    assert me["accounts"][0]["avatar_url"] == PIC_A  # the account's own picture is still known for choosing again
