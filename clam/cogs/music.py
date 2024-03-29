import asyncio
import datetime
import enum
import functools
import importlib
import itertools
import logging
import os.path
import random
import re
import sys
import traceback
import typing
from urllib.parse import urlparse

import asyncpg
import discord
import humanize
import youtube_dl
from async_timeout import timeout
from discord import app_commands
from discord.ext import commands, flags, menus

from clam.utils import colors, db, humantime, stopwatch
from clam.utils.context import Context
from clam.utils.emojis import GREEN_TICK, LOADING, RED_TICK
from clam.utils.flags import NoUsageFlagCommand
from clam.utils.formats import plural
from clam.utils.menus import MenuPages, UpdatingMessage

log = logging.getLogger("clam.music")
bin_log = logging.getLogger("clam.music.bin")
ytdl_log = logging.getLogger("clam.music.ytdl")
player_log = logging.getLogger("clam.music.player")


class SongsTable(db.Table, table_name="songs"):
    id = db.PrimaryKeyColumn()

    filename = db.Column(db.String())
    title = db.Column(db.String())
    song_id = db.Column(db.String())  # id that youtube gives the song
    extractor = db.Column(
        db.String()
    )  # the extractor that was used (platform like youtube, soundcloud)
    info = db.Column(db.JSON, default="'{}'::jsonb")  # info dict that youtube_dl gives
    plays = db.Column(db.Integer, default=0)

    registered_at = db.Column(db.Datetime(), default="now() at time zone 'utc'")
    last_updated = db.Column(db.Datetime(), default="now() at time zone 'utc'")

    @classmethod
    def create_table(cls, *, exists_ok=True):
        statement = super().create_table(exists_ok=exists_ok)
        sql = "CREATE INDEX IF NOT EXISTS songs_title_trgm_idx ON songs USING GIN (title gin_trgm_ops);"
        return statement + "\n" + sql


class SongAliases(db.Table, table_name="song_aliases"):
    id = db.PrimaryKeyColumn()

    alias = db.Column(db.String)
    song_id = db.Column(db.ForeignKey("songs", "id"))
    expires_at = db.Column(db.Datetime)

    user_id = db.Column(db.Integer(big=True))

    @classmethod
    def create_table(cls, *, exists_ok=True):
        statement = super().create_table(exists_ok=exists_ok)
        sql = "CREATE UNIQUE INDEX IF NOT EXISTS song_aliases_uniq_idx ON song_aliases (alias, user_id, song_id);"
        return statement + "\n" + sql


# Silence useless bug reports messages
youtube_dl.utils.bug_reports_message = lambda: ""


class YTDLError(commands.CommandError):
    pass


class Aborted(RuntimeError):
    pass


class SongSelector(discord.ui.View):
    class _Source(menus.ListPageSource):
        def __init__(self, songs):
            super().__init__(songs, per_page=1)

        def format_page(self, menu, song):
            em = discord.Embed(
                title="Select a song to continue",
                description=f"```yml\n{song.get('title')}\n```",
                color=discord.Color.green(),
            )

            em.add_field(
                name="Duration",
                value=Song.timestamp_duration(int(song.get("duration"))),
            )
            em.add_field(
                name="Uploader",
                value=f"[{song.get('uploader')}]({song.get('uploader_url')})",
            )
            em.add_field(name="URL", value=f"[Click]({song.get('webpage_url')})")
            em.set_thumbnail(url=song.get("thumbnail"))

            em.set_footer(text=f"Song {menu.current_page+1}/{self.get_max_pages()}")

            return em

    def __init__(self, ctx, songs, **kwargs):
        self.ctx = ctx
        self.songs = songs
        kwargs.setdefault("timeout", 180)  # 3m
        self._source = self._Source(songs)
        self.current_page = 0
        self.selected_page = None
        super().__init__(**kwargs)

    @property
    def source(self):
        """:class:`PageSource`: The source where the data comes from."""
        return self._source

    async def on_timeout(self) -> None:
        if self.message:
            await self.message.delete()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user == self.ctx.author:
            return True
        else:
            await interaction.response.send_message("This select dialog is not for you.", ephemeral=True)
            return False

    async def _get_kwargs_from_page(self, page):
        value = await discord.utils.maybe_coroutine(
            self._source.format_page, self, page
        )
        if isinstance(value, dict):
            return value
        elif isinstance(value, str):
            return {"content": value, "embed": None}
        elif isinstance(value, discord.Embed):
            return {"embed": value, "content": None}

    async def show_page(self, interaction: discord.Interaction, page_number: int) -> None:
        page = await self.source.get_page(page_number)
        self.current_page = page_number
        kwargs = await self._get_kwargs_from_page(page)
        if kwargs:
            if interaction.response.is_done():
                if self.message:
                    await self.message.edit(**kwargs, view=self)
            else:
                await interaction.response.edit_message(**kwargs, view=self)

    async def start(self) -> None:
        if not self.ctx.channel.permissions_for(self.ctx.me).embed_links:
            await self.ctx.send('Bot does not have embed links permission in this channel.')
            return

        await self.source._prepare_once()
        page = await self.source.get_page(0)
        kwargs = await self._get_kwargs_from_page(page)
        self.message = await self.ctx.send(**kwargs, view=self)  # type: ignore

        await self.wait()

        return self.songs[self.selected_page] if self.selected_page is not None else None

    async def show_checked_page(self, interaction: discord.Interaction, page_number: int) -> None:
        max_pages = self._source.get_max_pages()
        try:
            if max_pages is None:
                # If it doesn't give maximum pages, it cannot be checked
                await self.show_page(interaction, page_number)
            elif max_pages > page_number >= 0:
                await self.show_page(interaction, page_number)
        except IndexError:
            # An error happened that can be handled, so ignore it.
            pass

    @discord.ui.button(label="Select", style=discord.ButtonStyle.green)
    async def select_pages(self, interaction: discord.Interaction, button: discord.ui.Button):
        """select the current page"""
        self.selected_page = self.current_page
        await interaction.response.defer()
        await self.message.delete()
        self.stop()

    @discord.ui.button(label="Previous")
    async def go_to_previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        """go to the previous page"""
        await self.show_checked_page(interaction, self.current_page - 1)

    @discord.ui.button(label="Next")
    async def go_to_next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        """go to the next page"""
        await self.show_checked_page(interaction, self.current_page + 1)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def stop_pages(self, interaction: discord.Interaction, button: discord.ui.Button):
        """stops the pagination session."""
        await interaction.response.defer()
        await self.message.delete()
        self.stop()


class Song:
    YTDL_OPTIONS = {
        "format": "bestaudio/best",
        "extractaudio": True,
        "audioformat": "mp3",
        "outtmpl": "cache/%(extractor)s-%(id)s.%(ext)s",
        "restrictfilenames": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "ignoreerrors": False,
        "logtostderr": False,
        "quiet": True,
        "no_warnings": True,
        "default_search": "auto",
        "source_address": "0.0.0.0",
    }

    YTDL_PLAYLIST_OPTIONS = {
        "format": "bestaudio/best",
        "extractaudio": True,
        "audioformat": "mp3",
        "outtmpl": "cache/%(extractor)s-%(id)s.%(ext)s",
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
    playlist_ytdl = youtube_dl.YoutubeDL(YTDL_PLAYLIST_OPTIONS)

    def __init__(
        self,
        ctx: commands.Context,
        *,
        data: dict,
        source: discord.FFmpegPCMAudio = None,
        volume: float = 0.5,
        filename=None,
    ):
        self.ffmpeg_options = self.FFMPEG_OPTIONS.copy()

        self.source = source

        self.ctx = ctx
        self.requester = ctx.author
        self.channel = ctx.channel
        self.data = data
        self.filename = filename
        self._volume = volume

        self.id = data.get("id")
        self.extractor = data.get("extractor")
        self.uploader = data.get("uploader")
        self.uploader_url = data.get("uploader_url")
        date = data.get("upload_date")
        self.date = data.get("upload_date")
        try:
            self.total_seconds = int(data.get("duration"))
        except (TypeError, ValueError):
            self.total_seconds = 0
        if self.date:
            self.upload_date = date[6:8] + "." + date[4:6] + "." + date[0:4]
        else:
            self.upload_date = "???"
        self.title = data.get("title")
        self.thumbnail = data.get("thumbnail")
        self.description = data.get("description")
        self.human_duration = self.parse_duration(self.total_seconds)
        self.duration = self.timestamp_duration(self.total_seconds)
        self.tags = data.get("tags")
        self.url = data.get("webpage_url")
        self.views = data.get("view_count")
        self.likes = data.get("like_count")
        self.dislikes = data.get("dislike_count")
        self.stream_url = data.get("url")

        # database stuff
        self.database = False
        self.registered_at = None
        self.last_updated = None

    def __str__(self):
        return f"`{self.title}`"

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, volume: float):
        self._volume = volume
        if self.source:
            self.source.volume = volume

    def make_source(self):
        if self.source:
            self.source.cleanup()
            self.source = None

        source = discord.FFmpegPCMAudio(self.filename, **self.ffmpeg_options)
        self.source = discord.PCMVolumeTransformer(source, self.volume)

    def discard_source(self):
        self.source.cleanup()
        self.source = None

    @classmethod
    def from_record(cls, record, ctx):
        filename = record["filename"]
        info = record["info"]
        registered_at = record["registered_at"]
        last_updated = record["last_updated"]

        self = cls(ctx, data=info, filename=filename)

        self.database = True
        self.registered_at = registered_at
        self.last_updated = last_updated
        self.db_id = record["id"]
        self.plays = record["plays"]

        return self

    @classmethod
    async def get_song_from_db(cls, ctx, search, *, loop):
        loop = loop or asyncio.get_event_loop()

        ytdl_log.info(f"Searching database for '{search}'")

        song_id = cls.parse_youtube_id(search)

        ytdl_log.info(f"Searching database for id: {song_id or search}")
        song = await cls.fetch_from_database(ctx, song_id or search)

        if song:
            ytdl_log.info(f"Found song in database: {song.id}")
            return song

        query = """SELECT *
                   FROM songs
                   ORDER BY similarity(title, $1) DESC
                """

        record = await ctx.db.fetchrow(query, search)

        if not record:
            return await ctx.send(f":x: Could not find a match for `{search}`")

        return cls.from_record(record, ctx)

    @classmethod
    async def search_song_aliases(cls, ctx, search):
        query = """SELECT songs.*, song_aliases.expires_at
                   FROM song_aliases
                   INNER JOIN songs ON songs.id = song_aliases.song_id
                   WHERE (song_aliases.user_id=$1 OR song_aliases.user_id IS NULL) AND song_aliases.alias=$2;
                """

        record = await ctx.db.fetchrow(query, ctx.author.id, search.lower())

        if not record:
            return None

        expires_at = record.get("expires_at")

        if expires_at and expires_at < datetime.datetime.utcnow():
            query = """DELETE FROM song_aliases
                       WHERE (song_aliases.user_id=$1 OR song_aliases.user_id IS NULL) AND song_aliases.alias=$2;
                    """
            await ctx.db.execute(query, ctx.author.id, search.lower())
            return None

        return cls.from_record(record, ctx)

    @classmethod
    async def fetch_from_database(cls, ctx, song_id, extractor="youtube"):
        query = """SELECT * FROM songs
                   WHERE song_id=$1 AND extractor=$2;
                """

        record = await ctx.db.fetchrow(query, song_id, extractor)

        if not record:
            return None

        return cls.from_record(record, ctx)

    @staticmethod
    def parse_youtube_id(search):
        yt_urls = re.compile(
            r"(?:https?://)?(?:www.)?(?:youtube.com|youtu.be)/(?:watch\?v=)?([^\s]+)"
        )
        match = yt_urls.match(search)

        if match:
            return match.groups()[0]

        return None

    @classmethod
    async def resolve_webpage_url(cls, ctx, search, *, send_errors=True):
        loop = ctx.bot.loop

        partial = functools.partial(
            cls.ytdl.extract_info, search, download=False, process=False
        )
        try:
            data = await loop.run_in_executor(None, partial)

        except youtube_dl.DownloadError as e:
            ytdl_log.warning(f"Error while searching for '{search}': {e}")
            if send_errors:
                await ctx.send(
                    f"**:x: Error while searching for** `{search}`\n```\n{e}\n```"
                )
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
                raise YTDLError(
                    "Couldn't find anything that matches `{}`".format(search)
                )

        webpage_url = process_info.get("webpage_url")

        if not webpage_url:
            return search, True, process_info

        ytdl_log.info(f"Found URL for {search}: '{webpage_url}'")

        song_id = process_info.get("id")
        extractor = process_info.get("extractor")

        song = await cls.fetch_from_database(ctx, song_id, extractor)

        if song:
            ytdl_log.info(
                f"Song '{extractor}-{song_id}' in database, skipping further extraction"
            )
            return song

        # YTDL is weird about file extensions
        # Since the file extension is always .NA, I'll have to
        # take off the file extension and the cache/.
        # Then I have to loop through the files in the cache,
        # and take off their file extensions.
        # I can then compare the filename to each file in
        # the cache to see if the song has been downloaded.
        # There are probably a thousand better ways to do this...
        # ¯\_(ツ)_/¯

        def is_in_cache(filename):
            for f in os.listdir("cache"):
                name = os.path.splitext(f)[0]

                if filename == name:
                    return True

            return False

        filename = cls.ytdl.prepare_filename(process_info)[6:-3]

        if is_in_cache(filename):
            ytdl_log.info(f"Song {song_id} is already downloaded. Skipping download.")
            download = False
        else:
            ytdl_log.info(f"Downloading song {song_id}...")
            download = True

        return webpage_url, download, process_info

    @classmethod
    async def get_song(
        cls,
        ctx: commands.Context,
        search: str,
        *,
        loop: asyncio.BaseEventLoop = None,
        send_errors=True,
        skip_resolve=False,
    ):
        loop = loop or asyncio.get_event_loop()

        ytdl_log.info(f"Searching for '{search}'")

        song_id = cls.parse_youtube_id(search)

        song_id = song_id or search

        ytdl_log.info(f"Searching database for id: {song_id}")
        song = await cls.fetch_from_database(ctx, song_id)

        if song:
            ytdl_log.info(f"Found song in database: {song.id}")
            return song

        ytdl_log.info("Searching song aliases")
        song = await cls.search_song_aliases(ctx, search)

        if song:
            ytdl_log.info(f"Found song alias in database: {song.id}")
            return song

        ytdl_log.info("Song not in database, searching youtube")

        if not skip_resolve:
            result = await cls.resolve_webpage_url(
                ctx, search, send_errors=send_errors
            )
            if not result:
                return

            if isinstance(result, Song):
                return result

            webpage_url, download, resolved_info = result

            duration = resolved_info.get("duration")
            title = resolved_info.get("title", search)
            if duration:
                try:
                    duration = int(duration)
                except ValueError:
                    pass
                else:
                    if duration >= 60 * 60 * 3:  # 3 hours
                        confirm = await ctx.confirm(
                            f"Song `{title}` is over 3 hours long.\n"
                            "If you didn't intend this, please cancel this request. "
                            f"Consider using `{ctx.prefix}search <song>` to select from a list of songs.\n"
                            "Confirm selected song?"
                        )
                        if not confirm:
                            raise Aborted

        else:
            webpage_url = search
            download = True

        partial = functools.partial(
            cls.ytdl.extract_info, webpage_url, download=download
        )
        try:
            processed_info = await loop.run_in_executor(None, partial)
        except youtube_dl.DownloadError as e:
            ytdl_log.warning(f"Error while downloading '{webpage_url}': {e}")
            if send_errors:
                await ctx.send(
                    f"**:x: Error while downloading** `{webpage_url}`\n```\n{e}\n```"
                )
                return
        else:
            if processed_info is None:
                raise YTDLError("Couldn't fetch `{}`".format(webpage_url))

            ytdl_log.info("Fetched song info")

            if "entries" not in processed_info:
                info = processed_info
            else:
                info = None
                while info is None:
                    try:
                        info = processed_info["entries"].pop(0)
                    except IndexError as e:
                        print(e)
                        raise YTDLError(
                            "Couldn't retrieve any matches for `{}`".format(webpage_url)
                        )

            filename = cls.ytdl.prepare_filename(info)

            song_id = info.get("id")
            extractor = info.get("extractor")

            song = await cls.fetch_from_database(ctx, song_id, extractor)

            if not song:
                ytdl_log.info(f"Song '{extractor}-{song_id}' not in database, inserting")
                query = """INSERT INTO songs (filename, title, song_id, extractor, info)
                           VALUES ($1, $2, $3, $4, $5::jsonb)
                           RETURNING songs.id;
                        """

                song_id = await ctx.db.fetchval(
                    query, filename, info.get("title"), song_id, extractor, info
                )

            else:
                ytdl_log.info(
                    f"Song '{extractor}-{song_id}' is already in database, skipping insertion"
                )
                song_id = song.db_id

            query = """INSERT INTO song_aliases (alias, expires_at, song_id)
                       VALUES ($1, $2, $3);
                    """

            expires = datetime.datetime.utcnow() + datetime.timedelta(days=30)

            ytdl_log.info(f"Inserting song alias '{search.lower()}' into database...")
            async with ctx.db.acquire() as conn:
                async with conn.transaction():
                    try:
                        await ctx.db.execute(query, search.lower(), expires, song_id)

                    except asyncpg.UniqueViolationError:
                        ytdl_log.info(
                            "Could not insert song alias, there is already an identical one."
                        )

            song = cls(
                ctx,
                data=info,
                filename=filename,
            )

            music = ctx.bot.get_cog("Music")
            if music:
                ctx.bot.loop.create_task(music.check_song_duration(ctx, song))

            return song

    @classmethod
    async def get_playlist(
        cls,
        ctx: commands.Context,
        search: str,
        progress_message,
        *,
        loop: asyncio.BaseEventLoop = None,
    ):
        loop = loop or asyncio.get_event_loop()

        ytdl_log.info("Searching for playlist")

        partial = functools.partial(
            cls.playlist_ytdl.extract_info, search, download=False, process=False
        )
        unproccessed = await loop.run_in_executor(None, partial)

        if unproccessed is None:
            raise YTDLError("Couldn't find anything that matches `{}`".format(search))

        if "entries" not in unproccessed:
            data_list = [unproccessed]
        else:
            data_list = []
            for entry in unproccessed["entries"]:
                if entry:
                    data_list.append(entry)

            if len(data_list) == 0:
                raise YTDLError("Playlist is empty")

        length = len(data_list)
        progress_message.change_label(0, emoji=ctx.tick(True))
        progress_message.change_label(1, text=f"Getting songs (0/{length})")

        ytdl_log.info("Fetching songs in playlist")

        playlist = []
        counter = 0
        for i, video in enumerate(data_list):
            webpage_url = video["url"]
            ytdl_log.info(f"Song: '{webpage_url}'")

            song_id = video.get("id")
            extractor = video.get("extractor")

            # yes I know this is prone to failure, but I don't care
            extractor = extractor or "youtube"

            song = await cls.fetch_from_database(ctx, song_id, extractor)

            if song:
                ytdl_log.info(
                    f"Song '{extractor}-{song_id}' in database, skipping further extraction"
                )
                playlist.append(song)
                progress_message.change_label(1, text=f"Getting songs ({i+1}/{length})")
                progress_message.change_label(1, emoji=ctx.tick(True))
                continue

            filename = cls.playlist_ytdl.prepare_filename(video)[:-3] + ".webm"
            if os.path.isfile(filename):
                ytdl_log.info("Song is already downloaded. Skipping download.")
                download = False
            else:
                ytdl_log.info("Downloading song...")
                download = True

            full = functools.partial(
                cls.playlist_ytdl.extract_info, webpage_url, download=download
            )
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
                            await ctx.send(
                                f"Couldn't retrieve any matches for `{webpage_url}`"
                            )

                song_id = info.get("id")
                extractor = info.get("extractor")
                filename = cls.playlist_ytdl.prepare_filename(info)

                song = await cls.fetch_from_database(ctx, song_id, extractor)

                if not song:
                    ytdl_log.info(f"Song '{extractor}-{song_id}' not in database, inserting")
                    query = """INSERT INTO songs (filename, title, song_id, extractor, info)
                               VALUES ($1, $2, $3, $4, $5::jsonb)
                            """

                    await ctx.db.execute(
                        query, filename, info.get("title"), song_id, extractor, info
                    )

                else:
                    ytdl_log.info(
                        f"Song '{extractor}-{song_id}' is already in database, skipping insertion"
                    )

                source = cls(
                    ctx,
                    data=info,
                    filename=filename,
                )

                music = ctx.bot.get_cog("Music")
                if music:
                    ctx.bot.loop.create_task(music.check_song_duration(ctx, source))

                playlist.append(source)

            progress_message.change_label(1, text=f"Getting songs ({i+1}/{length})")
            progress_message.change_label(1, emoji=ctx.tick(True))

        return playlist, counter

    @classmethod
    async def search_ytdl(cls, ctx, search):
        loop = ctx.bot.loop

        async with ctx.typing():
            partial = functools.partial(cls.ytdl.extract_info, search, download=False)
            info = await loop.run_in_executor(None, partial)

        if not info or not info["entries"]:
            await ctx.send("No results found.")
            return

        pages = SongSelector(ctx=ctx, songs=info["entries"])
        entry = await pages.start()

        if not entry:
            await ctx.send("Aborted.")
            return

        async with ctx.typing():
            song = await cls.get_song(ctx, entry["webpage_url"])

        return song

    @staticmethod
    def parse_duration(duration: int):
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)

        duration_str = []
        if days > 0:
            duration_str.append(f"{plural(days):day}")
        if hours > 0:
            duration_str.append(f"{plural(hours):hour}")
        if minutes > 0:
            duration_str.append(f"{plural(minutes):minute}")
        if seconds > 0:
            duration_str.append(f"{plural(seconds):second}")

        if len(duration_str) == 0:
            return Song.timestamp_duration(duration)

        if len(duration_str) == 1:
            return duration_str[0]

        elif len(duration_str) == 2:
            return " and ".join(duration_str)

        else:
            return ", ".join(duration_str[:-1]) + f", and {duration_str[-1]}"

    @staticmethod
    def timestamp_duration(duration: int):
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)

        duration = ""

        if days > 0:
            duration += f"{plural(days):day}, "

        if hours > 0:
            duration += f"{hours}:"
            minutes = f"{minutes:02d}"
        duration += f"{minutes}:{seconds:02d}"
        return duration





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
        player_log.info(f"{ctx.guild}: Creating player...")

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

        player_log.info(f"{ctx.guild}: Starting player loop...")
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
        if total == 0:
            return "`Unknown Duration`"

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
    def now_playing_embed(song, title="Now playing", *, duration=None, db_info=False, filesize=None, show_context=False):
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

        if song.uploader:
            em.add_field(name="Uploader", value=f"[{song.uploader}]({song.uploader_url})")
        em.add_field(name="Source", value=song.extractor.capitalize())
        em.add_field(name="URL", value=f"[Click]({song.url})")
        if song.thumbnail:
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
                            player_log.info(f"{ctx.guild}: Getting a song from the queue...")
                            self.current = await self.songs.get()
                    except asyncio.TimeoutError:
                        player_log.info(
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

                player_log.info(f"{ctx.guild}: Playing song '{self.current.title}'")
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

        except Exception:
            player_log.exception(f"{ctx.guild}: Exception in player_loop")

            player_log.info(f"{ctx.guild}: Restarting task...")
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
            player_log.info(f"{self.ctx.guild}: Stopping and disconnecting from voice")
            self.voice.stop()
            await self.voice.disconnect()
            self.voice = None

        self.songs.clear()

    def pause(self):
        if self.is_playing and self.voice.is_playing():
            player_log.info(f"{self.ctx.guild}: Pausing...")
            self.voice.pause()
            self.duration.pause()
            self.status = PlayerStatus.PAUSED

    def resume(self):
        if self.is_playing and self.voice.is_paused():
            player_log.info(f"{self.ctx.guild}: Resuming...")
            self.voice.resume()
            self.duration.unpause()
            self.status = PlayerStatus.PLAYING


def hover_link(ctx, msg, text="`?`"):
    return (
        f"[{text}](https://www.discordapp.com/"
        f"channels/{ctx.guild.id}/{ctx.channel.id} "
        f""""{msg}")"""
    )


class QueuePages(menus.ListPageSource):
    def __init__(self, player):
        super().__init__(player.songs, per_page=10)
        self.player = player

        queue = player.songs._queue
        total_duration = sum(int(s.data.get("duration")) for s in queue)
        self.total_duration = Song.parse_duration(total_duration)

    def format_song(self, song):
        return f"[{song.title}]({song.url}) `{song.duration}` {song.requester.mention}"

    async def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page
        ctx = menu.ctx
        player = self.player
        max_pages = self.get_max_pages()

        hover = hover_link(ctx, "Song Title", text="Song")
        queue = []

        queue.append("Key:")
        queue.append(f"`#` {hover} `Duration` @Requester\n")

        if menu.current_page == 0 and player.current and player.status != PlayerStatus.WAITING:
            queue.append(f"**Now Playing:**\n{self.format_song(player.current)}\n")

        if ctx.player.songs:
            queue.append(f"**{plural(len(ctx.player.songs)):Song} Up Next:**")

            for i, song in enumerate(entries, start=offset):
                queue.append(f"`{i+1}.` {self.format_song(song)}")

            if max_pages > 1 and menu.current_page + 1 != max_pages:
                queue.append("\n*More songs on the next page -->*")

        else:
            queue.append("**No Songs Up Next**")

        em = discord.Embed(
            title="**:page_facing_up: Queue**",
            description="\n".join(queue),
            color=discord.Color.green(),
        )
        if ctx.player.loop_queue:
            em.title += " (:repeat: looping)"
            em.description = "**:repeat: Loop queue is on**\n\n" + em.description
        if ctx.player.loop:
            em.title += " (:repeat_one: looping)"
            em.description = "**:repeat_one: Loop single is on**\n\n" + em.description

        duration = (
            f"\n\nTotal queue duration: {self.total_duration}\n"
            if player.songs
            else "\n\n"
        )
        em.description += f"{duration}To see more about what's currently playing, use `{ctx.prefix}now`"
        songs = f"{plural(len(ctx.player.songs)):Song} | " if ctx.player.songs else ""
        em.set_footer(text=f"{songs}Page {menu.current_page+1} of {max_pages or 1}")
        return em


class LocationType(enum.Enum):
    youtube = 0
    soundcloud = 1
    db = 2
    bin = 3


class BinFetchingError(Exception):
    pass


class CannotJoinVoice(commands.CommandError):
    def __init__(self, message):
        super().__init__(f"{RED_TICK} {message}")
        self.message = message


class AlreadyActivePlayer(commands.CommandError):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


class NoPlayerError(commands.CommandError):
    def __init__(self):
        super().__init__(
            f"{RED_TICK} This server doesn't have a player. Play a song to create one!"
        )


class NotListeningError(commands.CommandError):
    def __init__(self, message):
        super().__init__(f"{RED_TICK} {message}")
        self.message = message


class BadSongPosition(commands.BadArgument):
    def __init__(self):
        super().__init__('Invalid time provided, try e.g. "90", "1:05", or "3m".')


class NotDJ(commands.CommandError):
    def __init__(self, *, only_member=False):
        message = f"{RED_TICK} You must have a role named 'DJ' to use this command. "
        addition = "having the manage server permission also works."
        message += f"Being in the channel alone or {addition}" if only_member else addition.capitalize()
        super().__init__(message)


class SongPosition(commands.Converter):
    async def convert(self, ctx, arg):
        # we want to be able to convert 3:00 into 180, but also 3m2s into 182
        # supported formats:
        # xx:xx:xx  | 3:02 --> 182
        # xx        | 90   --> 90
        # ShortTime | 2m   --> 120

        # try this as a blanket
        try:
            st = await humantime.ShortTime.convert(ctx, arg)
        except commands.BadArgument:
            pass
        else:
            delta = st.dt - ctx.message.created_at
            return int(delta.total_seconds())

        # this must just be in seconds
        if ":" not in arg:
            try:
                position = int(arg)
            except ValueError:
                raise BadSongPosition()
            else:
                return position

        # if we're here that means the user must've used the x:xx format
        args = arg.split(":")

        if len(args) > 3:
            raise BadSongPosition()

        position = 0
        time_map = [1, 60, 24 * 60]

        for i, arg in enumerate(reversed(args)):
            try:
                casted = int(arg)
            except ValueError:
                raise BadSongPosition()
            else:
                position += casted * time_map[i]

        return position


def is_dj(*, only_member_check=False):
    def predicate(ctx):
        if not ctx.guild:
            raise commands.NoPrivateMessage()

        author = ctx.author
        upper = discord.utils.get(ctx.guild.roles, name="DJ")
        lower = discord.utils.get(ctx.guild.roles, name="dj")

        player = ctx.cog.get_player(ctx)

        if player and player.voice and player.voice.channel:
            members = [m for m in player.voice.channel.members if not m.bot]

        else:
            members = []

        is_only_member = len(members) == 1 and ctx.author in members

        only_member_condition = only_member_check and is_only_member

        if (
            author.guild_permissions.manage_guild
            or upper in author.roles
            or lower in author.roles
            or author.id in [ctx.bot.owner_id, 612816777994305566, 251018556664184832]
            or only_member_condition
        ):
            return True

        raise NotDJ(only_member=only_member_check)

    return commands.check(predicate)


def is_listening():
    async def predicate(ctx):
        if not ctx.guild:
            return False

        player = ctx.cog.get_player(ctx)

        if not player:
            raise NoPlayerError()

        author = ctx.author

        if (
            not author.voice
            or not author.voice.channel
            or author.voice.channel != player.voice.channel
        ):
            raise NotListeningError(
                "You must be connected to voice to use this command."
            )

        try:
            return await is_dj().predicate(ctx)
        except NotDJ:
            pass

        if author.voice.self_deaf or author.voice.deaf:
            raise NotListeningError("You must be undeafened to use this command.")

        return True

    return commands.check(predicate)


class Music(commands.Cog):
    """Play music in a voice channel through the bot."""

    def __init__(self, bot):
        self.bot = bot
        self.emoji = "\N{MULTIPLE MUSICAL NOTES}"

        # Check if the cache folder is created
        if not os.path.exists("cache"):
            log.info("Cache folder not found; setting up cache...")
            os.mkdir("cache")

        if not hasattr(bot, "players"):
            self.bot.players = {}

        self.players = self.bot.players

    def get_player(self, ctx: commands.Context):
        return self.players.get(ctx.guild.id)

    def create_player(self, ctx):
        old_player = self.get_player(ctx)
        if old_player is not None and not old_player.closed:
            raise AlreadyActivePlayer("There is already an active player.")

        player = Player(self.bot, ctx)
        self.players[ctx.guild.id] = player
        ctx.player = player
        return player

    async def cog_before_invoke(self, ctx):
        if ctx.guild:
            ctx.player = self.get_player(ctx)

    async def cog_command_error(self, ctx, error: commands.CommandError):
        overridden_errors = (
            VoiceError,
            YTDLError,
            NoPlayerError,
            NotListeningError,
            CannotJoinVoice,
            AlreadyActivePlayer,
            NotDJ,
        )

        if isinstance(error, overridden_errors):
            await ctx.send(str(error), ephemeral=True)
            ctx.handled = True

    async def stop_all_players(self, *, save_queues=True):
        for player in self.players.values():
            # Get all songs in the queue
            if len(player.songs) > 0:
                songs = player.songs.to_list()
                songs = [s.url for s in songs]
                songs.insert(0, player.current.url)
            elif player.current:
                songs = [player.current.url]
            else:
                songs = None

            await player.stop()

            # Save the queue to clambin
            if songs:
                url = await self.post("\n".join(songs))
                if url is None:
                    return await player.text_channel.send(
                        "**Sorry! All music players have been stopped due to bot maintenance.**"
                        "\nUnfortunately, there was an error while automatically your saving queue. "
                        "Sorry about that :("
                    )

                prefix = self.bot.guild_prefix(player.ctx.guild)
                await player.text_channel.send(
                    "**Sorry! All music players have been stopped due to bot maintenance.**\n"
                    f"Good news, **I saved your queue!**\nTo resume where you left off, use: `{prefix}playbin {url}`"
                )

        self.bot.players.clear()
        self.players = self.bot.players

        for voice in self.bot.voice_clients:
            await voice.disconnect()

    def delete_all_songs(self):
        for file in os.listdir("cache"):
            if file.endswith(".webm"):
                os.remove(file)

    @commands.command()
    @commands.is_owner()
    async def stopall(self, ctx):
        """Stops all players."""

        confirm = await ctx.confirm(
            f"Are you sure you want to stop all {plural(len(self.bot.players)):player}?"
        )
        if confirm:
            await self.stop_all_players()
            await ctx.send("Stopped all players.")

        else:
            await ctx.send("Aborted.")

    @commands.command(aliases=["deleteall"])
    @commands.is_owner()
    async def deletesongs(self, ctx):
        """Deletes all songs in the bot's cache."""

        if self.players:
            return await ctx.send(
                "There are active players. Please use `stopall` first."
            )

        confirm = await ctx.confirm(
            "Are you sure you want to delete all songs in cache?"
        )
        if confirm:
            self.delete_all_songs()

            await ctx.send("Deleted all songs.")

    @commands.command()
    @commands.is_owner()
    async def allplayers(self, ctx):
        """View all players"""
        status_mapping = {
            PlayerStatus.PLAYING: "🎶",
            PlayerStatus.PAUSED: "⏸️",
            PlayerStatus.WAITING: "🕐",
            PlayerStatus.CLOSED: "💤",
        }

        v_emote = "<:voice_channel:665577300552843294>"
        t_emote = "<:text_channel:661798072384225307>"

        players = []

        for player in self.players.values():
            guild_name = discord.utils.escape_mentions(player.ctx.guild.name)
            channel = f"{v_emote}`{player.voice.channel}`" if player.voice else ""
            channel += f"{t_emote}`{player.text_channel}`"

            if player.voice and player.voice.channel:
                connected = sum(1 for m in player.voice.channel.members if not m.bot)
                deaf = sum(
                    1
                    for m in player.voice.channel.members
                    if not m.bot and (m.voice.deaf or m.voice.self_deaf)
                )
                connected = f" {connected} connected"
                connected += f" ({deaf} deafened)" if deaf else ""

            else:
                connected = ""

            if player.voice:
                num = player.voice.average_latency * 1000
                latency = f" `{num:.2f} ms`"

            else:
                latency = ""

            status = status_mapping.get(player.status, "❔")
            players.append(f"{status} **{guild_name}** - {channel}{connected}{latency}")

        if not players:
            return await ctx.send("No players")

        await ctx.send("\n".join(players))

    @commands.command(aliases=["fdisconnect", "fdc"])
    @commands.is_owner()
    @commands.guild_only()
    async def forcedisconnect(self, ctx):
        """Force disconnects the voice client in this server."""
        if not ctx.guild.voice_client:
            return await ctx.send("Not connected to a voice channel in this server.")

        await ctx.guild.voice_client.disconnect()

        await ctx.send("Disconnected bot from voice.")

    @commands.Cog.listener("on_voice_state_update")
    async def auto_self_deafen(self, member, before, after):
        """Automatically self-deafen when connecting to a voice channel"""
        if member != self.bot.user:
            return

        player = self.players.get(member.guild.id)

        if not player or not player.voice or not player.voice.channel:
            return

        # if not before.channel and after.channel:
        #     await member.guild.change_voice_state(
        #         channel=player.voice.channel, self_deaf=True
        #     )

    @commands.Cog.listener("on_voice_state_update")
    async def delete_player_on_kick(self, member, before, after):
        """Delete the player when kicked from a voice channel

        I don't know a good way to know if the disconnect was
        a leave command, a dc/reconnect, or an actual kick, so
        this is my solution.
        """
        if member != self.bot.user:
            return

        player = self.players.get(member.guild.id)

        if not player:
            return

        def check(m, b, a):
            return m == self.bot.user and a.channel

        if before.channel and not after.channel:
            log.info(f"{member.guild}: Disconnected from voice, waiting to rejoin...")

            # Attempt to wait for the player to reconnect
            connected = False
            for i in range(5):
                await asyncio.sleep(1)
                if player.voice and player.voice.is_connected():
                    connected = True
                    break

            if connected:
                log.info(f"{member.guild}: Looks like I rejoined")

            else:
                if player.voice and player.voice.is_playing():
                    return

                if not player.closed:
                    log.info(
                        f"{member.guild}: Bot left voice for 5 seconds, killing player..."
                    )
                    await player.stop()
                    del self.players[member.guild.id]

    @commands.Cog.listener("on_voice_state_update")
    async def on_voice_leave(self, member, before, after):
        if member.bot:
            return

        player = self.players.get(member.guild.id)

        if not player:
            return

        if not player.voice:
            return

        members = [m for m in player.voice.channel.members if not m.bot]

        def check(mem, bf, af):
            if not mem.bot and af and af.channel and af.channel == player.voice.channel:
                return True
            return False

        if len(members) > 0:
            return

        player.pause()

        try:
            await self.bot.wait_for("voice_state_update", timeout=180, check=check)
        except asyncio.TimeoutError:
            if len(player.songs) > 0:
                songs = player.songs.to_list()
                songs = [s.url for s in songs]
                songs.insert(0, player.current.url)
            else:
                songs = None
            await player.stop()
            del self.players[member.guild.id]
            if songs:
                url = await self.post("\n".join(songs))
                if url is None:
                    return await player.text_channel.send(
                        "**The bot automatically left the channel due to inactivity.**"
                        "\nUnfortunately, there was an error while automatically your saving queue. "
                        "Sorry about that :("
                    )

                prefix = self.bot.guild_prefix(member.guild)
                await player.text_channel.send(
                    "**I automatically left the channel due to inactivity.**\n"
                    f"Good news, **I saved your queue!**\nTo resume where you left off, use: `{prefix}playbin {url}`"
                )

        player.resume()

    async def votes(self, ctx, cmd: str, func):
        voter = ctx.author

        is_requester = voter == ctx.player.current.requester
        has_perms = voter.guild_permissions.manage_guild
        is_owner = voter.id == ctx.bot.owner_id

        upper = discord.utils.get(ctx.guild.roles, name="DJ")
        lower = discord.utils.get(ctx.guild.roles, name="dj")
        is_dj = upper in voter.roles or lower in voter.roles

        members = [
            m
            for m in ctx.player.voice.channel.members
            if not m.bot and not m.voice.deaf and not m.voice.self_deaf
        ]
        length = len(members)

        if length == 1:
            is_only_user = True

        else:
            is_only_user = False
            required_votes = round(length * 0.75)  # 75% of members must vote

        if is_requester or has_perms or is_only_user or is_dj or is_owner:
            await func(1, 1)
            return

        if cmd not in ctx.player._votes.keys():
            ctx.player._votes[cmd] = set()

        if voter.id not in ctx.player._votes[cmd]:
            ctx.player._votes[cmd].add(voter.id)
            total_votes = len(ctx.player._votes[cmd])

            if total_votes >= required_votes:
                ctx.player._votes[cmd].clear()
                await func(total_votes, required_votes)
            else:
                await ctx.send(
                    f"{cmd.capitalize()} vote added, "
                    f"currently at `{total_votes}/{required_votes}`"
                )

        else:
            await ctx.send(f"You have already voted to {cmd}.")

    def is_bot_borked(self, guild):
        if not guild.voice_client:
            return False

        # loop through channels to see if bot is in any.
        # if it is, we aren't borked. if it isn't we are borked
        # and I think we need to tamper with internals

        for channel in guild.voice_channels:
            if guild.me in channel.members:
                return False

        # if we're here that means that dpy has a VoiceClient
        # registered, but the bot isn't actually connected anywhere.
        # YIKES
        return True

    async def connect(self, ctx, destination):
        log.info(f"{ctx.guild}: Connecting to {destination}...")

        if self.is_bot_borked(ctx.guild):
            log.info(
                f"{ctx.guild}: Bot is borked! Trying to reset internal voice client dict...."
            )
            # scary!
            self.bot._connection._voice_clients.pop(ctx.guild.id)

        try:
            if ctx.player.voice:
                log.info(
                    f"{ctx.guild}: Player found and is already in a voice channel, moving to {destination}..."
                )
                await ctx.player.voice.move_to(destination)
                # await ctx.guild.change_voice_state(channel=destination, self_deaf=True)

            elif ctx.guild.voice_client:
                log.info(
                    f"{ctx.guild}: Player not found but bot is already in a voice channel, moving to {destination}..."
                )
                await ctx.guild.voice_client.move_to(destination)
                ctx.player.voice = ctx.guild.voice_client
                # await ctx.guild.change_voice_state(channel=destination, self_deaf=True)

            else:
                log.info(
                    f"{ctx.guild}: Bot not in voice channel, attempting to connect to {destination}..."
                )
                ctx.player.voice = await destination.connect()
                # await ctx.guild.change_voice_state(channel=destination, self_deaf=True)

        except discord.ClientException:
            log.info(f"{ctx.guild}: Connection attempt to {destination} failed")
            if ctx.guild.me.guild_permissions.move_members:
                log.info(
                    f"{ctx.guild}: I have permissions to move myself, attemping to do move to {destination}"
                )
                await ctx.guild.me.move_to(destination)
                # await ctx.guild.change_voice_state(channel=destination, self_deaf=True)
                log.info(
                    f"{ctx.guild}: Looks like I moved to {destination} successfully"
                )
                return

            log.info(
                f"{ctx.guild}: I don't have permissions to move myself, sending fail message..."
            )
            await ctx.send(
                "Failed to connect to voice. Try re-running the command. If that fails, contact Fyssion."
            )
            return False

        log.info(f"{ctx.guild}: Looks like I connected to {destination} successfully")

    @commands.command(aliases=["connect"], invoke_without_subcommand=True)
    @commands.guild_only()
    # @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def join(self, ctx):
        """Joins your voice channel."""

        if not ctx.player:
            player = self.create_player(ctx)

        destination = ctx.author.voice.channel
        ctx.player.text_channel = ctx.channel

        v_emote = "<:voice_channel:665577300552843294>"
        t_emote = "<:text_channel:661798072384225307>"

        if (
            destination.user_limit
            and (ctx.player.voice and ctx.player.voice.channel != destination)
            and not ctx.guild.me.guild_permissions.administrator
            and len(destination.members) >= destination.user_limit
        ):
            raise CannotJoinVoice(
                f"**I can't join** {v_emote}`{destination}` because **it is full!** "
                f"({len(destination.members)}/{destination.user_limit} members)"
            )

        result = await self.connect(ctx, destination)
        if result is False:
            return

        if ctx.interaction is None or ctx.interaction.is_expired():
            await ctx.send(
                ctx.tick(
                    True,
                    f"**Connected to ** {v_emote}`{destination}` and **bound to** {t_emote}`{ctx.channel}`",
                )
            )

    @commands.command()
    @commands.guild_only()
    @is_dj()
    # @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def summon(self, ctx, *, channel: discord.VoiceChannel = None):
        """Summons the bot to a voice channel.

        If not channel was specified, it joins your channel.
        """

        if not ctx.player:
            player = self.create_player(ctx)

        if not channel and not ctx.author.voice:
            raise VoiceError(
                "You are neither connected to a voice channel nor specified a channel to join."
            )

        destination = channel or ctx.author.voice.channel
        ctx.player.text_channel = ctx.channel

        v_emote = "<:voice_channel:665577300552843294>"
        t_emote = "<:text_channel:661798072384225307>"

        if (
            destination.user_limit
            and (ctx.player.voice and ctx.player.voice.channel != destination)
            and not ctx.guild.me.guild_permissions.administrator
            and len(destination.members) >= destination.user_limit
        ):
            raise CannotJoinVoice(
                f"**I can't join** {v_emote}`{destination}` because **it is full!** "
                f"({len(destination.members)}/{destination.user_limit} members)"
            )

        result = await self.connect(ctx, destination)
        if result is False:
            return

        await ctx.send(
            ctx.tick(
                True,
                f"**Connected to ** {v_emote}`{destination}` and **bound to** {t_emote}`{ctx.channel}`",
            )
        )

    async def post(self, content, url="https://paste.clambot.xyz"):
        async with self.bot.session.post(
            f"{url}/documents",
            data=content.encode("utf-8"),
            headers={"User-Agent": "Clam Music Cog"},
        ) as post:
            return url + "/" + (await post.json())["key"]

    @commands.hybrid_command(aliases=["disconnect", "dc"])
    @commands.guild_only()
    @is_dj(only_member_check=True)
    async def leave(self, ctx):
        """Clears the queue and leaves the voice channel."""

        if not ctx.player and ctx.voice_client:
            await ctx.voice_client.disconnect()
            return

        if not ctx.player:
            raise NoPlayerError()

        if not ctx.player.voice:
            if ctx.voice_client:
                ctx.player.voice = ctx.voice_client

            else:
                return await ctx.send("Not connected to any voice channel.")

        await ctx.player.stop()
        del self.players[ctx.guild.id]

        await ctx.send(ctx.tick(True, "Disconnected and cleared queue."))

    def get_volume_emoji(self, volume):
        if volume >= 50:
            return ":loud_sound:"
        else:
            return ":sound:"

    @commands.hybrid_command()
    @app_commands.describe(volume="The volume to change to")
    @commands.guild_only()
    @is_dj(only_member_check=True)
    async def volume(self, ctx, *, volume: int = None):
        """Sets the volume of the player.

        The volume must be between 1 and 100.
        """

        if not volume:
            volume = ctx.player.volume * 100
            emoji = self.get_volume_emoji(volume)
            return await ctx.send(f"**{emoji} Volume:** `{volume}%`")

        if not ctx.player.is_playing:
            return await ctx.send("Nothing is being played at the moment.")

        if 0 > volume > 100:
            return await ctx.send("Volume must be between 0 and 100")

        ctx.player.volume = volume / 100
        ctx.player.voice.volume = volume / 100
        ctx.player.current.source.volume = volume / 100

        await ctx.send(
            f"**{self.get_volume_emoji(volume)} Volume set to:** `{volume}%`"
        )

    @commands.hybrid_command()
    @app_commands.describe(position="The position in the song to jump to")
    @commands.guild_only()
    @is_dj(only_member_check=True)
    async def seek(self, ctx, position: SongPosition):
        """Seeks to a position in the current song.

        The position can either be a number in seconds
        or a timestamp, e.g. `1:25`.
        """

        if position == 0:
            return await ctx.invoke(self.startover)

        total_seconds = ctx.player.current.total_seconds
        if position >= total_seconds:
            raise commands.BadArgument(
                f"Position is greater than song length ({position}/{total_seconds})."
            )

        if position < 0:
            raise BadSongPosition()

        timestamp = Song.timestamp_duration(position)

        current = ctx.player.current

        song = Song(
            ctx,
            data=current.data,
            filename=current.filename,
        )

        song.ffmpeg_options["options"] = f"-ss {timestamp}"

        ctx.player.startover = True

        if not ctx.player.loop and not (
            ctx.player.loop_queue and len(ctx.player.songs) == 1
        ):
            ctx.player.songs._queue.appendleft(song)

        ctx.player.skip()

        async def set_duration():
            await asyncio.sleep(0.5)
            ctx.player.duration.start_time = (
                datetime.datetime.now() - datetime.timedelta(seconds=position)
            )

        self.bot.loop.create_task(set_duration())

        await ctx.send(f"**:fast_forward: Seeking to** `{timestamp}`")

    @commands.hybrid_command(aliases=["current", "playing", "np"])
    @commands.guild_only()
    async def now(self, ctx):
        """Shows the currently playing song."""

        if not ctx.player.is_playing:
            return await ctx.send("Not currently playing a song.")

        if ctx.player.voice.is_paused():
            em = ctx.player.now_playing_embed(
                ctx.player.current, "Currently Paused", duration=ctx.player.duration.get_time()
            )

        else:
            em = ctx.player.now_playing_embed(
                ctx.player.current, duration=ctx.player.duration.get_time()
            )

        await ctx.send(embed=em)

    @commands.hybrid_command()
    @commands.guild_only()
    @is_dj()
    async def pause(self, ctx):
        """Pauses the currently playing song."""

        if ctx.player.is_playing and ctx.player.voice.is_playing():
            ctx.player.pause()
            song = ctx.player.current.title
            await ctx.send(f"**:pause_button: Paused** `{song}`")

        else:
            await ctx.send("Not currently playing.")

    @commands.hybrid_command(aliases=["unpause"])
    @commands.guild_only()
    @is_dj()
    async def resume(self, ctx):
        """Resumes the paused song."""

        if ctx.player.is_playing and ctx.player.voice.is_paused():
            ctx.player.resume()
            song = ctx.player.current.title
            await ctx.send(f"**:arrow_forward: Resuming** `{song}`")

        else:
            await ctx.send("Not currently paused.")

    @commands.hybrid_command()
    @commands.guild_only()
    @is_dj()
    async def stop(self, ctx):
        """Stops playing and clears the queue."""

        if ctx.player.is_playing:
            ctx.player.voice.stop()

        ctx.player.songs.clear()
        ctx.player.loop = False
        ctx.player.loop_queue = False

        await ctx.send("**\N{BLACK SQUARE FOR STOP} Song stopped and queue cleared.**")

    @commands.hybrid_command(aliases=["next", "s"])
    @commands.guild_only()
    @is_listening()
    async def skip(self, ctx):
        """Votes to skip a song. The song's requester can automatically skip."""

        async def skip_song(total, required):
            if ctx.interaction is None or ctx.interaction.is_expired():
                await ctx.message.add_reaction("⏭")
            else:
                await ctx.send("**⏭ Skipped**")

            if required != 1:
                await ctx.send(
                    f"Required votes met `({total}/{required})`. **⏭ Skipping.**"
                )

            if not ctx.player.songs:
                ctx.player.loop = False
                ctx.player.loop_queue = False

            ctx.player.skip()

        if not ctx.player.is_playing:
            return await ctx.send("Nothing is playing.")

        await self.votes(ctx, "skip", skip_song)

    @commands.hybrid_command()
    @app_commands.describe(position="The position of the song in the queue")
    @app_commands.rename(position="song")
    @commands.guild_only()
    async def skipto(self, ctx, *, position: int):
        """Skips to a song in the queue."""

        if len(ctx.player.songs) < position:
            raise commands.BadArgument(f"The queue has less than {position} song(s).")

        async def skipto_song(total, required):
            song = ctx.player.songs[position - 1]

            if ctx.player.loop_queue:
                await ctx.player.songs.put(ctx.player.current)

            for i in range(position - 1):
                skipped_song = await ctx.player.songs.get()

                if ctx.player.loop_queue:
                    await ctx.player.songs.put(skipped_song)

            ctx.player.startover = True
            ctx.player.skip()

            votes = (
                f"Required votes met `({total}/{required})`.\n" if required != 1 else ""
            )
            await ctx.send(f"{votes}**⏩ Skipped to** `{song}`")

        await self.votes(ctx, "skipto", skipto_song)

    @commands.hybrid_group(aliases=["q", "playlist"], fallback="show", invoke_without_command=True)
    @commands.guild_only()
    async def queue(self, ctx):
        """Shows the song queue."""

        pages = MenuPages(QueuePages(ctx.player), ctx=ctx)
        return await pages.start()

    @queue.command(name="save", aliases=["upload"])
    @commands.cooldown(1, 10)
    @commands.guild_only()
    async def queue_save(self, ctx):
        """Saves the queue to <https://paste.clambot.xyz>."""

        if len(ctx.player.songs) > 0:
            songs = ctx.player.songs.to_list()
            songs = [s.url for s in songs]
            songs.insert(0, ctx.player.current.url)

        elif ctx.player.current:
            songs = [ctx.player.current.url]

        else:
            songs = None

        if not songs:
            return await ctx.send("There are no songs to save.")

        url = await self.post("\n".join(songs))

        if url is None:
            return await ctx.send("Sorry, I couldn't save your queue.")

        await ctx.send(
            f"**Saved queue:** {url}\n"
            "Hint: you can use this link with the `playbin` command, like so:\n"
            f"`{ctx.prefix}playbin {url}`"
        )

    @queue.command(name="clear")
    @commands.guild_only()
    async def queue_clear(self, ctx):
        """Clears the queue."""

        ctx.player.songs.clear()

        await ctx.send("**\N{WASTEBASKET} Cleared queue**")

    @commands.command()
    @commands.guild_only()
    @is_listening()
    async def shuffle(self, ctx):
        """Shuffles the queue."""

        async def shuffle_queue(total, required):
            if required != 1:
                votes_msg = f"Required votes met `({total}/{required})`. "

            else:
                votes_msg = ""

            ctx.player.songs.shuffle()
            await ctx.send(
                f"{votes_msg}**\N{TWISTED RIGHTWARDS ARROWS} Shuffled songs**"
            )

        if len(ctx.player.songs) == 0:
            return await ctx.send("Queue is empty. Nothing to shuffle!")

        await self.votes(ctx, "shuffle", shuffle_queue)

    @queue.command(name="remove", aliases=["delete"])
    @app_commands.describe(song="The index of the song in the queue")
    @commands.guild_only()
    @is_listening()
    async def queue_remove(self, ctx, song: int):
        """Removes a song from the queue."""

        async def remove_song(total, required):
            if required != 1:
                votes_msg = f"Required votes met `({total}/{required})`. "

            else:
                votes_msg = ""

            to_be_removed = ctx.player.songs[song - 1].title
            ctx.player.songs.remove(song - 1)
            await ctx.send(f"{votes_msg}**\N{WASTEBASKET} Removed** `{to_be_removed}`")

        if len(ctx.player.songs) == 0:
            return await ctx.send("Queue is empty.")

        if song > len(ctx.player.songs):
            length = len(ctx.player.songs)
            raise commands.BadArgument(
                f"There is no song at position {song}. Queue length is only {length}."
            )

        await self.votes(ctx, "remove", remove_song)

    @commands.command()
    @commands.guild_only()
    async def notify(self, ctx):
        """Enables or disables now playing notifications."""

        ctx.player.notify = not ctx.player.notify

        if ctx.player.notify:
            await ctx.send("**:bell: Now playing notifications enabled**")

        else:
            await ctx.send("**:no_bell: Now playing notifications disabled**")

    @commands.hybrid_group(name="loop", fallback="single", invoke_without_command=True)
    @commands.guild_only()
    async def loop(self, ctx):
        """Toggles repeat for the current song."""

        if not ctx.player.is_playing and not ctx.player.loop:
            return await ctx.send("Nothing is being played at the moment.")

        async def loop_song(total, required):
            # Inverse boolean value to loop and unloop.
            ctx.player.loop = not ctx.player.loop
            ctx.player.loop_queue = False

            votes = (
                f"Required votes met `({total}/{required})`.\n" if required != 1 else ""
            )

            if ctx.player.loop:
                await ctx.send(
                    f"{votes}**:repeat_one: Now looping** "
                    f"`{ctx.player.current.title}`"
                )
            else:
                await ctx.send(
                    f"{votes}**:repeat_one: :x: No longer looping** "
                    f"`{ctx.player.current.title}`"
                )

        await self.votes(ctx, "loop", loop_song)

    @loop.command(name="queue", aliases=["playlist"])
    @commands.guild_only()
    async def loop_queue(self, ctx):
        """Toogles repeat for the queue."""

        if not ctx.player.is_playing and not ctx.player.loop_queue:
            return await ctx.send("Nothing being played at the moment.")

        async def do_loop_queue(total, required):
            ctx.player.loop_queue = not ctx.player.loop_queue
            ctx.player.loop = False

            votes = (
                f"Required votes met `({total}/{required})`.\n" if required != 1 else ""
            )

            if ctx.player.loop_queue:
                await ctx.send(f"{votes}**:repeat: Now looping queue**")
            else:
                await ctx.send(f"{votes}**:repeat: :x: No longer looping queue**")

        await self.votes(ctx, "loop queue", do_loop_queue)

    @commands.hybrid_command()
    @commands.guild_only()
    @is_dj(only_member_check=True)
    async def startover(self, ctx):
        """Starts the current song over."""

        if not ctx.player.is_playing:
            return await ctx.send("Nothing is being played at the moment.")

        async def startover_song(total, required):
            current = ctx.player.current

            song = Song(
                ctx,
                data=current.data,
                filename=current.filename,
            )

            ctx.player.startover = True

            if not ctx.player.loop and not (
                ctx.player.loop_queue and len(ctx.player.songs) == 1
            ):
                ctx.player.songs._queue.appendleft(song)

            ctx.player.skip()

            votes = (
                f"Required votes met `({total}/{required})`.\n" if required != 1 else ""
            )
            await ctx.send(f"{votes}**⏪ Starting song over**")

        await self.votes(ctx, "startover", startover_song)

    async def check_song_duration(self, ctx, song):
        if song.total_seconds < 10800:  # 3 hours
            return

        try:
            record = await ctx.db.fetchrow("SELECT * FROM songs WHERE title=$1 AND song_id=$2;", song.title, song.id)
            if record:
                song = Song.from_record(record, ctx)

            partial = functools.partial(self.get_file_size, song.filename)
            size = await self.bot.loop.run_in_executor(None, partial)
            filesize = humanize.naturalsize(size, binary=True)

            em = Player.now_playing_embed(song, "3+ Hour Song Downloaded", db_info=True, filesize=filesize)
            em.add_field(name="Context", value=f"[Jump to message]({ctx.message.jump_url})")
            em.add_field(name="Guild", value=f"{song.requester.guild} ({song.requester.guild.id})")
            em.add_field(name="User", value=f"{song.requester} ({song.requester.id})")
            await ctx.console.send(embed=em)

        except Exception:
            pass

    async def fetch_yt_playlist(self, ctx, url):
        yt_emoji = "<:youtube:781633321255567361>"

        em = discord.Embed(
            title=f"{yt_emoji} Fetching YouTube playlist",
            color=0xFF0000,
        )
        em.set_footer(text="This may take awhile.")

        progress_message = UpdatingMessage(embed=em)
        progress_message.add_label(LOADING, "Fetching playlist")
        progress_message.add_label(LOADING, "Getting songs")
        progress_message.add_label(LOADING, "Enqueuing songs")

        await progress_message.start(ctx)

        try:
            playlist, failed_songs = await Song.get_playlist(
                ctx, url, progress_message, loop=self.bot.loop
            )

        except YTDLError as e:
            print(e)
            await ctx.send(
                f"An error occurred while processing this request:\n{str(e)}"
            )
            progress_message.change_label(0, emoji=ctx.tick(False))
            progress_message.change_label(1, emoji=ctx.tick(False))
            progress_message.change_label(2, emoji=ctx.tick(False))
            await progress_message.stop()

        else:
            em = discord.Embed(
                title="**\N{PAGE FACING UP} Enqueued:**",
                color=0xFF0000,
            )
            description = ""
            total_duration = 0
            for i, song in enumerate(playlist):
                if not song:
                    failed_songs += 1
                    continue

                await ctx.player.songs.put(song)
                total_duration += int(song.data.get("duration"))
                if i < 9:
                    description += f"\n• [{song.title}]({song.url}) `{song.duration}`"
                elif i == 9 and len(playlist) > 10:
                    songs_left = len(playlist) - (i + 1)
                    description += f"\n• [{song.title}]({song.url}) \
                    `{song.duration}`\n...and {songs_left} more song(s)"

            total_duration = Song.parse_duration(total_duration)
            description += f"\nTotal duration: {total_duration}"
            if failed_songs > 0:
                description += (
                    f"\n:warning: Sorry, {failed_songs} song(s) failed to download."
                )

            progress_message.change_label(2, emoji=GREEN_TICK)
            await progress_message.stop()

            em.description = description
            await ctx.send(
                f"{yt_emoji} **Finished loading Youtube playlist**", embed=em
            )

    URLS = re.compile(
        r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"
    )
    YT_URLS = re.compile(
        r"(?:https?://)?(?:www.)?(?:youtube.com|youtu.be)/(?:watch\?v=)?([^\s]+)"
    )

    async def play_song(self, ctx, location_type, query):
        if not ctx.player.voice:
            await ctx.invoke(self.join)

        if location_type is LocationType.bin:
            return await ctx.invoke(self.playbin, url=query)

        if query.startswith("<") and query.endswith(">"):
            query = query.strip("<>")

        if self.URLS.match(query):
            if self.YT_URLS.match(query):
                if "list=" in query:
                    return await self.fetch_yt_playlist(ctx, query)

        skip_resolve = False

        original = query

        if location_type is LocationType.soundcloud:
            query = f"scsearch:{query}"
            skip_resolve = True

        elif location_type is LocationType.youtube:
            query = f"ytsearch:{query}"
            skip_resolve = True

        await ctx.send(f"**:mag: Searching** `{original}`")

        async with ctx.typing():
            try:
                async with timeout(180):  # 3m
                    if location_type is LocationType.db:
                        song = await Song.get_song_from_db(
                            ctx, query, loop=self.bot.loop
                        )
                    else:
                        song = await Song.get_song(
                            ctx, query, loop=self.bot.loop, skip_resolve=skip_resolve
                        )

            except YTDLError as e:
                print(e)
                await ctx.send(
                    f"An error occurred while processing this request:\n{str(e)}"
                )

            except asyncio.TimeoutError:
                await ctx.send("Timed out while fetching song. Sorry.")

            except Aborted:
                await ctx.send("Aborted.")

            else:
                if not song:
                    return await ctx.send("I couldn't fetch that song. Sorry.")

                await ctx.player.songs.put(song)

                if ctx.player.is_playing:
                    await ctx.send(f"**\N{PAGE FACING UP} Enqueued** {str(song)}")

                elif not ctx.player._notify:
                    await ctx.send(
                        f"**\N{MULTIPLE MUSICAL NOTES} Now playing** `{song.title}`"
                    )

    async def get_paste(self, url="https://paste.clambot.xyz"):
        parsed = urlparse(url)
        newpath = "/raw" + parsed.path
        url = parsed.scheme + "://" + parsed.netloc + newpath

        try:
            async with timeout(10):
                async with self.bot.session.get(
                    url, headers={"User-Agent": "Clam Music Cog"}
                ) as resp:
                    if resp.status != 200:
                        raise BinFetchingError(
                            f"Could not fetch site: Error {resp.status}"
                        )

                    f = await resp.read()
                    f = f.decode("utf-8")
                    return f

        except asyncio.TimeoutError:
            raise TimeoutError("Timed out while fetching from site.")

    async def pastebin_playlist(self, ctx, search):
        bin_log.info(f"Fetching from bin: '{search}'")

        em = discord.Embed(
            title="\N{GLOBE WITH MERIDIANS} Fetching from pastebin",
            color=discord.Color.blue(),
        )
        em.set_footer(text="This may take some time.")
        progress_message = UpdatingMessage(embed=em)
        progress_message.add_label(LOADING, "Fetch from pastebin")
        progress_message.add_label(LOADING, "Find and enqueue songs")

        await progress_message.start(ctx)

        try:
            output = await self.get_paste(search)

        except BinFetchingError as e:
            progress_message.change_label(0, emoji=ctx.tick(False))
            progress_message.change_label(1, emoji=ctx.tick(False))
            await progress_message.stop()
            return await ctx.send(e)

        if not output or output == """{"message":"Document not found."}""":
            progress_message.change_label(0, emoji=ctx.tick(False))
            progress_message.change_label(1, emoji=ctx.tick(False))
            await progress_message.stop()
            return await ctx.send("Site returned an error: `Document not found.`")

        if output == "404: Not Found":
            progress_message.change_label(0, emoji=ctx.tick(False))
            progress_message.change_label(1, emoji=ctx.tick(False))
            await progress_message.stop()
            return await ctx.send("Site returned an error: `404: Not Found`")

        if len(self.YT_URLS.findall(output)) == 0:
            await ctx.send(
                ":warning: There are no YouTube URLs in this pastebin. "
                "Are you sure this is the correct site?\n**Continuing download...**"
            )

        videos = output.splitlines()
        if len(videos) > 50:
            confirm = await ctx.confirm(
                "I found more than 50 lines in this pastebin. Continue?"
            )
            if not confirm:
                bin_log.info("User denied bin. Cancelling...")
                progress_message.change_label(0, emoji=ctx.tick(False))
                progress_message.change_label(1, emoji=ctx.tick(False))
                await progress_message.stop()
                return await ctx.send("Cancelled.")

        length = len(videos)

        progress_message.change_label(0, emoji=GREEN_TICK)
        progress_message.change_label(1, text=f"Find and enqueue songs (0/{length})")

        bin_log.info(f"Fetching {len(videos)} songs...")
        playlist = []
        failed_songs = 0
        for i, video in enumerate(videos):
            try:
                song = await Song.get_song(
                    ctx, video, loop=self.bot.loop, send_errors=False
                )
            except YTDLError as e:
                await ctx.send(
                    f"An error occurred while processing this request:\n{str(e)}"
                )
            else:
                if song:

                    bin_log.info(f"Adding '{song.title}' to queue...")
                    await ctx.player.songs.put(song)
                    playlist.append(song)
                else:
                    failed_songs += 1

            progress_message.change_label(
                1, text=f"Find and enqueue songs ({i+1}/{length})"
            )

        progress_message.change_label(1, emoji=GREEN_TICK)
        await progress_message.stop()

        em = discord.Embed(
            title="**\N{PAGE FACING UP} Enqueued:**",
            color=discord.Color.green(),
        )
        description = ""
        total_duration = 0

        for i, song in enumerate(playlist):
            total_duration += int(song.data.get("duration"))

            if i < 9:
                description += f"\n• [{song.title}]({song.url}) `{song.duration}`"
            elif i == 9 and len(playlist) > 10:
                songs_left = len(playlist) - (i + 1)
                description += f"\n• [{song.title}]({song.url}) `{song.duration}`\n...and {songs_left} more song(s)"

        total_duration = Song.parse_duration(total_duration)
        description += f"\nTotal duration: {total_duration}"
        if failed_songs > 0:
            description += (
                f"\n:warning: Sorry, {failed_songs} song(s) failed to download."
            )

        em.description = description
        await ctx.send(
            ctx.tick(True, "**Finished loading songs from pastebin**"), embed=em
        )

    @commands.command(aliases=["pb"])
    @commands.guild_only()
    # @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def playbin(self, ctx, *, url):
        """Plays songs from a pastebin."""

        if not ctx.player:
            player = self.create_player(ctx)
            ctx.player = player

        if not ctx.player.voice:
            await ctx.invoke(self.join)

        if not self.URLS.match(url):
            raise commands.BadArgument("You must provide a valid URL.")

        await self.pastebin_playlist(ctx, url)

    def parse_search(self, search):
        type_regex = re.compile(r"(\w+):\s?(.+)")

        location_types = {
            LocationType.youtube: ["youtube", "yt"],
            LocationType.db: ["database", "db"],
            LocationType.soundcloud: ["soundcloud", "sc"],
            LocationType.bin: ["pastebin", "paste", "bin"],
        }

        valid_types = []
        for types in location_types.values():
            valid_types.extend(types)

        location_type = None

        match = type_regex.match(search)

        if not match:
            query = search

        else:
            their_type, query = match.groups()
            their_type = their_type.lower()
            if match and their_type in valid_types:
                for loctype, types in location_types.items():
                    if their_type in types:
                        location_type = loctype
                        break

            if not location_type:
                query = search

        return query, location_type

    @commands.hybrid_command(aliases=["p", "yt"])
    @app_commands.describe(song="What song to play")
    # @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def play(self, ctx, *, song: str):
        """Plays a song in a voice channel.

        You can specify where to search for the song with `source: search`.
        Defaults to Youtube.

        Sources:
          - `youtube` `yt` - Search Youtube
          - `soundcloud` `sc` - Search Soundcloud
          - `database` `db` - Search the bot's database
          - `pastebin` `paste` `bin` - Give a pastebin URL (shortcut to `playbin` command)

        Examples:
         - `soundcloud: a song here` - Searches Soundcloud
          - `search here` - Searches Youtube
          - `db: a song` - Searches the database
        """
        if not ctx.guild:
            raise commands.NoPrivateMessage()

        if not ctx.player:
            player = self.create_player(ctx)
            ctx.player = player

        query, location_type = self.parse_search(song)

        await self.play_song(ctx, location_type, query)

    @commands.command()
    @commands.guild_only()
    async def search(self, ctx, limit: typing.Optional[int], *, search):
        """Searches for songs and lets you select one to play.

        You can specify where to search for the song with `source: search`
        Defaults to Youtube.

        Sources:
          - `youtube` `yt` - Search Youtube
          - `soundcloud` `sc` - Search Soundcloud

        Examples:
         - `soundcloud: a song here` - Searches Soundcloud
          - `search here` - Searches Youtube
        """

        if not ctx.player:
            player = self.create_player(ctx)
            ctx.player = player

        if not ctx.player.voice:
            await ctx.invoke(self.join)

        limit = limit or 3

        if limit < 2:
            raise commands.BadArgument("You must search for at least 2 songs.")

        if limit > 6:
            raise commands.BadArgument("You cannot search for more than 6 songs.")

        query, location_type = self.parse_search(search)

        if location_type is LocationType.bin or location_type is LocationType.db:
            query = search  # don't want these to be available

        original = query

        if location_type is LocationType.soundcloud:
            query = f"scsearch{limit}:{query}"

        else:
            query = f"ytsearch{limit}:{query}"

        await ctx.send(f"**:mag: Searching** `{original}`")

        try:
            song = await Song.search_ytdl(ctx, query)

        except YTDLError as e:
            print(e)
            await ctx.send(
                f"An error occurred while processing this request:\n{str(e)}"
            )

        else:
            if not song:
                return

            await ctx.player.songs.put(song)

            if ctx.player.is_playing:
                await ctx.send(f"**\N{PAGE FACING UP} Enqueued** {str(song)}")

            elif not ctx.player._notify:
                await ctx.send(
                    f"**\N{MULTIPLE MUSICAL NOTES} Now playing** `{song.title}`"
                )

    @commands.command(hidden=True)
    @commands.guild_only()
    @commands.is_owner()
    async def ytdl_test(self, ctx):
        """Tests YTDL to see if it works."""

        if not ctx.player:
            player = self.create_player(ctx)

        partial = functools.partial(
            Song.ytdl.extract_info,
            "hat kid electro",
            download=False,
            process=False,
        )

        try:
            data = await self.bot.loop.run_in_executor(None, partial)

        except youtube_dl.DownloadError as e:
            self.bot.log.exception("Could not connect to YouTube")
            error = "".join(traceback.format_exception(type(e), e, e.__traceback__, 1))
            return await ctx.send(f"Could not connect to YouTube!```py\n{error}```")

        if not data:
            return await ctx.send("YouTube did not return any data.")

        await ctx.send("Successfully connected to YouTube with youtube_dl")

    @queue_remove.before_invoke
    @volume.before_invoke
    @now.before_invoke
    @pause.before_invoke
    @resume.before_invoke
    @stop.before_invoke
    @skip.before_invoke
    @skipto.before_invoke
    @queue.before_invoke
    @queue_save.before_invoke
    @queue_clear.before_invoke
    @shuffle.before_invoke
    @notify.before_invoke
    @loop.before_invoke
    @loop_queue.before_invoke
    @seek.before_invoke
    async def ensure_player(self, ctx):
        if not ctx.cog.get_player(ctx):
            raise NoPlayerError()

    @join.before_invoke
    @play.before_invoke
    @playbin.before_invoke
    @search.before_invoke
    async def ensure_player_channel(self, ctx):
        if not ctx.author.voice or not ctx.author.voice.channel:
            raise NotListeningError("You are not connected to a voice channel.")

        if ctx.voice_client:
            if ctx.voice_client.channel != ctx.author.voice.channel:
                try:
                    await is_dj().predicate(ctx)
                    hint = f" Use `{ctx.prefix}summon` to summon the bot to a channel."
                except NotDJ:
                    hint = ""

                raise NotListeningError(f"{self.bot.user.name} is in another voice channel.{hint}")

    # music db management commands

    def get_cache_size(self):
        return sum(os.path.getsize("cache/" + f) for f in os.listdir("cache") if os.path.isfile("cache/" + f))

    def get_file_size(self, fp):
        return os.path.getsize(fp)

    @commands.group(aliases=["mdb"], invoke_without_command=True)
    @commands.is_owner()
    async def musicdb(self, ctx):
        """Commands to manage the music db."""

        query = "SELECT COUNT(*), SUM(plays) FROM songs;"
        count, total_plays = await ctx.db.fetchrow(query)

        total_plays = total_plays or 0

        query = "SELECT info->>'duration', plays FROM songs;"
        records = await ctx.db.fetch(query)

        total = 0
        total_with_plays = 0

        for duration, plays in records:
            if not duration:
                continue
            duration = float(duration)
            total += duration
            total_with_plays += duration * plays

        total = round(total)
        total_with_plays = round(total_with_plays)

        duration = Song.parse_duration(total)
        duration_with_plays = Song.parse_duration(total_with_plays)

        cache_size = await self.bot.loop.run_in_executor(None, self.get_cache_size)

        await ctx.send(
            f"Music database contains **{count:,} songs** with a total of **{total_plays:,} plays**.\n"
            f"That's **{duration}** of music cached, and **{duration_with_plays}** of music played!\n"
            f"The total size of the cache folder is {humanize.naturalsize(cache_size, binary=True)}."
        )

    @musicdb.command(name="list", aliases=["all"])
    @commands.is_owner()
    async def musicdb_list(self, ctx):
        """Lists all songs in the database."""

        query = "SELECT id, title, plays, last_updated FROM songs;"
        records = await ctx.db.fetch(query)

        songs = []
        for song_id, title, plays, last_updated in records:
            formatted = humantime.timedelta(last_updated, brief=True, accuracy=1, discord_fmt=False)
            songs.append(
                f"{title} # ID: {song_id} ({plays:,} plays) last updated {formatted}"
            )

        pages = ctx.pages(songs, per_page=10, title="Music Database")
        await pages.start()

    @musicdb.command(name="search", aliases=["find"])
    @commands.is_owner()
    async def musicdb_search(self, ctx, *, song):
        """Searchs the database for songs."""

        query = """SELECT id, title, plays, last_updated, (info->>'duration')::DOUBLE PRECISION AS duration
                   FROM songs
                   ORDER BY similarity(title, $1) DESC
                   LIMIT 20;
                """

        records = await ctx.db.fetch(query, song)

        if not records:
            return await ctx.send("No matching songs found.")

        songs = []
        for song_id, title, plays, last_updated, duration in records:
            formatted = humantime.timedelta(last_updated, brief=True, accuracy=1, discord_fmt=False)
            dur = Song.timestamp_duration(round(duration)) if duration else "none"
            songs.append(
                f"{title} # ID: {song_id} ({plays:,} plays) duration: {dur} last updated {formatted}"
            )

        pages = ctx.pages(songs, per_page=10, title=f"Results for '{song}'")
        await pages.start()

    @musicdb.command(name="info", aliases=["show"])
    @commands.is_owner()
    async def musicdb_info(self, ctx, *, song):
        """Similar to search, but only shows the first result."""

        try:
            song = int(song)
            condition = "id=$1"
        except ValueError:
            condition = "song_id=$1"

        query = f"SELECT * FROM songs WHERE {condition} LIMIT 1;"
        record = await ctx.db.fetchrow(query, song)

        if not record:
            query = """SELECT *
                       FROM songs
                       ORDER BY similarity(title, $1) DESC
                       LIMIT 1;
                    """
            record = await ctx.db.fetchrow(query, song)

            if not record:
                return await ctx.send("No matching songs found.")

        song = Song.from_record(record, ctx)

        partial = functools.partial(self.get_file_size, song.filename)
        size = await self.bot.loop.run_in_executor(None, partial)
        filesize = humanize.naturalsize(size, binary=True)

        em = Player.now_playing_embed(song, "Song Info", db_info=True, filesize=filesize)
        await ctx.send(embed=em)

    @flags.add_flag("--delete-file", action="store_true")
    @musicdb.command(name="delete", aliases=["remove"], cls=NoUsageFlagCommand)
    @commands.is_owner()
    async def musicdb_delete(self, ctx, song_id: int, **flags):
        """Deletes a song from the database.

        Use `--delete-file` to delete the song's file too.
        """

        query = (
            """DELETE FROM songs WHERE id=$1 RETURNING songs.title, songs.filename;"""
        )
        record = await ctx.db.fetchrow(query, song_id)

        if not record:
            return await ctx.send(f"No song with the id of `{song_id}`")

        title, filename = record

        if flags["delete_file"]:
            try:
                os.remove(filename)
                human_friendly = f" and removed file `{filename}`"
            except Exception as e:
                human_friendly = (
                    f", but failed to delete file `{filename}`\n"
                    f"```py\n{str(e)}\n```"
                )

        else:
            human_friendly = ""

        await ctx.send(f"Deleted song `{title}`{human_friendly}")

    async def get_song_info(self, ctx, old_info):
        webpage_url = old_info["webpage_url"]

        partial = functools.partial(
            Song.ytdl.extract_info, old_info["webpage_url"], download=False
        )

        try:
            processed_info = await self.bot.loop.run_in_executor(None, partial)

        except youtube_dl.DownloadError as e:
            await ctx.send(f"Error while fetching `{webpage_url}`\n```\n{e}\n```")
            return

        else:
            if processed_info is None:
                await ctx.send(f"Couldn't fetch `{webpage_url}`")
                return

            if "entries" not in processed_info:
                info = processed_info

            else:
                info = None
                while info is None:
                    try:
                        info = processed_info["entries"].pop(0)

                    except IndexError as e:
                        await ctx.send(
                            f"Couldn't retrieve any matches for `{webpage_url}`\n```\n{e}\n```"
                        )
                        return

        if not info:
            await ctx.send(f"Couldn't fetch info for {webpage_url}")
            return

        return info

    @musicdb.command(name="refresh", aliases=["update"])
    @commands.is_owner()
    async def musicdb_refresh(self, ctx, song_id: int):
        """Updates cached information about a song."""

        query = "SELECT info FROM songs WHERE id=$1;"
        record = await ctx.db.fetchrow(query, song_id)

        if not record:
            return await ctx.send(f"No song with the id of `{song_id}`")

        old_info = record[0]

        info = await self.get_song_info(ctx, old_info)

        if not info:
            return

        query = """UPDATE songs
                   SET info=$1, last_updated=(now() at time zone 'utc')
                   WHERE id=$2;
                """

        await ctx.db.execute(query, info, song_id)

        title = info["title"]
        await ctx.send(ctx.tick(True, f"Updated info for `{title}`"))

    @musicdb.command(name="stats")
    @commands.is_owner()
    async def musicdb_stats(self, ctx):
        """Shows stats about the music database."""

        await ctx.typing()

        places = (
            "`1.`",
            "`2.`",
            "`3.`",
            "`4.`",
            "`5.`",
        )

        query = "SELECT COUNT(*), SUM(plays), MIN(registered_at) FROM songs;"
        count = await ctx.db.fetchrow(query)

        em = discord.Embed(
            title="Song Stats",
            color=colors.PRIMARY,
            timestamp=count[2] or datetime.datetime.utcnow(),
        )

        em.description = f"Music database contains **{count[0]:,} songs** with a total of **{count[1]:,} plays**."
        em.set_footer(text="First song registered")

        query = """SELECT title, plays
            FROM songs
            ORDER BY plays DESC
            LIMIT 5;
        """

        records = await ctx.db.fetch(query)

        formatted = []
        for (i, (title, plays)) in enumerate(records):
            formatted.append(f"{places[i]} **{title}** ({plays:,} plays)")

        value = "\n".join(formatted) or "None"
        em.add_field(name=":trophy: Top Songs", value=value, inline=False)

        query = """SELECT title, (info->>'duration')::DOUBLE PRECISION AS "dur"
                   FROM SONGS
                   ORDER BY dur DESC
                   LIMIT 5;
        """
        records = await ctx.db.fetch(query)

        formatted = []
        for (i, (title, duration)) in enumerate(records):
            dur = Song.timestamp_duration(round(duration)) if duration else "none"
            if len(title) > 30:
                title = title[:29] + "..."
            formatted.append(
                f"{places[i]} **{title}** ({dur})"
            )

        value = "\n".join(formatted) or "None"
        em.add_field(name=":clock10: Longest Songs", value=value, inline=False)

        await ctx.send(embed=em)


async def setup(bot):
    await bot.add_cog(Music(bot))
