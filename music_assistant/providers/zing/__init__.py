"""Zing music provider for Music Assistant."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from aiohttp import ClientResponseError
from music_assistant_models.config_entries import ConfigEntry, ConfigValueType
from music_assistant_models.enums import (
    ContentType,
    ImageType,
    MediaType,
    ProviderFeature,
    StreamType,
    ConfigEntryType,
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

API_URL = "https://jewishmusic.fm:8443/graphql"


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize provider(instance) with given configuration."""
    return ZingProvider(mass, manifest, config)


async def get_config_entries(
    mass: MusicAssistant,
    instance_id: str | None = None,
    action: str | None = None,
    values: dict[str, Any] | None = None,
) -> tuple[ConfigEntry, ...]:
    """Return Config entries for this provider."""
    return (
        ConfigEntry(
            key="userId",
            type=ConfigEntryType.STRING,
            label="User ID",
            description="Enter your Zing user ID (optional, for personalized features)",
            required=False,
            default_value="",
        ),
        ConfigEntry(
            key="token",
            type=ConfigEntryType.SECURE_STRING,
            label="Bearer Token",
            description="Enter your Zing API Bearer token for Authorization header.",
            required=False,
            default_value="",
        ),
    )


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
            ProviderFeature.ARTIST_ALBUMS,
            ProviderFeature.ARTIST_TOPTRACKS,
            ProviderFeature.AUDIO_SOURCE,  # Enable streaming support
        }

    @property
    def user_id(self) -> str:
        val = self.config.get_value("userId")
        return str(val) if val is not None else ""

    @property
    def token(self) -> str:
        val = self.config.get_value("token")
        return str("eyJhbGciOiJSUzI1NiIsImtpZCI6ImE4ZGY2MmQzYTBhNDRlM2RmY2RjYWZjNmRhMTM4Mzc3NDU5ZjliMDEiLCJ0eXAiOiJKV1QifQ.eyJuYW1lIjoi15DXkdeZIiwicGljdHVyZSI6Imh0dHBzOi8vbGgzLmdvb2dsZXVzZXJjb250ZW50LmNvbS9hL0FDZzhvY0k0b3JxUmJhd0NuNTZuVW9pM3FtLWJ2bkdMR0RJWTZ4bTB5VnJYaEZtcXE3cFowOFU9czk2LWMiLCJpc1N1YnNjcmliZWQiOnRydWUsImlzcyI6Imh0dHBzOi8vc2VjdXJldG9rZW4uZ29vZ2xlLmNvbS9hZG1vYi1hcHAtaWQtMzU0OTIyNTE4OCIsImF1ZCI6ImFkbW9iLWFwcC1pZC0zNTQ5MjI1MTg4IiwiYXV0aF90aW1lIjoxNzUyNTE4NTM3LCJ1c2VyX2lkIjoiUFZyVmNyV0d6dWdOTHNIM0VmQ2wzeVNYUGoyMyIsInN1YiI6IlBWclZjcldHenVnTkxzSDNFZkNsM3lTWFBqMjMiLCJpYXQiOjE3NTI1MTg1MzcsImV4cCI6MTc1MjUyMjEzNywiZW1haWwiOiJhdmlmaXhAZ21haWwuY29tIiwiZW1haWxfdmVyaWZpZWQiOnRydWUsImZpcmViYXNlIjp7ImlkZW50aXRpZXMiOnsiZ29vZ2xlLmNvbSI6WyIxMTMxNDg2Nzk1Njc4NjM0OTk0MzIiXSwiZW1haWwiOlsiYXZpZml4QGdtYWlsLmNvbSJdfSwic2lnbl9pbl9wcm92aWRlciI6Imdvb2dsZS5jb20ifX0.bV0nZBKjUYPPNTfaLCc6pyfk4cLp-Fj61YZKKWUDmwgbYQECkN7T74FpzWEU4Wm2zd6QTgDO-cisYxk9JV6Abk0Z2kFf2cWusgQzweSNUhwz5yh1D_3tnMhs2hj1Y-_m3FDFLtNQe6DXTghezjeCULlDI86JSA6ZxDc842q8c77Nyg16JHLzMLf9EXVVij-gEhhA10l4A-TilgUPzMHz0EpFciHSCKgtFkMB79N0Xyy8EJ-U6q6NBN47koKUpdOx5wYvQ-idS541WjgzVEEK7FBLsUL0xM1cNIu3-xiIf-ID8R6GuCD97rpy4QrwK1LNO7mr2S3zlMoLe9p3DmbIlQ") if val is not None else ""

    async def _graphql(self, query: str, variables: dict[str, Any] | None = None) -> Any:
        """Perform a GraphQL request."""
        variables = variables or {}

        try:
            request_data = {"query": query, "variables": variables}
            headers = {"Content-Type": "application/json"}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            async with self.mass.http_session.post(
                self.api_url, json=request_data, headers=headers
            ) as resp:
                if resp.status != 200:
                    try:
                        error_data = await resp.text()
                    except:
                        pass
                resp.raise_for_status()
                data = await resp.json()
        except ClientResponseError as err:
            self.logger.error(f"GraphQL request failed with status {err.status}: {err}")
            raise ProviderUnavailableError(str(err)) from err
        except Exception as e:
            self.logger.error(f"GraphQL request failed with exception: {e}")
            raise ProviderUnavailableError(str(e)) from e
        if "errors" in data:
            error_msg = data["errors"][0].get("message", "unknown error")
            self.logger.error(f"GraphQL returned errors: {error_msg}")
            self.logger.error(f"Full GraphQL error data: {data['errors']}")
            raise ProviderUnavailableError(error_msg)
        return data.get("data")

    async def search(
        self, search_query: str, media_types: list[MediaType], limit: int = 50
    ) -> SearchResults:
        """Perform a search on the provider."""
        import json
        
        results = SearchResults()
        
        # Search for tracks
        if MediaType.TRACK in media_types:
            track_query = {
                "query": {
                    "match": {
                        "enName": search_query
                    }
                },
                "size": max(limit, 100)  # Use at least 100 results
            }
            track_data = await self._graphql(
                "query SearchElastic($index: String!, $query: String!) { searchElastic(index: $index, query: $query) }",
                {"index": "tracks", "query": json.dumps(track_query)}
            )
            if track_data and track_data.get("searchElastic"):
                try:
                    elastic_data = json.loads(track_data["searchElastic"])
                    if "hits" in elastic_data and "hits" in elastic_data["hits"]:
                        tracks = []
                        for hit in elastic_data["hits"]["hits"]:
                            source = hit["_source"]
                            track = self._parse_track_from_elastic(source)
                            if track:
                                tracks.append(track)
                                if len(tracks) >= limit:
                                    break
                        results.tracks = tracks
                except (json.JSONDecodeError, KeyError):
                    pass
        
        # Search for albums
        if MediaType.ALBUM in media_types:
            album_query = {
                "query": {
                    "match": {
                        "enName": search_query
                    }
                },
                "size": max(limit, 100)  # Use at least 100 results
            }
            album_data = await self._graphql(
                "query SearchElastic($index: String!, $query: String!) { searchElastic(index: $index, query: $query) }",
                {"index": "albums", "query": json.dumps(album_query)}
            )
            if album_data and album_data.get("searchElastic"):
                try:
                    elastic_data = json.loads(album_data["searchElastic"])
                    if "hits" in elastic_data and "hits" in elastic_data["hits"]:
                        albums = []
                        for hit in elastic_data["hits"]["hits"]:
                            source = hit["_source"]
                            album = self._parse_album_from_elastic(source)
                            if album:
                                albums.append(album)
                                if len(albums) >= limit:
                                    break
                        results.albums = albums
                except (json.JSONDecodeError, KeyError):
                    pass
        
        # Search for artists
        if MediaType.ARTIST in media_types:
            artist_query = {
                "query": {
                    "match": {
                        "enName": search_query
                    }
                },
                "size": max(limit, 100)  # Use at least 100 results
            }
            artist_data = await self._graphql(
                "query SearchElastic($index: String!, $query: String!) { searchElastic(index: $index, query: $query) }",
                {"index": "artists", "query": json.dumps(artist_query)}
            )
            if artist_data and artist_data.get("searchElastic"):
                try:
                    elastic_data = json.loads(artist_data["searchElastic"])
                    if "hits" in elastic_data and "hits" in elastic_data["hits"]:
                        artists = []
                        for hit in elastic_data["hits"]["hits"]:
                            source = hit["_source"]
                            artist = self._parse_artist_from_elastic(source)
                            if artist:
                                artists.append(artist)
                                if len(artists) >= limit:
                                    break
                        results.artists = artists
                except (json.JSONDecodeError, KeyError):
                    pass
        
        return results

    async def get_track(self, prov_track_id: str) -> Track:
        """Return full track details."""
        self.logger.info(f"Getting track details for ID: {prov_track_id}")
        try:
            query = """
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
            variables = {"trackId": int(prov_track_id)}
            self.logger.debug(f"GraphQL variables: {variables}")
            
            data = await self._graphql(query, variables)
            track_data = data.get("track") if data else None
            
            if not track_data:
                self.logger.error(f"Track {prov_track_id} not found in API response")
                self.logger.debug(f"API response data: {data}")
                raise ProviderUnavailableError(f"Track {prov_track_id} not found")
            
            self.logger.info(f"Successfully retrieved track data for ID: {prov_track_id}")
            return self._parse_track(track_data)
            
        except Exception as e:
            self.logger.error(f"Error getting track {prov_track_id}: {e}")
            raise ProviderUnavailableError(f"Failed to get track {prov_track_id}: {e}")

    async def get_stream_details(self, item_id: str, media_type: MediaType) -> StreamDetails:
        """Return the content details for the given track when it will be streamed."""
        try:
            # Get track details to construct the audio URL
            track = await self.get_track(item_id)
            
            # Extract the file path from the track's provider mapping details
            file_path = None
            for mapping in track.provider_mappings:
                if mapping.details:
                    file_path = mapping.details
                    break
            
            if not file_path:
                raise ProviderUnavailableError(f"No audio file path found for track {item_id}")
            
            # Construct the full audio URL
            audio_url = f"{self.api_url.replace(':8443/graphql', '')}/wp-content/uploads/secretmusicfolder1{file_path}"
            
            # Log the constructed URL for debugging
            self.logger.info(f"Constructed audio URL for track {item_id}: {audio_url}")
            
            # Create stream details with the audio URL
            stream_details = StreamDetails(
                provider=self.instance_id,
                item_id=track.item_id,
                audio_format=AudioFormat(content_type=ContentType.MP3),
                stream_type=StreamType.HTTP,  # Use HTTP for direct streaming
                path=audio_url,
                duration=track.duration,
                can_seek=True,
                allow_seek=True,
                enable_cache=False,  # Disable caching to avoid issues with cached stream details
            )
            
            # Log the stream details for debugging
            self.logger.info(f"Created StreamDetails for track {item_id}:")
            self.logger.info(f"  - path: {stream_details.path}")
            self.logger.info(f"  - stream_type: {stream_details.stream_type}")
            self.logger.info(f"  - audio_format: {stream_details.audio_format}")
            
            return stream_details
        except Exception as e:
            self.logger.error(f"Error getting stream details for track {item_id}: {e}")
            raise ProviderUnavailableError(f"Failed to get stream details for track {item_id}: {e}")

    async def get_audio_stream(
        self, streamdetails: StreamDetails, seek_position: int = 0
    ) -> AsyncGenerator[bytes, None]:
        """Get audio stream with proper headers to bypass CORS."""
        # Log the stream details for debugging
        self.logger.info(f"Starting audio stream for track {streamdetails.item_id}")
        self.logger.info(f"Stream URL: {streamdetails.path}")
        self.logger.info(f"Stream type: {streamdetails.stream_type}")
        self.logger.info(f"Audio format: {streamdetails.audio_format}")
        self.logger.info(f"Seek position: {seek_position}")
        
        if not streamdetails.path:
            self.logger.error(f"No audio path available for track {streamdetails.item_id}")
            raise ProviderUnavailableError("No audio path available")
            
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; MusicAssistant/1.0)",
            "Referer": "https://zingmusic.app/",
            "Origin": "https://zingmusic.app",
        }
        
        # Add range header for seeking if needed
        if seek_position > 0:
            headers["Range"] = f"bytes={seek_position}-"
        
        try:
            async with self.mass.http_session.get(
                streamdetails.path, 
                headers=headers
            ) as response:
                response.raise_for_status()
                
                self.logger.info(f"HTTP response status: {response.status}")
                self.logger.info(f"HTTP response headers: {dict(response.headers)}")
                self.logger.info("Starting to stream audio chunks...")
                
                chunk_count = 0
                async for chunk in response.content.iter_chunked(8192):
                    chunk_count += 1
                    if chunk_count % 10 == 0:  # Log every 10th chunk
                        self.logger.info(f"Streamed {chunk_count} chunks, current chunk size: {len(chunk)} bytes")
                    yield chunk
                    
        except Exception as e:
            self.logger.error(f"Error streaming audio for track {streamdetails.item_id}: {e}")
            raise ProviderUnavailableError(f"Failed to stream audio: {e}")





    # Library queries
    async def get_library_artists(self) -> AsyncGenerator[Artist, None]:
        if not self.user_id:
            self.logger.info("No user_id set; not fetching library artists.")
            return
        # Fetch user favorites using GetUserMyMusic query
        query = """
        query GetUserMyMusic($userUid: String!) {
          user(where: {uid: $userUid}) {
            myArtists(orderBy: {artistPosition: asc}) {
              artist {
                id
                enName
                heName
                images { small medium large }
              }
            }
          }
        }
        """
        variables = {"userUid": self.user_id}
        try:
            data = await self._graphql(query, variables)
            artists_data = data["user"]["myArtists"]
            for item in artists_data:
                try:
                    artist = self._parse_artist(item["artist"])
                    yield artist
                except Exception as e:
                    self.logger.error(f"Error parsing artist: {e}")
                    continue
        except Exception as e:
            self.logger.error(f"Error fetching user library artists: {e}")
            raise

    async def get_library_albums(self) -> AsyncGenerator[Album, None]:
        if not self.user_id:
            self.logger.info("No user_id set; not fetching library albums.")
            return
        # Fetch user favorites using GetUserMyMusic query
        query = """
        query GetUserMyMusic($userUid: String!) {
          user(where: {uid: $userUid}) {
            myAlbums(orderBy: {albumPosition: asc}) {
              album {
                id
                enName
                heName
                releasedAt
                genres { id enName heName }
                images { small medium large }
                artists { id enName heName }
              }
            }
          }
        }
        """
        variables = {"userUid": self.user_id}
        try:
            data = await self._graphql(query, variables)
            albums_data = data["user"]["myAlbums"]
            for item in albums_data:
                try:
                    album = self._parse_album(item["album"])
                    yield album
                except Exception as e:
                    self.logger.error(f"Error parsing album: {e}")
                    continue
        except Exception as e:
            self.logger.error(f"Error fetching user library albums: {e}")
            raise

    async def get_library_tracks(self) -> AsyncGenerator[Track, None]:
        if not self.user_id:
            self.logger.info("No user_id set; not fetching library tracks.")
            return
        # Fetch user favorites using GetUserMyMusic query
        query = """
        query GetUserMyMusic($userUid: String!) {
          user(where: {uid: $userUid}) {
            myTracks(orderBy: {trackPosition: asc}) {
              track {
                id
                enName
                heName
                fileName
                duration
                album {
                  id
                  images { small medium large }
                }
              }
            }
          }
        }
        """
        variables = {"userUid": self.user_id}
        try:
            data = await self._graphql(query, variables)
            tracks_data = data["user"]["myTracks"]
            for item in tracks_data:
                try:
                    track = self._parse_track(item["track"])
                    yield track
                except Exception as e:
                    self.logger.error(f"Error parsing track: {e}")
                    continue
        except Exception as e:
            self.logger.error(f"Error fetching user library tracks: {e}")
            raise

    async def get_library_playlists(self) -> AsyncGenerator[Playlist, None]:
        if not self.user_id:
            self.logger.info("No user_id set; not fetching library playlists.")
            return
        query = '''
        query GetPlaylist($userUid: String!) {
          playlists(
            where: { user: { uid: { equals: $userUid } } }
            orderBy: { index: { sort: asc } }
          ) {
            id
            name
            image
          }
        }
        '''
        variables = {"userUid": self.user_id}
        try:
            data = await self._graphql(query, variables)
            playlists_data = data["playlists"]
            for item in playlists_data:
                try:
                    playlist = self._parse_playlist(item)
                    yield playlist
                except Exception as e:
                    self.logger.error(f"Error parsing playlist: {e}")
                    continue
        except Exception as e:
            self.logger.error(f"Error fetching user playlists: {e}")
            raise

    async def get_library_radios(self) -> AsyncGenerator[Radio, None]:
        """Retrieve all radio stations from the library."""
        # Radio functionality not available in this API
        return
        yield

    #Artists
    async def get_artist(self, prov_artist_id: str) -> Artist:
        """Return full artist details."""
        query = """
        query GetArtist($where: ArtistWhereUniqueInput!) { 
            artist(where: $where) { id enName heName } 
        }
        """
        data = await self._graphql(query, {"where": {"id": int(prov_artist_id)}})
        artist_data = data.get("artist") if data else None
        if not artist_data:
            raise ProviderUnavailableError(f"Artist {prov_artist_id} not found")
        return self._parse_artist(artist_data)

    async def get_artist_albums(self, prov_artist_id: str) -> list[Album]:
        """Return albums for the given artist."""
        query = """
        query GetArtistAlbums($where: ArtistWhereUniqueInput!) {
            artist(where: $where) {
                albums { id enName heName artists { id enName heName } }
            }
        }
        """
        data = await self._graphql(query, {"where": {"id": int(prov_artist_id)}})
        artist = data.get("artist") if data else None
        if not artist:
            return []
        return [self._parse_album(item) for item in artist.get("albums", [])]

    async def get_artist_toptracks(self, prov_artist_id: str) -> list[Track]:
        """Return top tracks for the given artist."""
        query = """
        query GetArtistTop($where: ArtistWhereUniqueInput!) {
            artist(where: $where) {
                tracks { id enName heName duration file album { id enName heName } artists { id enName heName } }
            }
        }
        """
        data = await self._graphql(query, {"where": {"id": int(prov_artist_id)}})
        artist = data.get("artist") if data else None
        if not artist:
            return []
        return [self._parse_track(item) for item in artist.get("tracks", [])]

    #Albums
    async def get_album(self, prov_album_id: str) -> Album:
        """Return full album details."""
        query = """
        query GetAlbum($where: AlbumWhereUniqueInput!) { 
            album(where: $where) { id enName heName artists { id enName heName } } 
        }
        """
        data = await self._graphql(query, {"where": {"id": int(prov_album_id)}})
        album_data = data.get("album") if data else None
        if not album_data:
            raise ProviderUnavailableError(f"Album {prov_album_id} not found")
        return self._parse_album(album_data)

    async def get_album_tracks(self, prov_album_id: str) -> list[Track]:
        """Return the tracks for the given album."""
        query = """
        query GetAlbum($where: AlbumWhereUniqueInput!) {
            album(where: $where) {
                tracks {
                    id
                    enName
                    heName
                    duration
                    file
                    artists { id enName heName }
                    album { id enName heName }
                }
            }
        }
        """
        data = await self._graphql(query, {"where": {"id": int(prov_album_id)}})
        album = data.get("album") if data else None
        if not album:
            return []
        return [self._parse_track(item) for item in album.get("tracks", [])]

    #Playlists
    async def get_playlist(self, prov_playlist_id: str) -> Playlist:
        """Return full playlist details."""
        query = """
        query GetPlaylist($where: PlaylistWhereUniqueInput!) { 
            playlist(where: $where) { id enName heName } 
        }
        """
        data = await self._graphql(query, {"where": {"id": int(prov_playlist_id)}})
        playlist_data = data.get("playlist") if data else None
        if not playlist_data:
            raise ProviderUnavailableError(f"Playlist {prov_playlist_id} not found")
        return self._parse_playlist(playlist_data)

    async def get_playlist_tracks(self, prov_playlist_id: str, page: int = 0, page_size: int = 100) -> list[Track]:
        """Return the tracks for the given playlist."""
        query = """
        query GetPlaylistTracks($playlistId: Int!) {
            playlist(where: { id: $playlistId }) {
                playlistTracks(orderBy: { trackPosition: asc }) {
                    track {
                        id
                        enName
                        heName
                        file
                        duration
                        album { id enName heName }
                        artists { id enName heName }
                    }
                }
            }
        }
        """
        variables = {"playlistId": int(prov_playlist_id)}
        data = await self._graphql(query, variables)
        playlist = data.get("playlist") if data else None
        if not playlist:
            return []
        tracks = []
        for item in playlist.get("playlistTracks", []):
            track_data = item.get("track")
            if track_data:
                track = self._parse_track(track_data)
                tracks.append(track)
        return tracks

    #Radios
    async def get_radio(self, prov_radio_id: str) -> Radio:
        """Return radio station details."""
        # Radio functionality not available in this API
        raise ProviderUnavailableError(f"Radio {prov_radio_id} not found")

    async def get_similar_tracks(self, prov_track_id: str, limit: int = 25) -> list[Track]:
        """Retrieve tracks similar to the provided track."""
        # Similar tracks functionality not available in this API
        return []

    # parsers for playlists and radios
    def _parse_playlist(self, data: dict[str, Any]) -> Playlist:
        # Parse playlist as before, but do not assign tracks (linter error)
        playlist = Playlist(
            item_id=str(data["id"]),
            provider=self.instance_id,
            name=data.get("name") or data.get("heName") or data.get("enName") or "Unknown Playlist",
            provider_mappings={
                ProviderMapping(
                    item_id=str(data["id"]),
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                )
            },
        )
        image_url = data.get("image")
        if image_url:
            from music_assistant_models.media_items import MediaItemImage
            playlist.metadata.images = UniqueList([
                MediaItemImage(
                    type=ImageType.THUMB,
                    path=image_url,
                    provider=self.instance_id,
                )
            ])
        return playlist

    def _parse_radio(self, data: dict[str, Any]) -> Radio:
        return Radio(
            item_id=str(data["id"]),
            provider=self.instance_id,
            name=data.get("enName") or data.get("heName") or "Unknown Radio",
            provider_mappings={
                ProviderMapping(
                    item_id=str(data["id"]),
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                    details=data.get("url"),
                )
            },
        )

    def _parse_artist(self, data: dict[str, Any]) -> Artist:
        artist_name = data.get("heName") or data.get("enName") or "Unknown Artist"
        artist = Artist(
            item_id=str(data["id"]),
            provider=self.instance_id,
            name=artist_name,
            provider_mappings={
                ProviderMapping(
                    item_id=str(data["id"]),
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                )
            },
        )
        if images_data := data.get("images"):
            from music_assistant_models.media_items import MediaItemImage
            image_url = images_data.get("large") or images_data.get("medium") or images_data.get("small")
            if image_url:
                self.logger.debug(f"Adding image to artist {artist_name}: {image_url}")
                artist.metadata.images = UniqueList([
                    MediaItemImage(
                        type=ImageType.THUMB,
                        path=image_url,
                        provider=self.instance_id,
                    )
                ])
            else:
                self.logger.debug(f"No valid image URL found for artist {artist_name}")
        else:
            self.logger.debug(f"No images data found for artist {artist_name}")
        return artist

    def _parse_album(self, data: dict[str, Any]) -> Album:
        artists_data = data.get("artists", [])
        if not artists_data:
            # Create a default "heName" if no artists are provided
            # Music Assistant requires albums to have at least one artist
            artists = [
                Artist(
                    item_id="unknown",
                    provider=self.instance_id,
                    name="heName",
                    provider_mappings={
                        ProviderMapping(
                            item_id="unknown",
                            provider_domain=self.domain,
                            provider_instance=self.instance_id,
                        )
                    },
                )
            ]
        else:
            artists = [self._parse_artist(art) for art in artists_data]
        
        album = Album(
            item_id=str(data["id"]),
            provider=self.instance_id,
            name=data.get("heName") or data.get("enName") or "Unknown Album",
            artists=UniqueList(artists),
            provider_mappings={
                ProviderMapping(
                    item_id=str(data["id"]),
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                )
            },
        )
        if images_data := data.get("images"):
            from music_assistant_models.media_items import MediaItemImage
            image_url = images_data.get("large") or images_data.get("medium") or images_data.get("small")
            if image_url:
                self.logger.debug(f"Adding image to album {data.get('heName') or data.get('enName')}: {image_url}")
                album.metadata.images = UniqueList([
                    MediaItemImage(
                        type=ImageType.THUMB,
                        path=image_url,
                        provider=self.instance_id,
                    )
                ])
            else:
                self.logger.debug(f"No valid image URL found in images data for album {data.get('heName') or data.get('enName')}")
                self.logger.debug(f"Images data structure: {images_data}")
        else:
            self.logger.debug(f"No images data found for album {data.get('heName') or data.get('enName')}")
        return album

    def _parse_track(self, data: dict[str, Any]) -> Track:
        track_name = data.get("heName") or data.get("enName") or "Unknown Track"
        file_path = data.get("file")
        
        # Log track parsing details
        self.logger.debug(f"Parsing track: {track_name} (ID: {data.get('id')})")
        if file_path:
            self.logger.debug(f"Track {track_name} has audio file: {file_path}")
        else:
            self.logger.warning(f"Track {track_name} has no audio file path")
        
        artists = [self._parse_artist(art) for art in data.get("artists", [])]
        album = self._parse_album(data["album"]) if data.get("album") else None
        track = Track(
            item_id=str(data["id"]),
            provider=self.instance_id,
            name=track_name,
            duration=int(data.get("duration") or 0),
            artists=UniqueList(artists),
            album=album,
            provider_mappings={
                ProviderMapping(
                    item_id=str(data["id"]),
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                    details=file_path,  # Store the file path for streaming
                )
            },
        )
        
        # Get images from the album if available, since tracks don't have their own images
        if album and album.metadata.images:
            track.metadata.images = album.metadata.images
            self.logger.debug(f"Using album image for track {track_name}")
        else:
            self.logger.debug(f"No album image available for track {track_name}")
            
        return track

    def _parse_track_from_elastic(self, data: dict[str, Any]) -> Track | None:
        """Parse track data from Elasticsearch response."""
        try:
            artists = [self._parse_artist(art) for art in data.get("artists", [])]
            album = self._parse_album_from_elastic(data["album"]) if data.get("album") else None
            track = Track(
                item_id=str(data["id"]),
                provider=self.instance_id,
                name=data.get("heName") or data.get("enName") or "Unknown Track",
                duration=int(data.get("duration") or 0),
                artists=UniqueList(artists),
                album=album,
                provider_mappings={
                    ProviderMapping(
                        item_id=str(data["id"]),
                        provider_domain=self.domain,
                        provider_instance=self.instance_id,
                        details=data.get("file"),  # Store the file path for streaming
                    )
                },
            )
            if images_data := data.get("images"):
                from music_assistant_models.media_items import MediaItemImage
                image_url = images_data.get("large") or images_data.get("medium") or images_data.get("small")
                if image_url:
                    track.metadata.images = UniqueList([
                        MediaItemImage(
                            type=ImageType.THUMB,
                            path=image_url,
                            provider=self.instance_id,
                        )
                    ])
            return track
        except (KeyError, ValueError):
            return None

    def _parse_album_from_elastic(self, data: dict[str, Any]) -> Album | None:
        """Parse album data from Elasticsearch response."""
        try:
            artists_data = data.get("artists", [])
            if not artists_data:
                # Create a default "heName" if no artists are provided
                # Music Assistant requires albums to have at least one artist
                artists = [
                    Artist(
                        item_id="unknown",
                        provider=self.instance_id,
                        name="heName",
                        provider_mappings={
                            ProviderMapping(
                                item_id="unknown",
                                provider_domain=self.domain,
                                provider_instance=self.instance_id,
                            )
                        },
                    )
                ]
            else:
                artists = [self._parse_artist(art) for art in artists_data]
            
            album = Album(
                item_id=str(data["id"]),
                provider=self.instance_id,
                name=data.get("heName") or data.get("enName") or "Unknown Album",
                artists=UniqueList(artists),
                provider_mappings={
                    ProviderMapping(
                        item_id=str(data["id"]),
                        provider_domain=self.domain,
                        provider_instance=self.instance_id,
                    )
                },
            )
            if images_data := data.get("images"):
                from music_assistant_models.media_items import MediaItemImage
                image_url = images_data.get("large") or images_data.get("medium") or images_data.get("small")
                if image_url:
                    album.metadata.images = UniqueList([
                        MediaItemImage(
                            type=ImageType.THUMB,
                            path=image_url,
                            provider=self.instance_id,
                        )
                    ])
            return album
        except (KeyError, ValueError):
            return None

    def _parse_artist_from_elastic(self, data: dict[str, Any]) -> Artist | None:
        """Parse artist data from Elasticsearch response."""
        try:
            artist = Artist(
                item_id=str(data["id"]),
                provider=self.instance_id,
                name=data.get("heName") or data.get("enName") or "Unknown Artist",
                provider_mappings={
                    ProviderMapping(
                        item_id=str(data["id"]),
                        provider_domain=self.domain,
                        provider_instance=self.instance_id,
                    )
                },
            )
            if images_data := data.get("images"):
                from music_assistant_models.media_items import MediaItemImage
                image_url = images_data.get("large") or images_data.get("medium") or images_data.get("small")
                if image_url:
                    artist.metadata.images = UniqueList([
                        MediaItemImage(
                            type=ImageType.THUMB,
                            path=image_url,
                            provider=self.instance_id,
                        )
                    ])
            return artist
        except (KeyError, ValueError):
            return None

   