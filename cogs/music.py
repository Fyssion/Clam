from discord.ext import commands
import discord

import asyncio
import youtube_dl
import functools
import itertools
import math
import random
from async_timeout import timeout
import re
from datetime import datetime as d
from urllib.parse import urlparse
import importlib
import sys
import os
import traceback

from .utils import utils
from .utils import stopwatch


# Silence useless bug reports messages
youtube_dl.utils.bug_reports_message = lambda: ""


class VoiceError(Exception):
    pass


class YTDLError(Exception):
    pass


class YTDLSource(discord.PCMVolumeTransformer):
    YTDL_OPTIONS = {
        "format": "bestaudio/best",
        "extractaudio": True,
        "audioformat": "mp3",
        "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
        "restrictfilenames": True,
        "noplaylist": False,
        "nocheckcertificate": True,
        "ignoreerrors": False,
        "logtostderr": False,
        "quiet": True,
        "no_warnings": True,
        "default_search": "auto",
        "source_address": "0.0.0.0",
    }

    # FFMPEG_OPTIONS = {
    #     "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    #     "options": "-vn",
    # }
    FFMPEG_OPTIONS = {
        "before_options": None,
        "options": None,
    }

    ytdl = youtube_dl.YoutubeDL(YTDL_OPTIONS)

    def __init__(self, ctx: commands.Context,
                 source: discord.FFmpegPCMAudio, *, data: dict,
                 volume: float = 0.5, filename = None):
        super().__init__(source, volume)

        self.ytdl_source = source

        self.ctx = ctx
        self.requester = ctx.author
        self.channel = ctx.channel
        self.data = data
        self.filename = filename

        self.uploader = data.get("uploader")
        self.uploader_url = data.get("uploader_url")
        date = data.get("upload_date")
        self.upload_date = date[6:8] + "." + date[4:6] + "." + date[0:4]
        self.title = data.get("title")
        self.thumbnail = data.get("thumbnail")
        self.description = data.get("description")
        self.human_duration = self.parse_duration(int(data.get("duration")))
        self.duration = self.timestamp_duration(int(data.get("duration")))
        self.tags = data.get("tags")
        self.url = data.get("webpage_url")
        self.views = data.get("view_count")
        self.likes = data.get("like_count")
        self.dislikes = data.get("dislike_count")
        self.stream_url = data.get("url")

    def __str__(self):
        return f"`{self.title}`"

    def remake_source(self):
        print(self.filename)
        return YTDLSource(self.ctx, discord.FFmpegPCMAudio(self.filename, **self.FFMPEG_OPTIONS), data=self.data, filename=self.filename)

    @classmethod
    async def create_source(cls, ctx: commands.Context,
                            search: str, *, loop: asyncio.BaseEventLoop = None,
                            send_errors=True):
        loop = loop or asyncio.get_event_loop()

        partial = functools.partial(cls.ytdl.extract_info, search, download=False, process=False)
        try:
            data = await loop.run_in_executor(None, partial)
        except youtube_dl.DownloadError as e:
            print(e)
            if send_errors:
                await ctx.send(f"**:x: Error while searching for** `{search}`")
            return

        if data is None:
            raise YTDLError("Couldn't find anything that matches `{}`".format(search))

        if "entries" not in data:
            process_info = data
        else:
            process_info = None
            for entry in data["entries"]:
                if entry:
                    process_info = entry
                    break

            if process_info is None:
                raise YTDLError("Couldn't find anything that matches `{}`".format(search))

        webpage_url = process_info["webpage_url"]
        filename = cls.ytdl.prepare_filename(process_info)[:-3] + ".webm"
        print(filename)
        if os.path.isfile(filename):
            print("not downloading")
            download = False
        else:
            download = True
        partial = functools.partial(cls.ytdl.extract_info, webpage_url, download=download)
        try:
            processed_info = await loop.run_in_executor(None, partial)
        except youtube_dl.DownloadError as e:
            print(e)
            if send_errors:
                await ctx.send(f"**:x: Error while downloading** `{webpage_url}`")
                return
        else:
            if processed_info is None:
                raise YTDLError("Couldn't fetch `{}`".format(webpage_url))

            if "entries" not in processed_info:
                info = processed_info
            else:
                info = None
                while info is None:
                    try:
                        info = processed_info["entries"].pop(0)
                    except IndexError as e:
                        print(e)
                        raise YTDLError("Couldn't retrieve any matches for `{}`".format(webpage_url))
            filename = cls.ytdl.prepare_filename(info)
            return cls(ctx, discord.FFmpegPCMAudio(filename, **cls.FFMPEG_OPTIONS), data=info, filename=filename)

    @classmethod
    async def get_playlist(cls, ctx: commands.Context, search: str, *, loop: asyncio.BaseEventLoop = None):
        loop = loop or asyncio.get_event_loop()

        partial = functools.partial(cls.ytdl.extract_info, search, download=False, process=False)
        unproccessed = await loop.run_in_executor(None, partial)

        if unproccessed is None:
            raise YTDLError("Couldn't find anything that matches `{}`".format(search))

        if "entries" not in unproccessed:
            data_list = unproccessed
        else:
            data_list = []
            for entry in unproccessed["entries"]:
                if entry:
                    data_list.append(entry)

            if len(data_list) == 0:
                raise YTDLError("Playlist is empty")

        playlist = []
        counter = 0
        for video in data_list:
            print(str(video))
            webpage_url = video["url"]
            full = functools.partial(cls.ytdl.extract_info, webpage_url, download=True)
            try:
                data = await loop.run_in_executor(None, full)
            except youtube_dl.DownloadError:
                counter += 1
            else:

                if data is None:
                    await ctx.send(f"Couldn't fetch `{webpage_url}`")

                if "entries" not in data:
                    info = data
                else:
                    info = None
                    while info is None:
                        try:
                            info = data["entries"].pop(0)
                        except IndexError as e:
                            print(e)
                            await ctx.send(f"Couldn't retrieve any matches for `{webpage_url}`")
                filename = cls.ytdl.prepare_filename(info)
                source = cls(ctx, discord.FFmpegPCMAudio(filename, **cls.FFMPEG_OPTIONS), data=info, filename=filename)
                playlist.append(source)

        return playlist, counter


    @staticmethod
    def parse_duration(duration: int):
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)

        duration_str = []
        if days > 0:
            duration_str.append("{} days".format(days))
        if hours > 0:
            duration_str.append("{} hours".format(hours))
        if minutes > 0:
            duration_str.append("{} minutes".format(minutes))
        if seconds > 0:
            duration_str.append("{} seconds".format(seconds))

        if len(duration_str) == 0:
            return YTDLSource.timestamp_duration(duration)

        return ", ".join(duration_str)

    @staticmethod
    def timestamp_duration(duration: int):
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)

        duration = ""
        if hours > 0:
            duration += (f"{hours}:")
            minutes = f"{minutes:02d}"
        duration += (f"{minutes}:{seconds:02d}")
        return duration


class Song:
    __slots__ = ("source", "requester")

    def __init__(self, source: YTDLSource):
        self.source = source
        self.requester = source.requester

    def create_embed(self, title="Now playing", duration=None):
        src = self.source
        em = discord.Embed(
            title=title,
            description=f"```css\n{src.title}\n```",
            color=discord.Color.blurple()
        )
        if not duration:
            em.add_field(name="Duration", value=src.duration)
        else:
            duration = duration.total_seconds()
            formatted = YTDLSource.timestamp_duration(int(duration))
            print(formatted)
            em.add_field(name="Duration", value=f"{formatted}/{src.duration}")
        em.add_field(name="Requested by",
                     value=self.requester.mention)
        em.add_field(
            name="Uploader",
            value=f"[{src.uploader}]({src.uploader_url})"
        )
        em.add_field(name="URL", value=f"[Click]({src.url})")
        em.set_thumbnail(url=self.source.thumbnail)

        return em

    def create_message(self):
        return f"**:notes: Now playing** `{self.source.title}`"


class SongQueue(asyncio.Queue):
    def __getitem__(self, item):
        if isinstance(item, slice):
            return list(itertools.islice(self._queue,
            item.start, item.stop, item.step))
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
        self.bot = bot
        self._ctx = ctx

        self.current = None
        self.voice = None
        self.text_channel = ctx.channel
        self.next = asyncio.Event()
        self.songs = SongQueue()
        self.saved_queue = SongQueue()
        self.duration = stopwatch.StopWatch()

        self._loop = False
        self._loop_queue = False
        self._volume = 0.5
        self._votes = {
            "skip": set(),
            "shuffle": set(),
            "remove": set()
            }

        self.audio_player = bot.loop.create_task(
            self.player_loop()
        )

    def __del__(self):
        self.audio_player.cancel()

    @property
    def loop(self):
        return self._loop

    @loop.setter
    def loop(self, value: bool):
        self._loop = value

    @property
    def loop_queue(self):
        return self._loop_queue

    @loop_queue.setter
    def loop_queue(self, value: bool):
        self._loop_queue = value
        if self._loop_queue:
            self.saved_queue = self.songs
        else:
            self.saved_queue = SongQueue()

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
            return (self.voice.is_playing() is True and
                    self.current is not None)
        return self.voice is not None and self.current is not None

    @property
    def has_started(self):
        return self.voice is not None and self.current is not None

    async def player_loop(self):
        while True:
            self.next.clear()
            self.duration.stop()

            if self.loop_queue:
                self.current.source = self.current.source.remake_source()
                await self.songs.put(self.current)
            if self.loop:
                self.current.source = self.current.source.remake_source()
            print(1)
            if not self.loop and not self.loop_queue and self.current:
                os.remove(self.current.source.filename)
            print(2)
            if not self.loop:
                try:
                    async with timeout(180):  # 3 minutes
                        self.current = await self.songs.get()
                except asyncio.TimeoutError:
                    self.bot.loop.create_task(self.stop())
                    return
            print(3)
            self.current.source.volume = self._volume
            self.voice.play(self.current.source, after=self.play_next_song)
            self.duration.start()
            print(4)
            if not self.loop:
                await self.text_channel.send(self.current.create_message())
            # else:
            #     await self.current.source.channel.send("Looping...")

            for vote, value in self._votes.items():
                self._votes[vote].clear()

            print("Before wait")
            await self.next.wait()
            print("After wait")

    def play_next_song(self, error=None):
        if error:
            raise VoiceError(str(error))

        self.next.set()

    def skip(self):
        self._votes["skip"].clear()

        if self.is_playing:
            self.voice.stop()

    async def stop(self):
        filenames = [s.source.filename for s in self.songs._queue]
        if self.current:
            filenames.insert(0, self.current.source.filename)
        self.songs.clear()
        if self.voice:
            await self.voice.disconnect()
            self.voice = None
        for filename in filenames:
            print(filename)
            if os.path.isfile(filename):
                os.remove(filename)

    def pause(self):
        if self.is_playing and self.voice.is_playing():
            self.voice.pause()
            self.duration.pause()

    def resume(self):
        if self.is_playing and self.voice.is_paused():
            self.voice.resume()
            self.duration.unpause()


def is_dj():
    def predicate(ctx):
        author = ctx.author
        upper = discord.utils.get(ctx.guild.roles, name="DJ")
        lower = discord.utils.get(ctx.guild.roles, name="dj")
        return (author.guild_permissions.manage_guild or
                upper in author.roles or
                lower in author.roles)
    return commands.check(predicate)


class Music(commands.Cog, name=":notes: Music"):
    """Listen to music in any voice channel!\nUse `c.play` to play a song."""
    def __init__(self, bot):
        self.bot = bot
        self.players = {}

    def get_player(self, ctx: commands.Context):
        state = self.players.get(ctx.guild.id)
        if not state:
            try:
                state = Player(self.bot, ctx)
            except Exception as e:
                print(e)
            self.players[ctx.guild.id] = state

        return state

    def cog_unload(self):
        for state in self.players.values():
            self.bot.loop.create_task(state.stop())

    def cog_check(self, ctx):
        if not ctx.guild:
            raise commands.NoPrivateMessage("This command can't be used in DM channels.")

        return True

    async def cog_before_invoke(self, ctx):
        ctx.player = self.get_player(ctx)

    async def cog_command_error(self, ctx, error: commands.CommandError):
        await ctx.send(f"Oops: {str(error)}")
        print(str(error))

    @commands.Cog.listener("on_voice_state_update")
    async def on_voice_leave(self, member, before, after):
        def check(mem, bf, af):
            if mem.bot:
                return False
            if af.channel:
                return True
        if member.bot:
            return
        player = self.players.get(member.guild.id)
        if not player:
            return
        if len(player.voice.channel.members) == 1:
            player.pause()
            print("PAUSED")
            try:
                await self.bot.wait_for("voice_state_update", timeout=120, check=check)
            except asyncio.TimeoutError:
                if len(player.songs) > 0:
                    songs = player.songs.to_list()
                    songs = [s.source.url for s in songs]
                    songs.insert(0, player.current.source.url)
                else:
                    songs = None
                await player.stop()
                del self.players[member.guild.id]
                if songs:
                    url = await self.post("\n".join(songs))
                    if url is None:
                        return await player.text_channel.send("Sorry, I couldn't save your queue.")
                    await player.text_channel.send("**I saved your queue!**\n"
                                f"To resume where you left off, use this link with the `play` command: **{url}**")
            print("Resuming!")
            player.resume()

    async def votes(self, ctx, cmd: str, func, param=None):
        async def run_func():
            if param:
                await func(param)
            else:
                await func()
        voter = ctx.message.author

        if_is_requester = (voter == ctx.player.current.requester)
        if_has_perms = voter.guild_permissions.manage_guild

        upper = discord.utils.get(ctx.guild.roles, name="DJ")
        lower = discord.utils.get(ctx.guild.roles, name="dj")
        if_is_dj = upper in voter.roles or lower in voter.roles

        if len(ctx.player.voice.channel.members) < 5:
            if len(ctx.player.voice.channel.members) < 3:
                is_only_user = True
            else:
                is_only_user = False
                required_votes = len(ctx.player.voice.channel.members) - 1
        else:
            is_only_user = False
            required_votes = 3
        if if_is_requester or if_has_perms or is_only_user or if_is_dj:
            await run_func()

        elif voter.id not in ctx.player._votes[cmd]:
            ctx.player._votes[cmd].add(voter.id)
            total_votes = len(ctx.player._votes[cmd])

            if total_votes >= required_votes:
                ctx.player._votes[cmd].clear()
                await run_func()
            else:
                await ctx.send(f"{cmd.capitalize()} vote added, "
                               f"currently at `{total_votes}/{required_votes}`")

        else:
            await ctx.send(f"You have already voted to {cmd}.")

    @commands.command(hidden=True)
    @commands.is_owner()
    async def _reload_hover_link(self, ctx):
        importlib.reload(utils)
        await ctx.send(f"Reloaded utils")

    @commands.command(name="join", description="Joins a voice channel.",
                      aliases=["connect"], invoke_without_subcommand=True)
    async def _join(self, ctx):
        destination = ctx.author.voice.channel
        ctx.player.text_channel = ctx.channel
        if ctx.player.voice:
            await ctx.player.voice.move_to(destination)
        else:
            ctx.player.voice = await destination.connect()
        v_emote = "<:voice_channel:665577300552843294>"
        t_emote = "<:text_channel:661798072384225307>"
        await ctx.send(f"**Connected to ** {v_emote}`{destination}` and **bound to** {t_emote}`{ctx.channel}`")

    @commands.command(
        name="summon",
        description="Summons the bot to a voice channel. \
            If no channel was specified, it joins your channel."
    )
    @is_dj()
    async def _summon(self, ctx, *, channel: discord.VoiceChannel = None):
        if not channel and not ctx.author.voice:
            raise VoiceError("You are neither connected to a voice channel nor specified a channel to join.")

        destination = channel or ctx.author.voice.channel
        ctx.player.text_channel = ctx.channel
        if ctx.player.voice:
            await ctx.player.voice.move_to(destination)
        else:
            ctx.player.voice = await destination.connect()
        v_emote = "<:voice_channel:665577300552843294>"
        t_emote = "<:text_channel:661798072384225307>"
        await ctx.send(f"**Connected to ** {v_emote}`{destination}` and **bound to** {t_emote}`{ctx.channel}`")

    async def post(self, content, url='https://hastebin.com'):
        async with self.bot.session.post(f'{url}/documents', data=content.encode('utf-8')) as post:
            return url + '/' + (await post.json())['key']

    @commands.command(
        name="leave",
        description="Clears the queue and leaves the voice channel.",
        aliases=["disconnect"]
    )
    @is_dj()
    async def _leave(self, ctx):
        if not ctx.player.voice:
            if ctx.voice_client:
                ctx.player.voice = ctx.voice_client
            else:
                return await ctx.send("Not connected to any voice channel.")
        if len(ctx.player.songs) > 0:
            songs = ctx.player.songs.to_list()
            songs = [s.source.url for s in songs]
            songs.insert(0, ctx.player.current.source.url)
        else:
            songs = None
        await ctx.player.stop()
        del self.players[ctx.guild.id]
        if songs:
            url = await self.post("\n".join(songs))
            if url is None:
                return await ctx.send("Sorry, I couldn't save your queue.")
            await ctx.send("**I saved your queue!**\n"
                           f"To resume where you left off, use this link with the `play` command: **{url}**")

    def get_volume_emoji(self, volume):
        if volume >= 50:
            return ":loud_sound:"
        else:
            return ":sound:"

    @commands.command(name="volume", description="Sets the volume of the player.")
    async def _volume(self, ctx, *, volume: int = None):
        return await ctx.send("To change the volume:\n"
                              "Right click on me in the voice channel, "
                              "and adjust the `User Volume` slider.")

        if not volume:
            volume = ctx.player.volume * 100
            emoji = self.get_volume_emoji(volume)
            return await ctx.send(f"**{emoji} Volume:** `{volume}%`")

        if not ctx.player.is_playing:
            return await ctx.send("Nothing is being played at the moment.")

        if 0 > volume > 100:
            return await ctx.send("Volume must be between 0 and 100")

        ctx.player.volume = volume / 100
        await ctx.send(f"**{self.get_volume_emoji(volume)} Volume:** `{volume}%`")

    @commands.command(
        name="now",
        description="Displays the currently playing song.",
        aliases=["current", "playing", "np"]
    )
    async def _now(self, ctx):
        if not ctx.player.is_playing:
            return await ctx.send("Not currently playing a song.")
        if ctx.player.voice.is_paused():
            em = ctx.player.current.create_embed("Currently Paused", ctx.player.duration.get_time())
        else:
            em = ctx.player.current.create_embed(duration=ctx.player.duration.get_time())

        await ctx.send(embed=em)

    @commands.command(name="pause", description="Pauses the currently playing song.")
    @is_dj()
    async def _pause(self, ctx):

        if ctx.player.is_playing and ctx.player.voice.is_playing():
            ctx.player.pause()
            song = ctx.player.current.source.title
            await ctx.send(f"**:pause_button: Paused** `{song}`")

    @commands.command(name="resume", description="Resumes a currently paused song.", aliases=["unpause"])
    @is_dj()
    async def _resume(self, ctx):

        if ctx.player.is_playing and ctx.player.voice.is_paused():
            ctx.player.resume()
            song = ctx.player.current.source.title
            await ctx.send(f"**:arrow_forward: Resuming** `{song}`")

    @commands.command(name="stop", description="Stops playing song and clears the queue.")
    @is_dj()
    async def _stop(self, ctx):
        if len(ctx.player.songs) > 0:
                songs = ctx.player.songs.to_list()
                songs = [s.source.url for s in songs]
                songs.insert(0, ctx.player.current.source.url)
        else:
            songs = None
        if ctx.player.is_playing:
            ctx.player.voice.stop()
        filenames = [s.source.filename for s in ctx.player.songs._queue]
        filenames.insert(0, ctx.player.current.source.filename)
        ctx.player.songs.clear()
        ctx.player.loop = False
        ctx.player.loop_queue = False
        for filename in filenames:
            if os.path.isfile(filename):
                os.remove(filename)

        await ctx.send("**:stop_button: Song stopped and queue cleared.**")
        if songs:
            url = await self.post("\n".join(songs))
            if url is None:
                return await ctx.send("Sorry, I couldn't save your queue.")
            await ctx.send("**I saved your queue!**\n"
                           f"To resume where you left off, use this link with the `play` command: **{url}**")

    @commands.command(
        name="skip",
        description="Vote to skip a song. The requester can automatically skip.",
        aliases=["next"]
    )
    async def _skip(self, ctx):
        async def skip_song():
            await ctx.message.add_reaction("⏭")
            ctx.player.skip()

        if not ctx.player.is_playing:
            return await ctx.send("Nothing is playing. There is nothing to skip!")

        await self.votes(ctx, "skip", skip_song)

    @commands.group(
        name="queue",
        description="Shows the player's queue. You can optionally select the page.",
        usage="<page #>",
        aliases=["playlist"],
        invoke_without_command=True
    )
    async def _queue(self, ctx, *, page: int = 1):

        if len(ctx.player.songs) == 0:
            return await ctx.send("Queue is empty. Nothing to display!")

        items_per_page = 10
        pages = math.ceil(len(ctx.player.songs) / items_per_page)

        start = (page - 1) * items_per_page
        end = start + items_per_page

        hover = utils.hover_link(ctx, "Song Title", text="Song")
        queue = f"`#` {hover} `Duration` @Requester\n\n"
        for i, song in enumerate(ctx.player.songs[start:end], start=start):
            queue += f"`{i+1}.` [{song.source.title}]({song.source.url}) `{song.source.duration}` {song.source.requester.mention}\n"

        em = discord.Embed(
            title = "**:page_facing_up: Queue**",
            description=f"**{len(ctx.player.songs)} Song(s):**\n{queue}"
        )
        if ctx.player.loop_queue:
            em.title += " (:repeat: looping)"
            em.description = "**:repeat: Loop queue is on**\n" + em.description
        em.set_footer(text=f"Page {page} of {pages}")
        await ctx.send(embed=em)

    @_queue.command(name="save", description="Save the queue to hastebin!",
                    aliases=["upload"])
    @commands.cooldown(1, 10)
    async def _save_queue(self, ctx):
        if len(ctx.player.songs) == 0:
            return await ctx.send("Queue is empty! Nothing to save.")
        songs = ctx.player.songs.to_list()
        songs = [s.source.url for s in songs]
        songs.insert(0, ctx.player.current.source.url)
        url = await self.post("\n".join(songs))
        if url is None:
            return await ctx.send("Sorry, I couldn't save your queue.")
        await ctx.send(f"**Current queue: {url}**")

    @commands.command(name="shuffle", description = "Shuffles the queue.")
    async def _shuffle(self, ctx):

        async def shuffle_queue():
            ctx.player.songs.shuffle()
            await ctx.send("**:twisted_rightwards_arrows: Shuffled songs**")

        if len(ctx.player.songs) == 0:
            return await ctx.send("Queue is empty. Nothing to shuffle!")

        await self.votes(ctx, "shuffle", shuffle_queue)

    @commands.command(
        name="remove",
        description="Removes a song from the queue at a given index.",
        usage="[song #]"
    )
    async def _remove(self, ctx, index: int):
        async def remove_song(index):
            to_be_removed = ctx.player.songs[index - 1].source.title
            ctx.player.songs.remove(index - 1)
            await ctx.send(f"**:wastebasket: Removed** `{to_be_removed}`")

        if len(ctx.player.songs) == 0:
            return await ctx.send("Queue is empty. Nothing to remove!")

        await self.votes(ctx, "remove", remove_song, index)

    @commands.group(
        name="loop",
        description="Loops/unloops the currently playing song.",
        invoke_without_command=True
    )
    async def _loop(self, ctx):

        # return await ctx.send(":warning: :( Sorry, this feature is \
        # currently under maintenance. Check back later.")

        if not ctx.player.is_playing and not ctx.player.loop:
            return await ctx.send("Nothing being played at the moment.")

        # Inverse boolean value to loop and unloop.
        ctx.player.loop = not ctx.player.loop
        ctx.player.loop_queue = False
        if ctx.player.loop:
            await ctx.send("**:repeat_one: Now looping** "
                           f"`{ctx.player.current.source.title}`")
        else:
            await ctx.send("**:repeat_one: :x: No longer looping** "
                           f"`{ctx.player.current.source.title}`")

    @_loop.command(
        name="playlist",
        description="Loop the entire playlist.",
        aliases=["queue"]
    )
    async def _loop_queue(self, ctx):
        if not ctx.player.is_playing and not ctx.player.loop_queue:
            return await ctx.send("Nothing being played at the moment.")
        if len(ctx.player.songs) == 0 and not ctx.player.loop_queue:
            return await ctx.send("The queue is empty. Nothing to loop!")

        ctx.player.loop_queue = not ctx.player.loop_queue
        ctx.player.loop = False

        if ctx.player.loop_queue:
            await ctx.send(f"**:repeat: Now looping queue**")
        else:
            await ctx.send(f"**:repeat: :x: No longer looping queue**")

    async def get_haste(self, url="https://hastebin.com"):
        parsed = urlparse(url)
        newpath = "/raw" + parsed.path
        url = (parsed.scheme +
            "://" +
            parsed.netloc +
            newpath)

        try:
            async with timeout(10):
                async with self.bot.session.get(url) as resp:
                    f = await resp.read()
        except asyncio.TimeoutError:
            raise TimeoutError(":warning: Could not fetch data from hastebin. \
            Is the site down? Try https://www.pastebin.com")
            return None
        async with self.bot.session.get(url) as resp:
            f = await resp.read()
            f = f.decode("utf-8")
            return f

    async def hastebin_playlist(self, ctx, search):
        output = await self.get_haste(search)
        if not output:
            return
        yt_urls = "(?:https?://)?(?:www.)?(?:youtube.com|youtu.be)/(?:watch\?v=)?([^\s]+)"
        if output == "404: Not Found":
            return await ctx.send(":warning: This is not a hastebin or hastebin-like website.")
        if len(re.findall(yt_urls, output)) == 0:
            return await ctx.send(":warning: There are no YouTube URLS in this bin.")
        videos = output.splitlines()
        playlist = []
        failed_songs = 0
        for video in videos:
            try:
                source = await YTDLSource.create_source(ctx, video, loop=self.bot.loop, send_errors=False)
            except YTDLError as e:
                await ctx.send(f"An error occurred while processing this request: ```py {str(e)}```")
            else:
                if source:
                    playlist.append(source)
                else:
                    failed_songs += 1

        em = discord.Embed(
            title="**:page_facing_up: Enqueued:**",
            timestamp=d.utcnow(),
            color=discord.Color.blurple()
        )
        description = ""
        total_duration = 0

        for i, src in enumerate(playlist):
            song = Song(src)
            total_duration += int(source.data.get("duration"))
            await ctx.player.songs.put(song)

            if i < 9:
                description += f"\n• [{src.title}]({src.url}) `{src.duration}`"
            elif i == 9 and len(playlist) > 10:
                songs_left = len(playlist) - (i + 1)
                description += f"\n• [{src.title}]({src.url}) `{src.duration}`\n...and {songs_left} more song(s)"

        total_duration = YTDLSource.parse_duration(total_duration)
        description += f"\nTotal duration: {total_duration}"
        if failed_songs > 0:
            description += f"\n:warning: Sorry, {failed_songs} song(s) failed to download."

        em.description = description
        em.set_footer(text=f"Requested by {ctx.message.author.name}#{ctx.message.author.discriminator}",
                      icon_url=self.bot.user.avatar_url)
        await ctx.send(embed=em)

    async def fetch_yt_playlist(self, ctx, url):
        try:
            playlist, failed_songs = await YTDLSource.get_playlist(ctx, url, loop=self.bot.loop)
        except YTDLError as e:
            print(e)
            await ctx.send(f"An error occurred while processing this request: ```py {str(e)}```")
        else:
            em = discord.Embed(title="**:page_facing_up: Enqueued:**",
                               timestamp=d.utcnow(), color=0xFF0000)
            description = ""
            total_duration = 0
            for i, source in enumerate(playlist):
                if not source:
                    failed_songs += 1
                    continue
                song = Song(source)

                await ctx.player.songs.put(song)
                total_duration += int(source.data.get("duration"))
                if i < 9:
                    description += f"\n• [{source.title}]({source.url}) `{source.duration}`"
                elif i == 9 and len(playlist) > 10:
                    songs_left = len(playlist) - (i + 1)
                    description += f"\n• [{source.title}]({source.url}) \
                    `{source.duration}`\n...and {songs_left} more song(s)"

            total_duration = YTDLSource.parse_duration(total_duration)
            description += f"\nTotal duration: {total_duration}"
            if failed_songs > 0:
                description += f"\n:warning: Sorry, {failed_songs} song(s) failed to download."

            em.description = description
            em.set_footer(text=f"Requested by {ctx.message.author.name}#{ctx.message.author.discriminator}",
                          icon_url=self.bot.user.avatar_url)
            await ctx.send(embed=em)

    @commands.command(
        name="play",
        description="Search for a song and play it.",
        aliases=["p", "yt"],
        usage="[song]"
    )
    async def _play(self, ctx, *, search: str = None):

        if not search and ctx.player.is_playing and ctx.player.voice.is_paused()\
             and ctx.author.guild_permissions.manage_guild:
            ctx.player.resume()
            return await ctx.send(f"**:arrow_forward: Resuming** `{ctx.player.current.source.title}`")
        if not search:
            return await ctx.send("Please specify a song to play/search for.")

        if not ctx.player.voice:
            await ctx.invoke(self._join)

        urls = "http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"
        if len(re.findall(urls, search)) > 0:
            youtube_urls = "(?:https?://)?(?:www.)?(?:youtube.com|youtu.be)/(?:watch\?v=)?([^\s]+)"
            if len(re.findall(youtube_urls, search)) > 0:
                if "list=" in search:
                    await ctx.send("**<:youtube:667536366447493120> Fetching YouTube playlist** "
                                   f"`{search}`\nThis make take awhile depending on playlist size.")

                    await self.fetch_yt_playlist(ctx, search)
                    return
            elif "soundcloud" in search:
                pass
            else:
                await ctx.send("**:globe_with_meridians: Fetching from bin** "
                               f"`{search}`\nThis make take awhile depending on amount of videos.")
                await self.hastebin_playlist(ctx, search)
                return

        await ctx.send(f"**:mag: Searching** `{search}`")

        async with ctx.typing():
            try:
                source = await YTDLSource.create_source(ctx, search, loop=self.bot.loop)
            except YTDLError as e:
                print(e)
                await ctx.send(f"An error occurred while processing this request: ```py {str(e)}```")
            else:
                if not source:
                     return await ctx.send("Sorry. I couldn't fetch that song. Possibly being ratelimited.")
                song = Song(source)

                if not song:
                    return await ctx.send("Sorry. I couldn't fetch that song. Possibly being ratelimited.")

                await ctx.player.songs.put(song)
                if ctx.player.is_playing:
                    await ctx.send(f"**:page_facing_up: Enqueued** {str(source)}")

    @commands.command(name="ytdl", description="Test YTDL to see if it works", hidden=True)
    @commands.is_owner()
    async def _ytdl_test(self, ctx):
        partial = functools.partial(YTDLSource.ytdl.extract_info, "hat kid electro", download=False, process=False)
        try:
            data = await self.bot.loop.run_in_executor(None, partial)
        except youtube_dl.DownloadError as e:
            print("Could not connect to YouTube")
            traceback.print_exception(type(e), e, e.__traceback__, file=sys.stderr)
            error = ''.join(traceback.format_exception(type(e), e, e.__traceback__, 1))
            return await ctx.send(f"Could not connect to YouTube!```py\n{error}```")
        if not data:
            return await ctx.send("YouTube did not return any data.")
        await ctx.send("Successfully connected to YouTube with youtube_dl")

    @_join.before_invoke
    @_play.before_invoke
    async def ensure_player(self, ctx):
        if not ctx.author.voice or not ctx.author.voice.channel:
            raise commands.CommandError("You are not connected to any voice channel.")

        if ctx.voice_client:
            if ctx.voice_client.channel != ctx.author.voice.channel:
                raise commands.CommandError("Bot is already in a voice channel.")


def setup(bot):
    bot.add_cog(Music(bot))
