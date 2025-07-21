"""Integration tests exercising the Zing provider against the live API."""

from collections.abc import AsyncGenerator
from typing import Any

import aiohttp
import pytest
from music_assistant_models.enums import MediaType

from music_assistant.mass import MusicAssistant
from music_assistant.providers.zing import API_URL, ZingProvider
from tests.common import wait_for_sync_completion


async def _server_available() -> bool:
    """Check if the Zing server is reachable."""
    try:
        async with aiohttp.ClientSession() as session, session.post(
            API_URL,
            json={"query": "{ __typename }"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            return bool(resp.status == 200)
    except Exception:
        return False


@pytest.fixture
async def zing_provider(mass: MusicAssistant) -> ZingProvider:
    """Ensure the Zing provider is loaded and the server reachable."""
    if not await _server_available():
        pytest.skip("Zing server not reachable")
    async with wait_for_sync_completion(mass):
        await mass.config.save_provider_config("zing", {})
    provider = mass.get_provider("zing")
    assert isinstance(provider, ZingProvider)
    return provider


async def _get_first(gen: AsyncGenerator[Any, None]) -> Any | None:
    async for item in gen:
        return item
    return None


async def test_search(zing_provider: ZingProvider) -> None:
    """Verify search returns results."""
    res = await zing_provider.search(
        "613",
        [MediaType.ARTIST, MediaType.ALBUM, MediaType.TRACK],
        limit=1,
    )
    assert res.artists or res.albums or res.tracks


async def test_library_artists(zing_provider: ZingProvider) -> None:
    """Ensure library artists are returned."""
    artist = await _get_first(zing_provider.get_library_artists())
    assert artist is not None


async def test_get_artist(zing_provider: ZingProvider) -> None:
    """Retrieve a single artist by id."""
    artist = await _get_first(zing_provider.get_library_artists())
    assert artist is not None
    full = await zing_provider.get_artist(artist.item_id)
    assert full.item_id == artist.item_id


async def test_get_artist_albums(zing_provider: ZingProvider) -> None:
    """Fetch albums for a given artist."""
    artist = await _get_first(zing_provider.get_library_artists())
    assert artist is not None
    albums = await zing_provider.get_artist_albums(artist.item_id)
    assert albums


async def test_get_artist_toptracks(zing_provider: ZingProvider) -> None:
    """Fetch top tracks for a given artist."""
    artist = await _get_first(zing_provider.get_library_artists())
    assert artist is not None
    tracks = await zing_provider.get_artist_toptracks(artist.item_id)
    assert tracks


async def test_library_albums(zing_provider: ZingProvider) -> None:
    """Ensure library albums are returned."""
    album = await _get_first(zing_provider.get_library_albums())
    assert album is not None


async def test_get_album_and_tracks(zing_provider: ZingProvider) -> None:
    """Retrieve album details and its tracks."""
    album = await _get_first(zing_provider.get_library_albums())
    assert album is not None
    full = await zing_provider.get_album(album.item_id)
    assert full.item_id == album.item_id
    tracks = await zing_provider.get_album_tracks(album.item_id)
    assert tracks


async def test_library_tracks(zing_provider: ZingProvider) -> None:
    """Ensure library tracks are returned."""
    track = await _get_first(zing_provider.get_library_tracks())
    assert track is not None


async def test_get_track_and_stream(zing_provider: ZingProvider) -> None:
    """Retrieve track details and streaming info."""
    track = await _get_first(zing_provider.get_library_tracks())
    assert track is not None
    full = await zing_provider.get_track(track.item_id)
    assert full.item_id == track.item_id
    details = await zing_provider.get_stream_details(track.item_id, MediaType.TRACK)
    assert details.path


async def test_library_playlists(zing_provider: ZingProvider) -> None:
    """Ensure library playlists are returned."""
    playlist = await _get_first(zing_provider.get_library_playlists())
    assert playlist is not None


async def test_get_playlist(zing_provider: ZingProvider) -> None:
    """Retrieve a single playlist."""
    playlist = await _get_first(zing_provider.get_library_playlists())
    assert playlist is not None
    full = await zing_provider.get_playlist(playlist.item_id)
    assert full.item_id == playlist.item_id


async def test_library_radios(zing_provider: ZingProvider) -> None:
    """Ensure library radios are returned."""
    radio = await _get_first(zing_provider.get_library_radios())
    assert radio is not None


async def test_get_radio(zing_provider: ZingProvider) -> None:
    """Retrieve a radio station."""
    radio = await _get_first(zing_provider.get_library_radios())
    assert radio is not None
    full = await zing_provider.get_radio(radio.item_id)
    assert full.item_id == radio.item_id


async def test_get_similar_tracks(zing_provider: ZingProvider) -> None:
    """Retrieve similar tracks for a given track."""
    track = await _get_first(zing_provider.get_library_tracks())
    assert track is not None
    similar = await zing_provider.get_similar_tracks(track.item_id, limit=5)
    assert isinstance(similar, list)
