import asyncio
import enum
import itertools
import logging
import random
import sys
import traceback

import discord
from async_timeout import timeout
from discord.ext import commands

from . import humantime, stopwatch
from .ytdl import Song


log = logging.getLogger("clam.music.player")


class VoiceError(commands.CommandError):
    pass


class SongQueue(asyncio.Queue):
    def __getitem__(self, item):
        if isinstance(item, slice):
            return list(itertools.islice(self._queue, item.start, item.stop, item.step))
        else:
            return self._queue[item]

    def __iter__(self):
        return self._queue.__iter__()

    def __len__(self):
        return self.qsize()

    def clear(self):
        self._queue.clear()

    def shuffle(self):
        random.shuffle(self._queue)

    def remove(self, index: int):
        del self._queue[index]

    def to_list(self):
        output = []
        for item in self._queue:
            output.append(item)
        return output


class PlayerStatus(enum.Enum):
    PLAYING = 0
    PAUSED = 1
    WAITING = 2
    CLOSED = 3


class Player:
    def __init__(self, bot: commands.Bot, ctx: commands.Context):
        log.info(f"{ctx.guild}: Creating player...")

        self.bot = bot
        self.ctx = ctx

        self.current = None
        self.voice = None
        self.text_channel = ctx.channel
        self.next = asyncio.Event()
        self.songs = SongQueue()
        self.duration = stopwatch.StopWatch()
        self.closed = False
        self.startover = False

        self.status = PlayerStatus.WAITING

        self._notify = False
        self._loop = False
        self._loop_queue = False
        self._volume = 0.5
        self._votes = {}

        log.info(f"{ctx.guild}: Starting player loop...")
        self.audio_player = bot.loop.create_task(self.player_loop())

    def __del__(self):
        self.audio_player.cancel()

    def __repr__(self):
        channel = self.voice.channel if self.voice else None
        return f"<Player guild='{self.ctx.guild}', channel='{channel}'>"

    @property
    def loop(self):
        return self._loop

    @loop.setter
    def loop(self, value: bool):
        self._loop = value

    @property
    def notify(self):
        return self._notify

    @notify.setter
    def notify(self, value: bool):
        self._notify = value

    @property
    def loop_queue(self):
        return self._loop_queue

    @loop_queue.setter
    def loop_queue(self, value: bool):
        self._loop_queue = value

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = value

    @property
    def is_playing(self):
        if self.voice:
            if self.voice.is_paused():
                # The player is techincally in
                # the middle of playing a song
                return True
            return self.voice.is_playing() is True and self.current is not None
        return self.voice is not None and self.current is not None

    @property
    def has_started(self):
        return self.voice is not None and self.current is not None

    @staticmethod
    def create_duration(current, total):
        decimal = current / total
        position = round(decimal * 30)
        bar = "`"
        for i in range(30):
            to_add = "▬"
            if position == i:
                to_add = "🔘"
            bar += to_add
        bar += "`"
        return bar

    @staticmethod
    def now_playing_embed(song, title="Now playing", *, duration=None, db_info=False, filesize=None):
        em = discord.Embed(
            title=title,
            description=f"```yml\n{song.title}\n```",
            color=discord.Color.green(),
        )
        if not duration:
            em.add_field(name="Duration", value=song.duration)
        else:
            seconds = duration.total_seconds()
            formatted = Song.timestamp_duration(int(seconds))
            bar = Player.create_duration(seconds, song.total_seconds)
            em.add_field(
                name="Duration",
                value=f"{formatted}/{song.duration} {bar}",
                inline=False,
            )

        if not db_info:
            em.add_field(name="Requested by", value=song.requester.mention)

        em.add_field(name="Uploader", value=f"[{song.uploader}]({song.uploader_url})")
        em.add_field(name="URL", value=f"[Click]({song.url})")
        em.set_thumbnail(url=song.thumbnail)

        if song.database:
            em.set_footer(text=f"Song cached in database (ID: {song.db_id}). Last updated")
            em.timestamp = song.last_updated

            if db_info:
                em.add_field(name="Plays", value=f"{song.plays:,}")
                em.add_field(name="First cached", value=humantime.fulltime(song.registered_at))
                em.add_field(name="Filename", value=f"`{song.filename}`")
                em.add_field(name="Platform ID", value=song.id)

                if filesize:
                    em.add_field(name="File size", value=filesize)

        return em

    async def player_loop(self):
        ctx = self.ctx
        try:
            while not self.bot.is_closed() and not self.closed:
                self.next.clear()
                self.duration.stop()

                if self.loop_queue and not self.startover:
                    await self.songs.put(self.current)

                if not self.loop:
                    self.status = PlayerStatus.WAITING
                    try:
                        async with timeout(180):  # 3 minutes
                            log.info(f"{ctx.guild}: Getting a song from the queue...")
                            self.current = await self.songs.get()
                    except asyncio.TimeoutError:
                        log.info(
                            f"{ctx.guild}: Timed out while waiting for song. Stopping..."
                        )
                        await self.stop()

                        if (
                            ctx.guild.id in ctx.bot.players.keys()
                            and ctx.bot.players[ctx.guild.id] is self
                        ):
                            del ctx.bot.players[ctx.guild.id]
                        return

                self.current.volume = self._volume

                self.current.make_source()
                self.current.ffmpeg_options = self.current.FFMPEG_OPTIONS.copy()

                log.info(f"{ctx.guild}: Playing song '{self.current.title}'")
                self.voice.play(self.current.source, after=self.play_next_song)

                # Start our stopwatch for keeping track of position
                self.duration.start()

                # Set status to playing
                self.status = PlayerStatus.PLAYING

                query = "UPDATE songs SET plays = plays + 1 WHERE song_id=$1 AND extractor=$2;"
                await self.bot.pool.execute(
                    query, self.current.id, self.current.extractor
                )

                if not self.loop and self.notify and not self.startover:
                    await self.text_channel.send(
                        f"**:notes: Now playing** `{self.current.title}`"
                    )

                self.startover = False

                self._votes.clear()

                await self.next.wait()

                self.current.discard_source()

        except Exception as exc:
            log.warning(f"{ctx.guild}: Exception in player_loop")
            traceback.print_exception(
                type(exc), exc, exc.__traceback__, file=sys.stderr
            )

            log.info(f"{ctx.guild}: Restarting task...")
            self.audio_player.cancel()
            self.audio_player = self.bot.loop.create_task(self.player_loop())

    def play_next_song(self, error=None):
        if error:
            raise VoiceError(str(error))

        self.next.set()

    def skip(self):
        if self.is_playing:
            self.voice.stop()

    async def stop(self):
        self.closed = True
        self.status = PlayerStatus.CLOSED

        if self.voice:
            log.info(f"{self.ctx.guild}: Stopping and disconnecting from voice")
            self.voice.stop()
            await self.voice.disconnect()
            self.voice = None

        self.songs.clear()

    def pause(self):
        if self.is_playing and self.voice.is_playing():
            log.info(f"{self.ctx.guild}: Pausing...")
            self.voice.pause()
            self.duration.pause()
            self.status = PlayerStatus.PAUSED

    def resume(self):
        if self.is_playing and self.voice.is_paused():
            log.info(f"{self.ctx.guild}: Resuming...")
            self.voice.resume()
            self.duration.unpause()
            self.status = PlayerStatus.PLAYING
