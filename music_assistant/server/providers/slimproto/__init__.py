"""Base/builtin provider with support for players using slimproto."""
from __future__ import annotations

import asyncio
import statistics
import time
from collections import deque
from collections.abc import Callable, Generator
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aioslimproto.client import PlayerState as SlimPlayerState
from aioslimproto.client import SlimClient as SlimClientOrg
from aioslimproto.client import TransitionType as SlimTransition
from aioslimproto.const import EventType as SlimEventType
from aioslimproto.discovery import start_discovery

from music_assistant.common.models.config_entries import (
    CONF_ENTRY_CROSSFADE_DURATION,
    CONF_ENTRY_OUTPUT_CODEC,
    ConfigEntry,
    ConfigValueOption,
    ConfigValueType,
)
from music_assistant.common.models.enums import (
    ConfigEntryType,
    ContentType,
    PlayerFeature,
    PlayerState,
    PlayerType,
)
from music_assistant.common.models.errors import QueueEmpty, SetupFailedError
from music_assistant.common.models.player import DeviceInfo, Player
from music_assistant.common.models.queue_item import QueueItem
from music_assistant.constants import CONF_CROSSFADE_DURATION, CONF_PORT
from music_assistant.server.models.player_provider import PlayerProvider

from .cli import LmsCli

if TYPE_CHECKING:
    from music_assistant.common.models.config_entries import ProviderConfig
    from music_assistant.common.models.provider import ProviderManifest
    from music_assistant.server import MusicAssistant
    from music_assistant.server.controllers.streams import MultiClientStreamJob
    from music_assistant.server.models import ProviderInstanceType

CACHE_KEY_PREV_STATE = "slimproto_prev_state"

# sync constants
MIN_DEVIATION_ADJUST = 10  # 10 milliseconds
MIN_REQ_PLAYPOINTS = 4  # we need at least 4 measurements
ENABLE_EXPERIMENTAL_SYNC_JOIN = False  # WIP

# TODO: Implement display support

STATE_MAP = {
    SlimPlayerState.BUFFERING: PlayerState.PLAYING,
    SlimPlayerState.BUFFER_READY: PlayerState.PLAYING,
    SlimPlayerState.PAUSED: PlayerState.PAUSED,
    SlimPlayerState.PLAYING: PlayerState.PLAYING,
    SlimPlayerState.STOPPED: PlayerState.IDLE,
}


@dataclass
class SyncPlayPoint:
    """Simple structure to describe a Sync Playpoint."""

    timestamp: float
    sync_job_id: str
    diff: int


CONF_SYNC_ADJUST = "sync_adjust"
CONF_CLI_TELNET = "cli_telnet"
CONF_CLI_JSON = "cli_json"
CONF_DISCOVERY = "discovery"
DEFAULT_PLAYER_VOLUME = 20
DEFAULT_SLIMPROTO_PORT = 3483


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize provider(instance) with given configuration."""
    prov = SlimprotoProvider(mass, manifest, config)
    await prov.handle_setup()
    return prov


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
    # ruff: noqa: ARG001
    return (
        ConfigEntry(
            key=CONF_PORT,
            type=ConfigEntryType.INTEGER,
            default_value=DEFAULT_SLIMPROTO_PORT,
            label="Slimproto port",
            description="The TCP/UDP port to run the slimproto sockets server. "
            "The default is 3483 and using a different port is not supported by "
            "hardware squeezebox players. Only adjust this port if you want to "
            "use other slimproto based servers side by side with software players, "
            "such as squeezelite.\n\n"
            "NOTE that the Airplay provider in MA (which relies on slimproto), does not seem "
            "to support a different slimproto port.",
            advanced=True,
        ),
        ConfigEntry(
            key=CONF_CLI_TELNET,
            type=ConfigEntryType.BOOLEAN,
            default_value=True,
            label="Enable classic Squeezebox Telnet CLI",
            description="Some slimproto based players require the presence of the telnet CLI "
            " to request more information. For example the Airplay provider "
            "(which relies on slimproto) uses this to fetch the album cover and other metadata."
            "By default this Telnet CLI is hosted on port 9090 but another port will be chosen if "
            "that port is already taken. \n\n"
            "Commands allowed on this interface are very limited and just enough to satisfy "
            "player compatibility, so security risks are minimized to practically zero."
            "You may safely disable this option if you have no players that rely on this feature "
            "or you dont care about the additional metadata.",
            advanced=True,
        ),
        ConfigEntry(
            key=CONF_CLI_JSON,
            type=ConfigEntryType.BOOLEAN,
            default_value=True,
            label="Enable JSON-RPC API",
            description="Some slimproto based players require the presence of the JSON-RPC "
            "API from LMS to request more information. For example to fetch the album cover "
            "and other metadata. "
            "This JSON-RPC API is compatible with Logitech Media Server but not all commands "
            "are implemented. Just enough to satisfy player compatibility. \n\n"
            "This API is hosted on the webserver responsible for streaming to players and thus "
            "accessible on your local network but security impact should be minimal. "
            "You may safely disable this option if you have no players that rely on this feature "
            "or you dont care about the additional metadata.",
            advanced=True,
        ),
        ConfigEntry(
            key=CONF_DISCOVERY,
            type=ConfigEntryType.BOOLEAN,
            default_value=True,
            label="Enable Discovery server",
            description="Broadcast discovery packets for slimproto clients to automatically "
            "discover and connect to this server. \n\n"
            "You may want to disable this feature if you are running multiple slimproto servers "
            "on your network and/or you don't want clients to auto connect to this server.",
            advanced=True,
        ),
    )


class SlimprotoProvider(PlayerProvider):
    """Base/builtin provider for players using the SLIM protocol (aka slimproto)."""

    _socket_servers: list[asyncio.Server | asyncio.BaseTransport]
    _socket_clients: dict[str, SlimClient]
    _sync_playpoints: dict[str, deque[SyncPlayPoint]]
    _virtual_providers: dict[str, tuple[Callable, Callable]]
    _do_not_resync_before: dict[str, float]
    _cli: LmsCli
    port: int = DEFAULT_SLIMPROTO_PORT

    async def handle_setup(self) -> None:
        """Handle async initialization of the provider."""
        self._socket_clients = {}
        self._sync_playpoints = {}
        self._virtual_providers = {}
        self._do_not_resync_before = {}
        self.port = self.config.get_value(CONF_PORT)
        # start slimproto socket server
        try:
            self._socket_servers = [
                await asyncio.start_server(self._create_client, "0.0.0.0", self.port)
            ]
            self.logger.info("Started SLIMProto server on port %s", self.port)
        except OSError:
            raise SetupFailedError(
                f"Unable to start the Slimproto server - is port {self.port} already taken ?"
            )

        # start CLI interface(s)
        enable_telnet = self.config.get_value(CONF_CLI_TELNET)
        enable_json = self.config.get_value(CONF_CLI_JSON)
        if enable_json or enable_telnet:
            self._cli = LmsCli(self, enable_telnet, enable_json)
            await self._cli.setup()

        # start discovery
        if self.config.get_value(CONF_DISCOVERY):
            self._socket_servers.append(
                await start_discovery(
                    self.mass.streams.publish_ip,
                    self.port,
                    self._cli.cli_port if enable_telnet else None,
                    self.mass.streams.publish_ip if enable_json else None,
                    "Music Assistant",
                    self.mass.server_id,
                )
            )

    async def unload(self) -> None:
        """Handle close/cleanup of the provider."""
        if hasattr(self, "_socket_clients"):
            for client in list(self._socket_clients.values()):
                client.disconnect()
        self._socket_clients = {}
        if hasattr(self, "_socket_servers"):
            for _server in self._socket_servers:
                _server.close()
        if hasattr(self, "_cli"):
            await self._cli.unload()
        self._socket_servers = []

    async def _create_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Create player from new connection on the socket."""
        if self.mass.closing:
            return
        addr = writer.get_extra_info("peername")
        self.logger.debug("Socket client connected: %s", addr)

        def client_callback(
            event_type: SlimEventType | str, client: SlimClient, data: Any = None  # noqa: ARG001
        ):
            if event_type == SlimEventType.PLAYER_DISCONNECTED:
                self.mass.create_task(self._handle_disconnected(client))
                return

            if event_type == SlimEventType.PLAYER_CONNECTED:
                self.mass.create_task(self._handle_connected(client))
                return

            if event_type == SlimEventType.PLAYER_DECODER_READY:
                self.mass.create_task(self._handle_decoder_ready(client))
                return

            if event_type == SlimEventType.PLAYER_BUFFER_READY:
                self.mass.create_task(self._handle_buffer_ready(client))
                return

            if event_type == "output_underrun":
                # player ran out of buffer
                self.mass.create_task(self._handle_output_underrun(client))
                return

            if event_type == SlimEventType.PLAYER_HEARTBEAT:
                self._handle_player_heartbeat(client)
                return

            # ignore some uninteresting events
            if event_type in (
                SlimEventType.PLAYER_CLI_EVENT,
                SlimEventType.PLAYER_DECODER_ERROR,
            ):
                return

            # forward player update to MA player controller
            self._handle_player_update(client)

        # construct SlimClient from socket client
        SlimClient(reader, writer, client_callback)

    async def get_player_config_entries(self, player_id: str) -> tuple[ConfigEntry]:
        """Return all (provider/player specific) Config Entries for the given player (if any)."""
        # pick default codec based on capabilities
        default_codec = ContentType.PCM
        if client := self._socket_clients.get(player_id):
            for fmt, fmt_type in (
                ("flc", ContentType.FLAC),
                ("pcm", ContentType.PCM),
                ("mp3", ContentType.MP3),
            ):
                if fmt in client.supported_codecs:
                    default_codec = fmt_type
                    break

        # create preset entries (for players that support it)
        preset_entries = tuple()
        if not (client and client.device_model in self._virtual_providers):
            presets = []
            async for playlist in self.mass.music.playlists.iter_db_items(True):
                presets.append(ConfigValueOption(playlist.name, playlist.uri))
            async for radio in self.mass.music.radio.iter_db_items(True):
                presets.append(ConfigValueOption(radio.name, radio.uri))
            # dynamically extend the amount of presets when needed
            if self.mass.config.get_raw_player_config_value(player_id, "preset_15"):
                preset_count = 20
            elif self.mass.config.get_raw_player_config_value(player_id, "preset_10"):
                preset_count = 15
            elif self.mass.config.get_raw_player_config_value(player_id, "preset_5"):
                preset_count = 10
            else:
                preset_count = 5
            preset_entries = tuple(
                ConfigEntry(
                    key=f"preset_{index}",
                    type=ConfigEntryType.STRING,
                    options=presets,
                    label=f"Preset {index}",
                    description="Assign a playable item to the player's preset. "
                    "Only supported on real squeezebox hardware or jive(lite) based emulators.",
                    advanced=False,
                    required=False,
                )
                for index in range(1, preset_count + 1)
            )

        return preset_entries + (
            ConfigEntry(
                key=CONF_SYNC_ADJUST,
                type=ConfigEntryType.INTEGER,
                range=(0, 1500),
                default_value=0,
                label="Audio synchronization delay correction",
                description="If this player is playing audio synced with other players "
                "and you always hear the audio too late on this player, "
                "you can shift the audio a bit.",
                advanced=True,
            ),
            CONF_ENTRY_CROSSFADE_DURATION,
            ConfigEntry.from_dict(
                {**CONF_ENTRY_OUTPUT_CODEC.to_dict(), "default_value": default_codec}
            ),
        )

    async def cmd_stop(self, player_id: str) -> None:
        """Send STOP command to given player."""
        # forward command to player and any connected sync child's
        for client in self._get_sync_clients(player_id):
            if client.state == SlimPlayerState.STOPPED:
                continue
            await client.stop()
            # workaround: some players do not send an event when playback stopped
            client._process_stat_stmu(b"")

    async def cmd_play(self, player_id: str) -> None:
        """Send PLAY command to given player."""
        # forward command to player and any connected sync child's
        async with asyncio.TaskGroup() as tg:
            for client in self._get_sync_clients(player_id):
                if client.state not in (
                    SlimPlayerState.PAUSED,
                    SlimPlayerState.BUFFERING,
                    SlimPlayerState.BUFFER_READY,
                ):
                    continue
                tg.create_task(client.play())

    async def cmd_play_url(
        self,
        player_id: str,
        url: str,
        queue_item: QueueItem | None,
    ) -> None:
        """Send PLAY URL command to given player.

        This is called when the Queue wants the player to start playing a specific url.
        If an item from the Queue is being played, the QueueItem will be provided with
        all metadata present.

            - player_id: player_id of the player to handle the command.
            - url: the url that the player should start playing.
            - queue_item: the QueueItem that is related to the URL (None when playing direct url).
        """
        # send stop first
        await self.cmd_stop(player_id)

        player = self.mass.players.get(player_id)
        if player.synced_to:
            raise RuntimeError("A synced player cannot receive play commands directly")

        # forward command to player and any connected sync child's
        sync_clients = [x for x in self._get_sync_clients(player_id)]
        async with asyncio.TaskGroup() as tg:
            for client in sync_clients:
                tg.create_task(
                    self._handle_play_url(
                        client,
                        url=url,
                        queue_item=queue_item,
                        send_flush=True,
                        auto_play=len(sync_clients) == 1,
                    )
                )

    async def cmd_handle_stream_job(self, player_id: str, stream_job: MultiClientStreamJob) -> None:
        """Handle StreamJob play command on given player."""
        # send stop first
        await self.cmd_stop(player_id)

        player = self.mass.players.get(player_id)
        if player.synced_to:
            raise RuntimeError("A synced player cannot receive play commands directly")
        sync_clients = [x for x in self._get_sync_clients(player_id)]
        async with asyncio.TaskGroup() as tg:
            for client in sync_clients:
                url = await stream_job.resolve_stream_url(client.player_id)
                tg.create_task(
                    self._handle_play_url(
                        client,
                        url=url,
                        queue_item=None,
                        send_flush=True,
                        auto_play=len(sync_clients) == 1,
                    )
                )

    async def _handle_play_url(
        self,
        client: SlimClient,
        url: str,
        queue_item: QueueItem | None,
        send_flush: bool = True,
        crossfade: bool = False,
        auto_play: bool = False,
    ) -> None:
        """Handle PlayMedia on slimproto player(s)."""
        player_id = client.player_id
        if crossfade:
            transition_duration = await self.mass.config.get_player_config_value(
                player_id, CONF_CROSSFADE_DURATION
            )
        else:
            transition_duration = 0

        await client.play_url(
            url=url,
            mime_type=f"audio/{url.split('.')[-1].split('?')[0]}",
            metadata={"item_id": queue_item.queue_item_id, "title": queue_item.name}
            if queue_item
            else None,
            send_flush=send_flush,
            transition=SlimTransition.CROSSFADE if crossfade else SlimTransition.NONE,
            transition_duration=transition_duration,
            # if autoplay=False playback will not start automatically
            # instead 'buffer ready' will be called when the buffer is full
            # to coordinate a start of multiple synced players
            autostart=auto_play,
        )

    async def cmd_pause(self, player_id: str) -> None:
        """Send PAUSE command to given player."""
        # forward command to player and any connected sync child's
        async with asyncio.TaskGroup() as tg:
            for client in self._get_sync_clients(player_id):
                if client.state not in (
                    SlimPlayerState.PLAYING,
                    SlimPlayerState.BUFFERING,
                    SlimPlayerState.BUFFER_READY,
                ):
                    continue
                tg.create_task(client.pause())

    async def cmd_power(self, player_id: str, powered: bool) -> None:
        """Send POWER command to given player."""
        if client := self._socket_clients.get(player_id):
            await client.power(powered)
            # store last state in cache
            await self.mass.cache.set(
                f"{CACHE_KEY_PREV_STATE}.{player_id}", (powered, client.volume_level)
            )

    async def cmd_volume_set(self, player_id: str, volume_level: int) -> None:
        """Send VOLUME_SET command to given player."""
        if client := self._socket_clients.get(player_id):
            await client.volume_set(volume_level)
            # store last state in cache
            await self.mass.cache.set(
                f"{CACHE_KEY_PREV_STATE}.{player_id}", (client.powered, volume_level)
            )

    async def cmd_volume_mute(self, player_id: str, muted: bool) -> None:
        """Send VOLUME MUTE command to given player."""
        if client := self._socket_clients.get(player_id):
            await client.mute(muted)

    async def cmd_sync(self, player_id: str, target_player: str) -> None:
        """Handle SYNC command for given player."""
        child_player = self.mass.players.get(player_id)
        assert child_player  # guard
        parent_player = self.mass.players.get(target_player)
        assert parent_player  # guard
        # always make sure that the parent player is part of the sync group
        parent_player.group_childs.add(parent_player.player_id)
        parent_player.group_childs.add(child_player.player_id)
        child_player.synced_to = parent_player.player_id
        # check if we should (re)start or join a stream session
        active_queue = self.mass.players.queues.get_active_queue(parent_player.player_id)
        if (
            ENABLE_EXPERIMENTAL_SYNC_JOIN
            and (stream_job := self.mass.streams.multi_client_jobs.get(active_queue.queue_id))
            and (stream_job.pending or stream_job.running)
        ):
            # this is a brave attempt to get players to to just join an existing stream
            # session without having to resume playback
            # it does not work reliable so far so consider this a WIP
            # for someone to pickup with a lot of patience and too much time
            url = await stream_job.resolve_stream_url(player_id)
            client = self._socket_clients[player_id]
            await self._handle_play_url(client, url, None, auto_play=True)
        elif parent_player.state == PlayerState.PLAYING:
            # playback needs to be restarted to form a new multi client stream session
            await self.mass.players.queues.resume(active_queue.queue_id, fade_in=False)
        else:
            # make sure that the player manager gets an update
            self.mass.players.update(child_player.player_id)
            self.mass.players.update(parent_player.player_id)

    async def cmd_unsync(self, player_id: str) -> None:
        """Handle UNSYNC command for given player."""
        child_player = self.mass.players.get(player_id)
        parent_player = self.mass.players.get(child_player.synced_to)
        # make sure to send stop to the player
        await self.cmd_stop(child_player.player_id)
        child_player.synced_to = None
        with suppress(KeyError):
            parent_player.group_childs.remove(child_player.player_id)
        if parent_player.group_childs == {parent_player.player_id}:
            # last child vanished; the sync group is dissolved
            parent_player.group_childs.remove(parent_player.player_id)
        self.mass.players.update(child_player.player_id)
        self.mass.players.update(parent_player.player_id)

    def register_virtual_provider(
        self,
        player_model: str,
        register_callback: Callable,
        update_callback: Callable,
    ) -> None:
        """Register a virtual provider based on slimproto, such as the airplay bridge."""
        self._virtual_providers[player_model] = (
            register_callback,
            update_callback,
        )

    def unregister_virtual_provider(
        self,
        player_model: str,
    ) -> None:
        """Unregister a virtual provider."""
        self._virtual_providers.pop(player_model, None)

    def _handle_player_update(self, client: SlimClient) -> None:
        """Process SlimClient update/add to Player controller."""
        player_id = client.player_id
        virtual_provider_info = self._virtual_providers.get(client.device_model)
        player = self.mass.players.get(player_id, raise_unavailable=False)
        if not player:
            # player does not yet exist, create it
            player = Player(
                player_id=player_id,
                provider=self.domain,
                type=PlayerType.PLAYER,
                name=client.name,
                available=True,
                powered=client.powered,
                device_info=DeviceInfo(
                    model=client.device_model,
                    address=client.device_address,
                    manufacturer=client.device_type,
                ),
                supported_features=(
                    PlayerFeature.ACCURATE_TIME,
                    PlayerFeature.POWER,
                    PlayerFeature.SYNC,
                    PlayerFeature.VOLUME_MUTE,
                    PlayerFeature.VOLUME_SET,
                ),
                max_sample_rate=int(client.max_sample_rate),
            )
            if virtual_provider_info:
                # if this player is part of a virtual provider run the callback
                virtual_provider_info[0](player)
            self.mass.players.register_or_update(player)

        # update player state on player events
        player.available = True
        player.current_url = client.current_url
        player.name = client.name
        player.powered = client.powered
        player.state = STATE_MAP[client.state]
        player.volume_level = client.volume_level
        # player.volume_muted = client.muted
        player.volume_muted = client.powered and client.muted
        # set all existing player ids in `can_sync_with` field
        player.can_sync_with = tuple(
            x.player_id for x in self._socket_clients.values() if x.player_id != player_id
        )
        if virtual_provider_info:
            # if this player is part of a virtual provider run the callback
            virtual_provider_info[1](player)
        self.mass.players.update(player_id)

    def _handle_player_heartbeat(self, client: SlimClient) -> None:
        """Process SlimClient elapsed_time update."""
        if client.state == SlimPlayerState.STOPPED:
            # ignore server heartbeats when stopped
            return

        # elapsed time change on the player will be auto picked up
        # by the player manager.
        player = self.mass.players.get(client.player_id)
        player.elapsed_time = client.elapsed_seconds
        player.elapsed_time_last_updated = time.time()

        # handle sync
        if player.synced_to:
            self._handle_client_sync(client)

    async def _handle_output_underrun(self, client: SlimClient) -> None:
        """Process SlimClient Output Underrun Event."""
        player = self.mass.players.get(client.player_id)
        self.logger.error("Player %s ran out of buffer", player.display_name)
        if player.synced_to:
            # if player is synced, resync it
            await self.cmd_sync(player.player_id, player.synced_to)
        else:
            await self.cmd_stop(client.player_id)

    def _handle_client_sync(self, client: SlimClient) -> None:
        """Synchronize audio of a sync client."""
        player = self.mass.players.get(client.player_id)
        sync_master_id = player.synced_to
        if not sync_master_id:
            # we only correct sync child's, not the sync master itself
            return
        if sync_master_id not in self._socket_clients:
            return  # just here as a guard as bad things can happen

        sync_master = self._socket_clients[sync_master_id]

        if sync_master.state != SlimPlayerState.PLAYING:
            return
        if client.state != SlimPlayerState.PLAYING:
            return

        if backoff_time := self._do_not_resync_before.get(client.player_id):  # noqa: SIM102
            # player has set a timestamp we should backoff from syncing it
            if time.time() < backoff_time:
                return

        # we collect a few playpoints of the player to determine
        # average lag/drift so we can adjust accordingly
        sync_playpoints = self._sync_playpoints.setdefault(
            client.player_id, deque(maxlen=MIN_REQ_PLAYPOINTS)
        )

        active_queue = self.mass.players.queues.get_active_queue(client.player_id)
        stream_job = self.mass.streams.multi_client_jobs.get(active_queue.queue_id)
        if not stream_job:
            # should not happen, but just in case
            return

        last_playpoint = sync_playpoints[-1] if sync_playpoints else None
        if last_playpoint and (time.time() - last_playpoint.timestamp) > 10:
            # last playpoint is too old, invalidate
            sync_playpoints.clear()
        if last_playpoint and last_playpoint.sync_job_id != stream_job.job_id:
            # streamjob has changed, invalidate
            sync_playpoints.clear()

        diff = int(
            self._get_corrected_elapsed_milliseconds(sync_master)
            - self._get_corrected_elapsed_milliseconds(client)
        )

        # we can now append the current playpoint to our list
        sync_playpoints.append(SyncPlayPoint(time.time(), stream_job.job_id, diff))

        if len(sync_playpoints) < MIN_REQ_PLAYPOINTS:
            return

        # get the average diff
        avg_diff = statistics.fmean(x.diff for x in sync_playpoints)
        delta = abs(avg_diff)

        if delta < MIN_DEVIATION_ADJUST:
            return

        # resync the player by skipping ahead or pause for x amount of (milli)seconds
        sync_playpoints.clear()
        if avg_diff > 0:
            # handle player lagging behind, fix with skip_ahead
            self.logger.debug("%s resync: skipAhead %sms", player.display_name, delta)
            self._do_not_resync_before[client.player_id] = time.time() + 2
            asyncio.create_task(self._skip_over(client.player_id, delta))
        else:
            # handle player is drifting too far ahead, use pause_for to adjust
            self.logger.debug("%s resync: pauseFor %sms", player.display_name, delta)
            self._do_not_resync_before[client.player_id] = time.time() + (delta / 1000) + 2
            asyncio.create_task(self._pause_for(client.player_id, delta))

    async def _handle_decoder_ready(self, client: SlimClient) -> None:
        """Handle decoder ready event, player is ready for the next track."""
        if not client.current_metadata:
            return
        player = self.mass.players.get(client.player_id)
        if player.synced_to:
            # handled by sync master
            return
        if client.state == SlimPlayerState.STOPPED:
            return
        if player.active_source != player.player_id:
            return
        try:
            next_url, next_item, crossfade = await self.mass.players.queues.preload_next_url(
                client.player_id
            )
            async with asyncio.TaskGroup() as tg:
                for client in self._get_sync_clients(client.player_id):
                    tg.create_task(
                        self._handle_play_url(
                            client,
                            url=next_url,
                            queue_item=next_item,
                            send_flush=False,
                            crossfade=crossfade,
                            auto_play=True,
                        )
                    )
        except QueueEmpty:
            pass

    async def _handle_buffer_ready(self, client: SlimClient) -> None:
        """Handle buffer ready event, player has buffered a (new) track."""
        player = self.mass.players.get(client.player_id)
        if player.synced_to:
            # unpause of sync child is handled by sync master
            return
        if not player.group_childs:
            # not a sync group, continue
            await client.play()
            return
        count = 0
        while count < 40:
            childs_total = 0
            childs_ready = 0
            for sync_child in self._get_sync_clients(player.player_id):
                childs_total += 1
                if sync_child.state == SlimPlayerState.BUFFER_READY:
                    childs_ready += 1
            if childs_total == childs_ready:
                break
            await asyncio.sleep(0.1)
        # all child's ready (or timeout) - start play
        async with asyncio.TaskGroup() as tg:
            for client in self._get_sync_clients(player.player_id):
                timestamp = client.jiffies + 20
                self._do_not_resync_before[client.player_id] = time.time() + 1
                tg.create_task(client.send_strm(b"u", replay_gain=int(timestamp)))

    async def _handle_connected(self, client: SlimClient) -> None:
        """Handle a client connected event."""
        player_id = client.player_id
        if existing := self._socket_clients.pop(player_id, None):
            # race condition: new socket client connected while
            # the old one has not yet been cleaned up
            existing.disconnect()

        self._socket_clients[player_id] = client
        # update all attributes
        self._handle_player_update(client)
        # update existing players so they can update their `can_sync_with` field
        for item in self._socket_clients.values():
            if item.player_id == player_id:
                continue
            self._handle_player_update(item)
        # restore volume and power state
        if last_state := await self.mass.cache.get(f"{CACHE_KEY_PREV_STATE}.{player_id}"):
            init_power = last_state[0]
            init_volume = last_state[1]
        else:
            init_volume = DEFAULT_PLAYER_VOLUME
            init_power = False
        await client.power(init_power)
        await client.volume_set(init_volume)

    async def _handle_disconnected(self, client: SlimClient) -> None:
        """Handle a client disconnected event."""
        if self.mass.closing:
            return
        player_id = client.player_id
        if client := self._socket_clients.pop(player_id, None):
            # store last state in cache
            await self.mass.cache.set(
                f"{CACHE_KEY_PREV_STATE}.{player_id}", (client.powered, client.volume_level)
            )
        if player := self.mass.players.get(player_id):
            player.available = False
            self.mass.players.update(player_id)

    async def _pause_for(self, client_id: str, millis: int) -> None:
        """Handle pause for x amount of time to help with syncing."""
        client = self._socket_clients[client_id]
        # https://wiki.slimdevices.com/index.php/SlimProto_TCP_protocol.html#u.2C_p.2C_a_.26_t_commands_and_replay_gain_field§
        await client.send_strm(b"p", replay_gain=int(millis))

    async def _skip_over(self, client_id: str, millis: int) -> None:
        """Handle skip for x amount of time to help with syncing."""
        client = self._socket_clients[client_id]
        # https://wiki.slimdevices.com/index.php/SlimProto_TCP_protocol.html#u.2C_p.2C_a_.26_t_commands_and_replay_gain_field
        await client.send_strm(b"a", replay_gain=int(millis))

    def _get_sync_clients(self, player_id: str) -> Generator[SlimClient]:
        """Get all sync clients for a player."""
        player = self.mass.players.get(player_id)
        # we need to return the player itself too
        group_child_ids = {player_id}
        group_child_ids.update(player.group_childs)
        for child_id in group_child_ids:
            if client := self._socket_clients.get(child_id):
                yield client

    def _get_corrected_elapsed_milliseconds(self, client: SlimClient) -> int:
        """Return corrected elapsed milliseconds."""
        skipped_millis = 0
        active_queue = self.mass.players.queues.get_active_queue(client.player_id)
        if stream_job := self.mass.streams.multi_client_jobs.get(active_queue.queue_id):
            skipped_millis = stream_job.client_seconds_skipped.get(client.player_id, 0) * 1000
        sync_delay = self.mass.config.get_raw_player_config_value(
            client.player_id, CONF_SYNC_ADJUST, 0
        )
        current_millis = int(skipped_millis + client.elapsed_milliseconds)
        if sync_delay != 0:
            return current_millis - sync_delay
        return current_millis


class SlimClient(SlimClientOrg):
    """Patched SLIMProto socket client."""

    def _process_stat_stmo(self, data: bytes) -> None:  # noqa: ARG002
        """Process incoming stat STMo message: Output Underrun."""
        self.callback("output_underrun", self)
