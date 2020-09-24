from discord.ext import commands, menus
import discord

import asyncio
from async_timeout import timeout
import itertools
import logging
import random
import traceback
import sys

from . import stopwatch
from .ytdl import Song


log = logging.getLogger("clam.music.player")


def hover_link(ctx, msg, text="`?`"):
    return (
        f"[{text}](https://www.discordapp.com/"
        f"channels/{ctx.guild.id}/{ctx.channel.id} "
        f""""{msg}")"""
    )


class SearchPages(menus.ListPageSource):
    def __init__(self, data, total_duration):
        super().__init__(data, per_page=10)
        self.total_duration = total_duration

    async def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page
        ctx = menu.ctx

        hover = hover_link(ctx, "Song Title", text="Song")
        queue = f"`#` {hover} `Duration` @Requester\n\n"
        for i, song in enumerate(entries, start=offset):
            queue += f"`{i+1}.` [{song.title}]({song.url}) `{song.duration}` {song.requester.mention}\n"

        em = discord.Embed(
            title="**:page_facing_up: Queue**",
            description=f"**{len(ctx.player.songs)} Song(s):**\n{queue}",
            color=discord.Color.green(),
        )
        if ctx.player.loop_queue:
            em.title += " (:repeat: looping)"
            em.description = "**:repeat: Loop queue is on**\n" + em.description
        if ctx.player.loop:
            em.title += " (:repeat_one: looping)"
            em.description = "**:repeat_one: Loop single is on**\n" + em.description
        em.description += (
            f"\nTotal duration: {self.total_duration}\n\n"
            f"To see what's currently playing, use `{ctx.prefix}now`"
        )
        em.set_footer(text=f"Page {menu.current_page+1} of {self.get_max_pages()}")
        return em


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
        self._notify = True

        self._loop = False
        self._loop_queue = False
        self._volume = 0.5
        self._votes = {"skip": set(), "shuffle": set(), "remove": set()}

        log.info(f"{ctx.guild}: Starting player loop...")
        self.audio_player = bot.loop.create_task(self.player_loop())

    def __del__(self):
        self.audio_player.cancel()

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

    def create_duration(self, current, total):
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

    def now_playing_embed(self, title="Now playing", duration=None):
        src = self.current
        em = discord.Embed(
            title=title,
            description=f"```css\n{src.title}\n```",
            color=discord.Color.green(),
        )
        if not duration:
            em.add_field(name="Duration", value=src.duration)
        else:
            seconds = duration.total_seconds()
            formatted = Song.timestamp_duration(int(seconds))
            bar = self.create_duration(seconds, src.total_seconds)
            em.add_field(
                name="Duration", value=f"{formatted}/{src.duration} {bar}", inline=False
            )
        em.add_field(name="Requested by", value=src.requester.mention)
        em.add_field(name="Uploader", value=f"[{src.uploader}]({src.uploader_url})")
        em.add_field(name="URL", value=f"[Click]({src.url})")
        em.set_thumbnail(url=src.thumbnail)

        return em

    async def player_loop(self):
        ctx = self.ctx
        try:
            while not self.bot.is_closed() and not self.closed:
                self.next.clear()
                self.duration.stop()

                if self.loop_queue:
                    await self.songs.put(self.current)

                if not self.loop:
                    try:
                        async with timeout(180):  # 3 minutes
                            log.info(f"{ctx.guild}: Getting a song from the queue...")
                            self.current = await self.songs.get()
                    except asyncio.TimeoutError:
                        log.info(f"{ctx.guild}: Timed out while waiting for song. Stopping...")
                        self.bot.loop.create_task(self.stop())
                        return

                self.current.volume = self._volume

                self.current.make_source()

                log.info(f"{ctx.guild}: Playing song '{self.current.title}'")
                self.voice.play(self.current.source, after=self.play_next_song)

                # Start our stopwatch for keeping track of position
                self.duration.start()

                if not self.loop and self.notify and not self.startover:
                    await self.text_channel.send(f"**:notes: Now playing** `{self.current.title}`")

                self.startover = False

                for vote, value in self._votes.items():
                    self._votes[vote].clear()

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
        self._votes["skip"].clear()

        if self.is_playing:
            self.voice.stop()

    async def stop(self):
        self.closed = True

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

    def resume(self):
        if self.is_playing and self.voice.is_paused():
            log.info(f"{self.ctx.guild}: Resuming...")
            self.voice.resume()
            self.duration.unpause()