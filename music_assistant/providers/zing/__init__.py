"""Zing music provider for Music Assistant."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from aiohttp import ClientResponseError
from music_assistant_models.enums import (
    ContentType,
    MediaType,
    ProviderFeature,
    StreamType,
)
from music_assistant_models.errors import ProviderUnavailableError
from music_assistant_models.media_items import (
    Album,
    Artist,
    AudioFormat,
    Playlist,
    ProviderMapping,
    Radio,
    SearchResults,
    Track,
    UniqueList,
)
from music_assistant_models.streamdetails import StreamDetails

from music_assistant.models.music_provider import MusicProvider

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType

API_URL = "http://jewishmusic.fm:4000/graphql"


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize provider(instance) with given configuration."""
    return ZingProvider(mass, manifest, config)


class ZingProvider(MusicProvider):
    """Support for the Zing GraphQL music provider."""

    api_url: str = API_URL

    @property
    def supported_features(self) -> set[ProviderFeature]:
        """Return the features supported by this provider."""
        return {
            ProviderFeature.SEARCH,
            ProviderFeature.LIBRARY_ARTISTS,
            ProviderFeature.LIBRARY_ALBUMS,
            ProviderFeature.LIBRARY_TRACKS,
            ProviderFeature.LIBRARY_PLAYLISTS,
            ProviderFeature.LIBRARY_RADIOS,
            ProviderFeature.ARTIST_ALBUMS,
            ProviderFeature.ARTIST_TOPTRACKS,
            ProviderFeature.SIMILAR_TRACKS,
        }

    async def _graphql(self, query: str, variables: dict[str, Any] | None = None) -> Any:
        """Perform a GraphQL request."""
        variables = variables or {}
        try:
            async with self.mass.http_session.post(
                self.api_url, json={"query": query, "variables": variables}
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except ClientResponseError as err:
            raise ProviderUnavailableError(str(err)) from err
        if "errors" in data:
            raise ProviderUnavailableError(data["errors"][0].get("message", "unknown error"))
        return data.get("data")

    async def search(
        self, search_query: str, media_types: list[MediaType], limit: int = 5
    ) -> SearchResults:
        """Perform a search on the provider."""
        query = """
        query Search($query: String!, $limit: Int!) {
            search(query: $query, limit: $limit) {
                tracks { id title duration url album { id title } artists { id name } }
                albums { id title artists { id name } }
                artists { id name }
            }
        }
        """
        data = await self._graphql(query, {"query": search_query, "limit": limit})
        results = SearchResults()
        if not data or "search" not in data:
            return results
        search_data = data["search"]
        if MediaType.TRACK in media_types and search_data.get("tracks"):
            results.tracks = [self._parse_track(item) for item in search_data["tracks"]][:limit]
        if MediaType.ALBUM in media_types and search_data.get("albums"):
            results.albums = [self._parse_album(item) for item in search_data["albums"]][:limit]
        if MediaType.ARTIST in media_types and search_data.get("artists"):
            results.artists = [self._parse_artist(item) for item in search_data["artists"]][:limit]
        return results

    def _parse_artist(self, data: dict[str, Any]) -> Artist:
        return Artist(
            item_id=str(data["id"]),
            provider=self.instance_id,
            name=data.get("name") or "Unknown Artist",
            provider_mappings={
                ProviderMapping(
                    item_id=str(data["id"]),
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                )
            },
        )

    def _parse_album(self, data: dict[str, Any]) -> Album:
        artists = [self._parse_artist(art) for art in data.get("artists", [])]
        return Album(
            item_id=str(data["id"]),
            provider=self.instance_id,
            name=data.get("title") or "Unknown Album",
            artists=UniqueList(artists),
            provider_mappings={
                ProviderMapping(
                    item_id=str(data["id"]),
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                )
            },
        )

    def _parse_track(self, data: dict[str, Any]) -> Track:
        artists = [self._parse_artist(art) for art in data.get("artists", [])]
        album = self._parse_album(data["album"]) if data.get("album") else None
        return Track(
            item_id=str(data["id"]),
            provider=self.instance_id,
            name=data.get("title") or "Unknown Track",
            duration=int(data.get("duration") or 0),
            artists=UniqueList(artists),
            album=album,
            provider_mappings={
                ProviderMapping(
                    item_id=str(data["id"]),
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                )
            },
        )

    async def get_track(self, prov_track_id: str) -> Track:
        """Return full track details."""
        query = """
        query GetTrack($id: ID!) {
            track(id: $id) {
                id title duration url album { id title } artists { id name }
            }
        }
        """
        data = await self._graphql(query, {"id": prov_track_id})
        track_data = data.get("track") if data else None
        if not track_data:
            raise ProviderUnavailableError(f"Track {prov_track_id} not found")
        return self._parse_track(track_data)

    async def get_stream_details(self, item_id: str, media_type: MediaType) -> StreamDetails:
        """Return stream details for a track."""
        track = await self.get_track(item_id)
        return StreamDetails(
            item_id=track.item_id,
            provider=self.instance_id,
            audio_format=AudioFormat(content_type=ContentType.UNKNOWN),
            stream_type=StreamType.HTTP,
            path=(
                track.provider_mappings[0].item_id
                if hasattr(track.provider_mappings, "__getitem__")
                else track.item_id
            ),
            duration=track.duration,
            can_seek=True,
            allow_seek=True,
        )

    # Library queries
    async def get_library_artists(self) -> AsyncGenerator[Artist, None]:
        """Retrieve all artists from the library."""
        query = """
        query { artists { id name } }
        """
        data = await self._graphql(query)
        for item in data.get("artists", []):
            yield self._parse_artist(item)

    async def get_library_albums(self) -> AsyncGenerator[Album, None]:
        """Retrieve all albums from the library."""
        query = """
        query { albums { id title artists { id name } } }
        """
        data = await self._graphql(query)
        for item in data.get("albums", []):
            yield self._parse_album(item)

    async def get_library_tracks(self) -> AsyncGenerator[Track, None]:
        """Retrieve all tracks from the library."""
        query = """
        query { tracks { id title duration url album { id title } artists { id name } } }
        """
        data = await self._graphql(query)
        for item in data.get("tracks", []):
            yield self._parse_track(item)

    async def get_library_playlists(self) -> AsyncGenerator[Playlist, None]:
        """Retrieve all playlists from the library."""
        query = """
        query { playlists { id title } }
        """
        data = await self._graphql(query)
        for item in data.get("playlists", []):
            yield self._parse_playlist(item)

    async def get_library_radios(self) -> AsyncGenerator[Radio, None]:
        """Retrieve all radio stations from the library."""
        query = """
        query { radios { id title url } }
        """
        data = await self._graphql(query)
        for item in data.get("radios", []):
            yield self._parse_radio(item)

    async def get_artist(self, prov_artist_id: str) -> Artist:
        """Return full artist details."""
        query = """
        query GetArtist($id: ID!) { artist(id: $id) { id name } }
        """
        data = await self._graphql(query, {"id": prov_artist_id})
        artist_data = data.get("artist") if data else None
        if not artist_data:
            raise ProviderUnavailableError(f"Artist {prov_artist_id} not found")
        return self._parse_artist(artist_data)

    async def get_artist_albums(self, prov_artist_id: str) -> list[Album]:
        """Return albums for the given artist."""
        query = """
        query GetArtistAlbums($id: ID!) {
            artist(id: $id) {
                albums { id title artists { id name } }
            }
        }
        """
        data = await self._graphql(query, {"id": prov_artist_id})
        artist = data.get("artist") if data else None
        if not artist:
            return []
        return [self._parse_album(item) for item in artist.get("albums", [])]

    async def get_artist_toptracks(self, prov_artist_id: str) -> list[Track]:
        """Return top tracks for the given artist."""
        query = """
        query GetArtistTop($id: ID!) {
            artist(id: $id) {
                topTracks { id title duration url album { id title } artists { id name } }
            }
        }
        """
        data = await self._graphql(query, {"id": prov_artist_id})
        artist = data.get("artist") if data else None
        if not artist:
            return []
        return [self._parse_track(item) for item in artist.get("topTracks", [])]

    async def get_album(self, prov_album_id: str) -> Album:
        """Return full album details."""
        query = """
        query GetAlbum($id: ID!) { album(id: $id) { id title artists { id name } } }
        """
        data = await self._graphql(query, {"id": prov_album_id})
        album_data = data.get("album") if data else None
        if not album_data:
            raise ProviderUnavailableError(f"Album {prov_album_id} not found")
        return self._parse_album(album_data)

    async def get_album_tracks(self, prov_album_id: str) -> list[Track]:
        """Return the tracks for the given album."""
        query = """
        query GetAlbum($id: ID!) {
            album(id: $id) {
                tracks {
                    id
                    title
                    duration
                    url
                    artists { id name }
                    album { id title }
                }
            }
        }
        """
        data = await self._graphql(query, {"id": prov_album_id})
        album = data.get("album") if data else None
        if not album:
            return []
        return [self._parse_track(item) for item in album.get("tracks", [])]

    async def get_playlist(self, prov_playlist_id: str) -> Playlist:
        """Return full playlist details."""
        query = """
        query GetPlaylist($id: ID!) { playlist(id: $id) { id title } }
        """
        data = await self._graphql(query, {"id": prov_playlist_id})
        playlist_data = data.get("playlist") if data else None
        if not playlist_data:
            raise ProviderUnavailableError(f"Playlist {prov_playlist_id} not found")
        return self._parse_playlist(playlist_data)

    async def get_radio(self, prov_radio_id: str) -> Radio:
        """Return radio station details."""
        query = """
        query GetRadio($id: ID!) { radio(id: $id) { id title url } }
        """
        data = await self._graphql(query, {"id": prov_radio_id})
        radio_data = data.get("radio") if data else None
        if not radio_data:
            raise ProviderUnavailableError(f"Radio {prov_radio_id} not found")
        return self._parse_radio(radio_data)

    async def get_similar_tracks(self, prov_track_id: str, limit: int = 25) -> list[Track]:
        """Retrieve tracks similar to the provided track."""
        query = """
        query Similar($id: ID!, $limit: Int!) {
            similarTracks(id: $id, limit: $limit) {
                id
                title
                duration
                url
                album { id title }
                artists { id name }
            }
        }
        """
        data = await self._graphql(query, {"id": prov_track_id, "limit": limit})
        return [self._parse_track(item) for item in data.get("similarTracks", [])]

    # parsers for playlists and radios
    def _parse_playlist(self, data: dict[str, Any]) -> Playlist:
        return Playlist(
            item_id=str(data["id"]),
            provider=self.instance_id,
            name=data.get("title") or data.get("name") or "Unknown Playlist",
            provider_mappings={
                ProviderMapping(
                    item_id=str(data["id"]),
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                )
            },
        )

    def _parse_radio(self, data: dict[str, Any]) -> Radio:
        return Radio(
            item_id=str(data["id"]),
            provider=self.instance_id,
            name=data.get("title") or data.get("name") or "Unknown Radio",
            provider_mappings={
                ProviderMapping(
                    item_id=str(data["id"]),
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                    details=data.get("url"),
                )
            },
        )
