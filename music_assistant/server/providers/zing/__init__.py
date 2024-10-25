from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from typing import TYPE_CHECKING

from music_assistant.common.models.config_entries import ConfigEntry, ConfigValueType
from music_assistant.common.models.enums import ContentType, MediaType, ProviderFeature, StreamType
from music_assistant.common.models.media_items import (
    Album,
    Artist,
    AudioFormat,
    ItemMapping,
    MediaItemType,
    MediaItemImage,
    Playlist,
    ProviderMapping,
    Radio,
    SearchResults,
    ImageType,
    Track,
)
from music_assistant.common.models.streamdetails import StreamDetails
from music_assistant.server.models.music_provider import MusicProvider

if TYPE_CHECKING:
    from music_assistant.common.models.config_entries import ProviderConfig
    from music_assistant.common.models.provider import ProviderManifest
    from music_assistant.server import MusicAssistant
    from music_assistant.server.models import ProviderInstanceType

from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport


async def setup(
        mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize provider(instance) with given configuration."""
    # setup is called when the user wants to setup a new provider instance.
    # you are free to do any preflight checks here and but you must return
    #  an instance of the provider.
    return ZingMusicProvider(mass, manifest, config)


async def get_config_entries(
        mass: MusicAssistant,
        instance_id: str | None = None,
        action: str | None = None,
        values: dict[str, ConfigValueType] | None = None,
) -> tuple[ConfigEntry, ...]:
    """
    Return Config entries to setup this provider.

    instance_id: id of an existing provider instance (None if new instance setup).
    action: [optional] action key called from config entries UI.
    values: the (intermediate) raw values for config entries sent with the action.
    """
    return ()


class ZingMusicProvider(MusicProvider):
    def __init__(self, mass, manifest, config):
        super().__init__(mass, manifest, config)
        self.api_url = "http://jewishmusic.fm:4000/graphql"
        self.client = Client(
            transport=RequestsHTTPTransport(
                url=self.api_url, verify=True, retries=3),
            fetch_schema_from_transport=True,
        )

    @property
    def supported_features(self) -> tuple[ProviderFeature, ...]:
        """Return the features supported by this Provider."""
        # MANDATORY
        # you should return a tuple of provider-level features
        # here that your player provider supports or an empty tuple if none.
        # for example 'ProviderFeature.SYNC_PLAYERS' if you can sync players.
        return (
            ProviderFeature.SEARCH,
            ProviderFeature.BROWSE,
            ProviderFeature.LIBRARY_ALBUMS,
            ProviderFeature.LIBRARY_ARTISTS,
            ProviderFeature.LIBRARY_TRACKS,
            ProviderFeature.ARTIST_ALBUMS,
            ProviderFeature.SIMILAR_TRACKS,
        )

    @property
    def is_streaming_provider(self) -> bool:
        # For streaming providers return True here but for local file based providers return False.
        return True

    async def search(
            self,
            search_query: str,
            media_types: list[MediaType],
            limit: int = 5,
    ) -> SearchResults:
        """Perform search on the music provider."""
        query = gql("""
            query SearchByName($term: String!, $skip: Int!, $take: Int!) {
                artists(
                    where: {
                    OR: [
                        { enName: { contains: $term } },
                        { heName: { contains: $term } }
                    ]
                    },
                    skip: $skip,
                    take: $take
                ) {
                    id
                    enName
                    heName
                    images {
                    large
                    medium
                    small
                    }
                }

                albums(
                    where: {
                    OR: [
                        { enName: { contains: $term } },
                        { heName: { contains: $term } }
                    ]
                    },
                    skip: $skip,
                    take: $take
                ) {
                    id
                    enName
                    heName
                    releasedAt
                    artists {
                    id
                    enName
                    heName
                    }
                    images {
                    large
                    medium
                    small
                    }
                }

                tracks(
                    where: {
                    OR: [
                        { enName: { contains: $term } },
                        { heName: { contains: $term } }
                    ]
                    },
                    skip: $skip,
                    take: $take
                ) {
                    id
                    trackNumber
                    enName
                    heName
                    duration
                    file
                    album {
                        id
                        enName
                        heName
                    }
                    artists {
                        id
                        enName
                        heName
                    }
                    images
                }
                }




        """
                    )

        params = {"term": search_query, "take": limit, "skip": 0}
        response = self.client.execute(query, variable_values=params)

        tracks = []
        for track in response["tracks"]:
            parsed_track = self._parse_track(track)
            tracks.append(parsed_track)

        albums = []
        for album in response["albums"]:
            parsed_album = self._parse_album(album)
            albums.append(parsed_album)

        artists = []
        for artist in response["artists"]:
            parsed_artist = self._parse_album(artist)
            artists.append(parsed_artist)

        return SearchResults(tracks=tracks, albums=albums, artists=artists)

    async def get_library_artists(self) -> AsyncGenerator[Artist, None]:
        query = gql(
            """
            query GetCatArtists(
                $term: String!
                $skip: Int!
                $count: Int!
                $category: String!
                ) {
                __typename
                artists(
                    take: $count
                    skip: $skip
                    orderBy: { heName: asc }
                    where: {
                    OR: [
                        { enName: { contains: $term, mode: insensitive } }
                        { heName: { contains: $term, mode: insensitive } }
                    ]
                    categories: {
                        some: { enName: { contains: $category, mode: insensitive } }
                    }
                    }
                ) {
                    __typename
                    id
                    enName
                    heName
                    images {
                        __typename
                        small
                        medium
                        large
                    }
                }
                }
        """
        )
        params = {"term": "", "skip": 0, "count": 10,
                  "category": "popular artist"}
        response = self.client.execute(query, variable_values=params)

        artists_obj = response["artists"]
        for artist in artists_obj:
            yield self._parse_artist(artist)

    async def get_library_albums(self) -> AsyncGenerator[Album, None]:
        """Get full artist details by id."""
        query = gql(
            """
           query GetAllAlbums($skip: Int!, $take: Int!) {
                albums(
                    orderBy: { releasedAt: desc },
                    skip: $skip,
                    take: $take
                ) {
                    id
                    enName
                    heName
                    artists {
                        id
                        enName
                        heName
                    }
                    images {
                        large
                        small
                        medium
                    }
                }
            }
        """
        )
        params = {"skip": 0, "take": 10}
        response = self.client.execute(query, variable_values=params)

        albums_obj = response["albums"]
        albums_list = []

        for album in albums_obj:
            yield self._parse_album(album)
            # albums_list.append(parsed_album)

        # return albums_list

    async def get_library_tracks(self) -> AsyncGenerator[Track, None]:
        query = gql(
            """
           query GetPopularTracks($count: Int!) {
                tracks(take: $count) {
                    id
                    enName
                    heName
                    file
                    duration
                    album {
                    id
                    enName
                    heName
                    artists {
                        id
                        heName
                        enName
                        images {
                            large
                            medium
                            small
                        }
                    }
                    images {
                        small
                        medium
                        large
                    }
                    }
                }
            }
        """
        )
        params = {"count": 10}
        response = self.client.execute(query, variable_values=params)

        tracks_obj = response["tracks"]
        for track in tracks_obj:
            self._parse_track(track)

        tracks_list = []

        for track in tracks_obj:
            yield self._parse_track(track)
            # tracks_list.append(parsed_track)

        # return tracks_list

    async def get_artist_albums(self, prov_artist_id) -> AsyncGenerator[Album, None]:
        """Get a list of all albums for the given artist."""
        query = gql(
            """
                query GetAlbumsByArtist($artistId: Int!, $orderBy: [AlbumOrderByWithRelationInput!], $take: Int, $skip: Int) {
                    albums(where: { artists: { some: { id: { equals: $artistId } } } }, orderBy: $orderBy, take: $take, skip: $skip) {
                        id
                        enName
                        heName
                        artists {
                            id
                            enName
                            heName
                        }
                        images {
                            large
                            small
                            medium
                        }
                    }
                }

        """
        )
        params = {"term": "", "skip": 0,
                  "count": 50, "artistId": int(prov_artist_id)}
        response = self.client.execute(query, variable_values=params)

        albums_obj = response["albums"]

        albums_list = []

        for album in albums_obj:
            parsed_album = self._parse_album(album)
            albums_list.append(parsed_album)

        return albums_list

    async def get_album_tracks(self, prov_album_id: str) -> list[Track]:
        query = gql(
            """
            query GetAlbumTracks($albumId: Int!) {
                album(where: { id: $albumId }) {
                    id
                    enName
                    heName
                    tracks {
                    id
                    trackNumber
                    enName
                    heName
                    duration
                    file
                    artists {
                        id
                        enName
                        heName
                    }
                    }
                }
            }

        """
        )
        params = {"albumId": int(prov_album_id)}
        response = self.client.execute(query, variable_values=params)

        tracks_obj = response["album"]["tracks"]
        tracks = []
        for track in tracks_obj:
            parsed_track = self._parse_track(track)
            tracks.append(parsed_track)
        return tracks

    async def get_album(self, prov_album_id) -> Album:
        """Get full album details by id."""
        query = gql(
            """
                query GetAlbumById($albumId: Int!) {
                        album(where: { id: $albumId }) {
                            id
                            enName
                            heName
                            enDesc
                            heDesc
                            artists {
                                id
                                enName
                                heName
                            }
                            images {
                                large
                                medium
                                small
                            }
                        }
                    }

        """
        )
        params = {"albumId": int(prov_album_id)}
        response = self.client.execute(query, variable_values=params)

        album_data = response["album"]
        parsed_album = self._parse_album(album_data)
        return parsed_album

    async def get_track(self, prov_track_id) -> Track:
        """Get full track details by id."""
        query = gql(
            """
            query GetTrackById($trackId: Int!) {
                track(where: { id: $trackId }) {
                    id
                    trackNumber
                    enName
                    heName
                    file
                    duration
                    album {
                    id
                    enName
                    heName
                    }
                    artists {
                    id
                    enName
                    heName
                    }
                    genres {
                    id
                    enName
                    heName
                    }
                    images
                }
                }

        """
        )
        params = {"trackId": int(prov_track_id)}
        response = self.client.execute(query, variable_values=params)
        track_data = response["track"]
        res = self._parse_track(track_data)
        return res

    async def get_artist(self, prov_artist_id) -> Artist:
        query = gql(
            """
            query GetArtistById($artistId: Int!) {
                artist(where: { id: $artistId }) {
                    id
                    enName
                    heName
                    images {
                    large
                    medium
                    small
                    }
                }
            }
        """
        )
        params = {"artistId": int(prov_artist_id)}
        response = self.client.execute(query, variable_values=params)

        artist_obj = response["artist"]
        self._parse_artist(artist_obj)

    async def get_similar_tracks(  # type: ignore[return]
        self, prov_track_id: str, limit: int = 25
    ) -> list[Track]:
        """Retrieve a dynamic list of similar tracks based on the provided track."""
        # Get a list of similar tracks based on the provided track.
        # This is only called if the provider supports the SIMILAR_TRACKS feature.
        return []

    async def get_stream_details(self, item_id: str) -> StreamDetails:
        """Get streamdetails for a track/radio."""
        # Get stream details for a track or radio.
        # Implementing this method is MANDATORY to allow playback.
        # The StreamDetails contain info how Music Assistant can play the track.
        # item_id will always be a track or radio id. Later, when/if MA supports
        # podcasts or audiobooks, this may as well be an episode or chapter id.
        # You should return a StreamDetails object here with the info as accurate as possible
        # to allow Music Assistant to process the audio using ffmpeg.
        sd = StreamDetails(
            provider=self.instance_id,
            item_id=str(item_id),
            audio_format=AudioFormat(
                # provide details here about sample rate etc. if known
                # set content type to unknown to let ffmpeg guess the codec/container
                content_type=ContentType.UNKNOWN,
            ),
            media_type=MediaType.TRACK,
            # streamtype defines how the stream is provided
            # for most providers this will be HTTP but you can also use CUSTOM
            # to provide a custom stream generator in get_audio_stream.
            stream_type=StreamType.HTTP,
            # explore the StreamDetails model and StreamType enum for more options
            # but the above should be the mandatory fields to set.
        )
        return sd

    def _parse_artist(self, artist_obj: dict) -> Artist:
        """Parse a YT Artist response to Artist model object."""

        artist = Artist(
            item_id=str(artist_obj["id"]),
            name=artist_obj["heName"] or artist_obj["enName"],
            provider=self.domain,
            provider_mappings={
                ProviderMapping(
                    item_id=str(artist_obj["id"]),
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                )
            },
        )

        if artist_obj.get("images"):
            artist.metadata.images = self._parse_thumbnails(
                artist_obj["images"])
        return artist

    def _parse_album(self, album_obj) -> Album:
        album = Album(
            item_id=str(album_obj["id"]),
            name=album_obj["heName"] or album_obj["enName"],
            provider=self.domain,
            provider_mappings={
                ProviderMapping(
                    item_id=str(album_obj["id"]),
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                )
            },
        )
        if album_obj.get("artists"):
            album.artists = [
                self._get_artist_item_mapping(artist) for artist in album_obj["artists"]
            ]

        if album_obj.get("images"):
            album.metadata.images = self._parse_thumbnails(album_obj["images"])

        return album

    def _parse_track(self, track_obj: dict) -> Track:
        url = "https://jewishmusic.fm/wp-content/uploads/secretmusicfolder1" + \
            track_obj["file"]
        track = Track(
            item_id=str(track_obj["id"]),
            provider=self.domain,
            name=track_obj["heName"] or track_obj["enName"],
            provider_mappings={
                ProviderMapping(
                    item_id=str(track_obj["id"]),
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                    available=True,
                    url=url,
                    audio_format=AudioFormat(
                        content_type=ContentType.MP3,
                    ),
                )
            },
            disc_number=0,  # not supported on YTM?
            track_number=track_obj.get("trackNumber", 0),
        )

        if track_obj.get("artists"):
            track.artists = [
                self._get_artist_item_mapping(artist) for artist in track_obj["artists"]
            ]

        if (
                track_obj.get("album")
                and isinstance(track_obj.get("album"), dict)
                and track_obj["album"].get("id")
        ):
            album = track_obj["album"]
            track.album = self._get_item_mapping(
                MediaType.ALBUM, album["id"], album["heName"] or album["enName"]
            )

        if "duration" in track_obj and isinstance(track_obj["duration"], (int, float)):
            track.duration = int(track_obj["duration"])

        return track

    def _parse_thumbnails(self, thumbnails_obj: dict) -> list[MediaItemImage]:
        """Parse and YTM thumbnails to MediaItemImage."""
        result: list[MediaItemImage] = []
        processed_images = set()

        # Assuming thumbnails_obj contains keys like 'small', 'medium', 'large', etc.
        for size_key, url in thumbnails_obj.items():
            # Dummy values for width and height based on the size_key.
            if size_key == "small":
                width, height = 150, 150
            elif size_key == "medium":
                width, height = 300, 300
            else:  # assuming "large" or any other size
                width, height = 600, 600

            image_ratio: float = width / height
            image_type = ImageType.LANDSCAPE if image_ratio > 2.0 else ImageType.THUMB

            # Base URL
            url_base = url

            if (url_base, image_type) in processed_images:
                continue

            processed_images.add((url_base, image_type))
            result.append(
                MediaItemImage(
                    type=image_type,
                    path=url,
                    provider=self.lookup_key,
                    remotely_accessible=True,
                )
            )

        return result

    def _get_item_mapping(
            self, media_type: MediaType, key: str, name: str
    ) -> ItemMapping:
        return ItemMapping(
            media_type=media_type,
            item_id=str(key),
            provider=self.instance_id,
            name=name,
        )

    def _get_artist_item_mapping(self, artist_obj: dict) -> ItemMapping:
        return self._get_item_mapping(
            MediaType.ARTIST,
            artist_obj["id"],
            artist_obj["heName"] or artist_obj["enName"],
        )
