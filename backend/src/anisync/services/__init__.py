from anisync.models import Service
from anisync.services import anilist, kitsu, mal
from anisync.services.base import ServiceClient

CLIENTS: dict[Service, type[ServiceClient]] = {
    Service.MAL: mal.MalClient,
    Service.ANILIST: anilist.AniListClient,
    Service.KITSU: kitsu.KitsuClient,
}
