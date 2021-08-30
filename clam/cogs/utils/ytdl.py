import asyncio
import datetime
import functools
import logging
import re
import os

import asyncpg
import discord
import youtube_dl
from discord.ext import commands, menus

from .emojis import BACK, FORWARD, GREEN_TICK, RED_TICK
from .formats import plural


log = logging.getLogger("clam.music.ytdl")


# Silence useless bug reports messages
youtube_dl.utils.bug_reports_message = lambda: ""


class YTDLError(commands.CommandError):
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
    async def select_pages(self, button: discord.ui.Button, interaction: discord.Interaction):
        """select the current page"""
        self.selected_page = self.current_page
        await interaction.response.defer()
        await self.message.delete()
        self.stop()

    @discord.ui.button(label="Previous")
    async def go_to_previous_page(self, button: discord.ui.Button, interaction: discord.Interaction):
        """go to the previous page"""
        await self.show_checked_page(interaction, self.current_page - 1)

    @discord.ui.button(label="Next")
    async def go_to_next_page(self, button: discord.ui.Button, interaction: discord.Interaction):
        """go to the next page"""
        await self.show_checked_page(interaction, self.current_page + 1)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def stop_pages(self, button: discord.ui.Button, interaction: discord.Interaction):
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
        self.total_seconds = int(data.get("duration"))
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

        log.info(f"Searching database for '{search}'")

        song_id = cls.parse_youtube_id(search)

        log.info(f"Searching database for id: {song_id or search}")
        song = await cls.fetch_from_database(ctx, song_id or search)

        if song:
            log.info(f"Found song in database: {song.id}")
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
            log.warning(f"Error while searching for '{search}': {e}")
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
            return search, True

        log.info(f"Found URL for '{webpage_url}'")

        song_id = process_info.get("id")
        extractor = process_info.get("extractor")

        song = await cls.fetch_from_database(ctx, song_id, extractor)

        if song:
            log.info(
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
            log.info("Song is already downloaded. Skipping download.")
            download = False
        else:
            log.info("Downloading song...")
            download = True

        return webpage_url, download

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

        log.info(f"Searching for '{search}'")

        song_id = cls.parse_youtube_id(search)

        song_id = song_id or search

        log.info(f"Searching database for id: {song_id}")
        song = await cls.fetch_from_database(ctx, song_id)

        if song:
            log.info(f"Found song in database: {song.id}")
            return song

        log.info("Searching song aliases")
        song = await cls.search_song_aliases(ctx, search)

        if song:
            log.info(f"Found song alias in database: {song.id}")
            return song

        log.info("Song not in database, searching youtube")

        if not skip_resolve:
            result = await cls.resolve_webpage_url(
                ctx, search, send_errors=send_errors
            )
            if not result:
                return

            if isinstance(result, Song):
                return result

            webpage_url, download = result

        else:
            webpage_url = search
            download = True

        partial = functools.partial(
            cls.ytdl.extract_info, webpage_url, download=download
        )
        try:
            processed_info = await loop.run_in_executor(None, partial)
        except youtube_dl.DownloadError as e:
            log.warning(f"Error while downloading '{webpage_url}': {e}")
            if send_errors:
                await ctx.send(
                    f"**:x: Error while downloading** `{webpage_url}`\n```\n{e}\n```"
                )
                return
        else:
            if processed_info is None:
                raise YTDLError("Couldn't fetch `{}`".format(webpage_url))

            log.info("Fetched song info")

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
                log.info(f"Song '{extractor}-{song_id}' not in database, inserting")
                query = """INSERT INTO songs (filename, title, song_id, extractor, info)
                           VALUES ($1, $2, $3, $4, $5::jsonb)
                           RETURNING songs.id;
                        """

                song_id = await ctx.db.fetchval(
                    query, filename, info.get("title"), song_id, extractor, info
                )

            else:
                log.info(
                    f"Song '{extractor}-{song_id}' is already in database, skipping insertion"
                )
                song_id = song.db_id

            query = """INSERT INTO song_aliases (alias, expires_at, song_id)
                       VALUES ($1, $2, $3);
                    """

            expires = datetime.datetime.utcnow() + datetime.timedelta(days=30)

            log.info(f"Inserting song alias '{search.lower()}' into database...")
            async with ctx.db.acquire() as conn:
                async with conn.transaction():
                    try:
                        await ctx.db.execute(query, search.lower(), expires, song_id)

                    except asyncpg.UniqueViolationError:
                        log.info(
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

        log.info("Searching for playlist")

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

        log.info("Fetching songs in playlist")

        playlist = []
        counter = 0
        for i, video in enumerate(data_list):
            webpage_url = video["url"]
            log.info(f"Song: '{webpage_url}'")

            song_id = video.get("id")
            extractor = video.get("extractor")

            song = await cls.fetch_from_database(ctx, song_id, extractor)

            if song:
                log.info(
                    f"Song '{extractor}-{song_id}' in database, skipping further extraction"
                )
                playlist.append(song)
                continue

            filename = cls.playlist_ytdl.prepare_filename(video)[:-3] + ".webm"
            if os.path.isfile(filename):
                log.info("Song is already downloaded. Skipping download.")
                download = False
            else:
                log.info("Downloading song...")
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
                    log.info(f"Song '{extractor}-{song_id}' not in database, inserting")
                    query = """INSERT INTO songs (filename, title, song_id, extractor, info)
                               VALUES ($1, $2, $3, $4, $5::jsonb)
                            """

                    await ctx.db.execute(
                        query, filename, info.get("title"), song_id, extractor, info
                    )

                else:
                    log.info(
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
