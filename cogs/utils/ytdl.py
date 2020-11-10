from discord.ext import commands
import discord

import asyncio
import functools
import logging
import os
import re
import youtube_dl


log = logging.getLogger("clam.music.ytdl")


# Silence useless bug reports messages
youtube_dl.utils.bug_reports_message = lambda: ""


class YTDLError(commands.CommandError):
    pass


class Song:
    YTDL_OPTIONS = {
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

    def __init__(
        self,
        ctx: commands.Context,
        *,
        data: dict,
        source: discord.FFmpegPCMAudio = None,
        volume: float = 0.5,
        filename=None,
    ):
        self.source = source

        self.ctx = ctx
        self.requester = ctx.author
        self.channel = ctx.channel
        self.data = data
        self.filename = filename
        self._volume = volume

        self.id = data.get("id")
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
        source = discord.FFmpegPCMAudio(self.filename, **self.FFMPEG_OPTIONS)
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

        return self

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
    async def get_song(
        cls,
        ctx: commands.Context,
        search: str,
        *,
        loop: asyncio.BaseEventLoop = None,
        send_errors=True,
    ):
        loop = loop or asyncio.get_event_loop()

        log.info(f"Searching for '{search}'")

        song_id = cls.parse_youtube_id(search)

        log.info(f"Search database for id: {song_id or search}")
        song = await cls.fetch_from_database(ctx, song_id or search)

        if song:
            log.info(f"Found song in database: {song.id}")
            return song

        log.info("Song not in database, fetching info from youtube")

        partial = functools.partial(
            cls.ytdl.extract_info, search, download=False, process=False
        )
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
                raise YTDLError(
                    "Couldn't find anything that matches `{}`".format(search)
                )

        webpage_url = process_info["webpage_url"]

        log.info(f"Found URL '{webpage_url}'")

        song_id = process_info.get("id")
        extractor = process_info.get("extractor")

        song = await cls.fetch_from_database(ctx, song_id, extractor)

        if song:
            log.info(f"Song '{extractor}-{song_id}' in database, skipping further extraction")
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

        partial = functools.partial(
            cls.ytdl.extract_info, webpage_url, download=download
        )
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
                        """

                await ctx.db.execute(
                    query, filename, info.get("title"), song_id, extractor, info
                )

            return cls(
                ctx,
                data=info,
                filename=filename,
            )

    @classmethod
    async def get_playlist(
        cls, ctx: commands.Context, search: str, *, loop: asyncio.BaseEventLoop = None
    ):
        loop = loop or asyncio.get_event_loop()

        log.info("Searching for playlist")

        partial = functools.partial(
            cls.ytdl.extract_info, search, download=False, process=False
        )
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

        log.info("Fetching songs in playlist")

        playlist = []
        counter = 0
        for video in data_list:
            webpage_url = video["url"]
            log.info(f"Song: '{webpage_url}'")

            filename = cls.ytdl.prepare_filename(video)[:-3] + ".webm"
            if os.path.isfile(filename):
                log.info("Song is already downloaded. Skipping download.")
                download = False
            else:
                log.info("Downloading song...")
                download = True

            full = functools.partial(
                cls.ytdl.extract_info, webpage_url, download=download
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
                filename = cls.ytdl.prepare_filename(info)
                source = cls(
                    ctx,
                    data=info,
                    filename=filename,
                )
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
            return Song.timestamp_duration(duration)

        return ", ".join(duration_str)

    @staticmethod
    def timestamp_duration(duration: int):
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)

        duration = ""
        if hours > 0:
            duration += f"{hours}:"
            minutes = f"{minutes:02d}"
        duration += f"{minutes}:{seconds:02d}"
        return duration
