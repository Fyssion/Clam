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


"""
Copyright (c) 2019 Valentin B.
A simple music bot written in discord.py using youtube-dl.
Though it's a simple example, music bots are complex and require much time and knowledge until they work perfectly.
Use this as an example or a base for your own bot and extend it as you want. If there are any bugs, please let me know.
Requirements:
Python 3.5+
pip install -U discord.py pynacl youtube-dl
You also need FFmpeg in your PATH environment variable or the FFmpeg.exe binary in your bot's directory on Windows.
"""

# Silence useless bug reports messages
youtube_dl.utils.bug_reports_message = lambda: ''


class VoiceError(Exception):
    pass


class YTDLError(Exception):
    pass


class YTDLSource(discord.PCMVolumeTransformer):
    YTDL_OPTIONS = {
        'format': 'bestaudio/best',
        'extractaudio': True,
        'audioformat': 'mp3',
        'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        'restrictfilenames': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'quiet': True,
        'no_warnings': True,
        'default_search': 'auto',
        'source_address': '0.0.0.0',
    }

    FFMPEG_OPTIONS = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn',
    }

    ytdl = youtube_dl.YoutubeDL(YTDL_OPTIONS)

    def __init__(self, ctx: commands.Context, source: discord.FFmpegPCMAudio, *, data: dict, volume: float = 0.5):
        super().__init__(source, volume)

        self.requester = ctx.author
        self.channel = ctx.channel
        self.data = data

        self.uploader = data.get('uploader')
        self.uploader_url = data.get('uploader_url')
        date = data.get('upload_date')
        self.upload_date = date[6:8] + '.' + date[4:6] + '.' + date[0:4]
        self.title = data.get('title')
        self.thumbnail = data.get('thumbnail')
        self.description = data.get('description')
        self.duration = self.parse_duration(int(data.get('duration')))
        self.tags = data.get('tags')
        self.url = data.get('webpage_url')
        self.views = data.get('view_count')
        self.likes = data.get('like_count')
        self.dislikes = data.get('dislike_count')
        self.stream_url = data.get('url')

    def __str__(self):
        return f"`{self.title}`"

    @classmethod
    async def create_source(cls, ctx: commands.Context, search: str, *, loop: asyncio.BaseEventLoop = None):
        loop = loop or asyncio.get_event_loop()

        partial = functools.partial(cls.ytdl.extract_info, search, download=False, process=False)
        data = await loop.run_in_executor(None, partial)

        if data is None:
            raise YTDLError('Couldn\'t find anything that matches `{}`'.format(search))

        if 'entries' not in data:
            process_info = data
        else:
            process_info = None
            for entry in data['entries']:
                if entry:
                    process_info = entry
                    break

            if process_info is None:
                raise YTDLError('Couldn\'t find anything that matches `{}`'.format(search))

        webpage_url = process_info['webpage_url']
        partial = functools.partial(cls.ytdl.extract_info, webpage_url, download=False)
        processed_info = await loop.run_in_executor(None, partial)

        if processed_info is None:
            raise YTDLError('Couldn\'t fetch `{}`'.format(webpage_url))

        if 'entries' not in processed_info:
            info = processed_info
        else:
            info = None
            while info is None:
                try:
                    info = processed_info['entries'].pop(0)
                except IndexError:
                    raise YTDLError('Couldn\'t retrieve any matches for `{}`'.format(webpage_url))

        return cls(ctx, discord.FFmpegPCMAudio(info['url'], **cls.FFMPEG_OPTIONS), data=info)

    @staticmethod
    def parse_duration(duration: int):
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)

        duration = []
        if days > 0:
            duration.append('{} days'.format(days))
        if hours > 0:
            duration.append('{} hours'.format(hours))
        if minutes > 0:
            duration.append('{} minutes'.format(minutes))
        if seconds > 0:
            duration.append('{} seconds'.format(seconds))

        return ', '.join(duration)


class Song:
    __slots__ = ('source', 'requester')

    def __init__(self, source: YTDLSource):
        self.source = source
        self.requester = source.requester

    def create_embed(self, title = "Now playing"):
        embed = (discord.Embed(title=title,
                               description='```css\n{0.source.title}\n```'.format(self),
                               color=discord.Color.blurple())
                 .add_field(name='Duration', value=self.source.duration)
                 .add_field(name='Requested by', value=self.requester.mention)
                 .add_field(name='Uploader', value='[{0.source.uploader}]({0.source.uploader_url})'.format(self))
                 .add_field(name='URL', value='[Click]({0.source.url})'.format(self))
                 .set_thumbnail(url=self.source.thumbnail))

        return embed
    
    def create_message(self):
        return f"**:notes: Now playing** `{self.source.title}`"


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


class VoiceState:
    def __init__(self, bot: commands.Bot, ctx: commands.Context):
        self.bot = bot
        self._ctx = ctx

        self.current = None
        self.voice = None
        self.next = asyncio.Event()
        self.songs = SongQueue()

        self._loop = False
        self._volume = 0.5
        self.skip_votes = set()

        self.audio_player = bot.loop.create_task(self.audio_player_task())

    def __del__(self):
        self.audio_player.cancel()

    @property
    def loop(self):
        return self._loop

    @loop.setter
    def loop(self, value: bool):
        self._loop = value

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = value

    @property
    def is_playing(self):
        if self.voice:
            if self.voice.is_paused(): # The player is techincally in the middle of playing a song
                return True
            return self.voice.is_playing() == True and self.current is not None
        return self.voice is not None and self.current is not None

    @property
    def has_started(self):
        return self.voice is not None and self.current is not None

    async def audio_player_task(self):
        while True:
            self.next.clear()

            if not self.loop:

                # if self.songs.qsize() < 1:
                #     self.bot.loop.create_task(self.stop())
                #     return
                
                # Try to get the next song within 3 minutes.
                # If no song will be added to the queue in time,
                # the player will disconnect due to performance
                # reasons.
                try:
                    async with timeout(180):  # 3 minutes
                        self.current = await self.songs.get()
                except asyncio.TimeoutError:
                    self.bot.loop.create_task(self.stop())
                    return

            self.current.source.volume = self._volume
            self.voice.play(self.current.source, after=self.play_next_song)
            if not self.loop:
                await self.current.source.channel.send(self.current.create_message())
            # else:
            #     await self.current.source.channel.send("Looping...")

            await self.next.wait()

    def play_next_song(self, error=None):
        if error:
            raise VoiceError(str(error))

        self.next.set()

    def skip(self):
        self.skip_votes.clear()

        if self.is_playing:
            self.voice.stop()

    async def stop(self):
        self.songs.clear()

        if self.voice:
            await self.voice.disconnect()
            self.voice = None

def is_dj():
    def predicate(ctx):
        author = ctx.author
        role_cap = discord.utils.get(ctx.guild.roles, name="DJ")
        role_lower = role = discord.utils.get(ctx.guild.roles, name="dj")
        return author.guild_permissions.manage_guild or role_cap in author.roles or role_lower in author.roles
    return commands.check(predicate)

class Music(commands.Cog, name = ":notes: Music"):
    """Listen to music in any voice channel!\nUse `r.play` to play a song."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_states = {}

    def get_voice_state(self, ctx: commands.Context):
        state = self.voice_states.get(ctx.guild.id)
        if not state:
            state = VoiceState(self.bot, ctx)
            self.voice_states[ctx.guild.id] = state

        return state

    def cog_unload(self):
        for state in self.voice_states.values():
            self.bot.loop.create_task(state.stop())

    def cog_check(self, ctx: commands.Context):
        if not ctx.guild:
            raise commands.NoPrivateMessage('This command can\'t be used in DM channels.')

        return True

    async def cog_before_invoke(self, ctx: commands.Context):
        ctx.voice_state = self.get_voice_state(ctx)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        await ctx.send(f"Oops: {str(error)}")
        print(str(error))

    async def votes(self, ctx, cmd: str, func, param = None):
        async def run_func():
            if param:
                await func(param)
            else:
                await func()
        voter = ctx.message.author
        if_is_requester = (voter == ctx.voice_state.current.requester)
        if_has_perms = voter.guild_permissions.manage_guild
        if len(ctx.voice_state.voice.channel.members) < 5:
            if len(ctx.voice_state.voice.channel.members) < 3:
                is_only_user = True
            else:
                is_only_user = False
                required_votes = len(ctx.voice_state.voice.channel.members) - 1
        else:
            is_only_user = False
            required_votes = 3
        if if_is_requester or if_has_perms or is_only_user:
            await run_func()

        elif voter.id not in ctx.voice_state.skip_votes:
            ctx.voice_state.skip_votes.add(voter.id)
            total_votes = len(ctx.voice_state.skip_votes)

            if total_votes >= required_votes:
                await run_func()
            else:
                await ctx.send(f'{cmd.capitalize()} vote added, currently at `{total_votes}/{required_votes}`')

        else:
            await ctx.send(f'You have already voted to {cmd}.')

    @commands.command(name='join', description = "Joins a voice channel.", aliases = ['connect'], invoke_without_subcommand=True)
    async def _join(self, ctx: commands.Context):

        destination = ctx.author.voice.channel
        if ctx.voice_state.voice:
            await ctx.voice_state.voice.move_to(destination)
            return

        ctx.voice_state.voice = await destination.connect()

    @commands.command(name='summon', description = "Summons the bot to a voice channel. If no channel was specified, it joins your channel.")
    @is_dj()
    async def _summon(self, ctx, *, channel: discord.VoiceChannel = None):

        if not channel and not ctx.author.voice:
            raise VoiceError('You are neither connected to a voice channel nor specified a channel to join.')

        destination = channel or ctx.author.voice.channel
        if ctx.voice_state.voice:
            await ctx.voice_state.voice.move_to(destination)
            return

        ctx.voice_state.voice = await destination.connect()

    @commands.command(name='leave', description = "Clears the queue and leaves the voice channel.", aliases=['disconnect'])
    @is_dj()
    async def _leave(self, ctx):
        

        if not ctx.voice_state.voice:
            return await ctx.send('Not connected to any voice channel.')

        await ctx.voice_state.stop()
        del self.voice_states[ctx.guild.id]

    def get_volume_emoji(self, volume):
        if volume >= 50:
            return ":loud_sound:"
        else:
            return ":sound:"

    @commands.command(name='volume', description = "Sets the volume of the player.")
    async def _volume(self, ctx, *, volume: int = None):
        return await ctx.send("To change the volume:\nRight click on me in the voice channel, and adjust the `User Volume` slider.")

        if not volume:
            volume = ctx.voice_state.volume * 100
            return await ctx.send(f'**{self.get_volume_emoji(volume)} Volume:** `{volume}%`')
        

        if not ctx.voice_state.is_playing:
            return await ctx.send('Nothing is being played at the moment.')

        if 0 > volume > 100:
            return await ctx.send('Volume must be between 0 and 100')

        ctx.voice_state.volume = volume / 100
        await ctx.send(f'**{self.get_volume_emoji(volume)} Volume:** `{volume}%`')

    @commands.command(name='now', description = "Displays the currently playing song.", aliases=['current', 'playing'])
    async def _now(self, ctx):
        if not ctx.voice_state.is_playing:
            return await ctx.send("Not currently playing a song.")
        if ctx.voice_state.voice.is_paused():
            em = ctx.voice_state.current.create_embed("Currently Paused")
        else:
            em = ctx.voice_state.current.create_embed()
            
        await ctx.send(embed=em)

    @commands.command(name='pause', description = "Pauses the currently playing song.")
    @is_dj()
    async def _pause(self, ctx):

        if ctx.voice_state.is_playing and ctx.voice_state.voice.is_playing():
            ctx.voice_state.voice.pause()
            await ctx.send(f'**:pause_button: Paused** `{ctx.voice_state.current.source.title}`')

    @commands.command(name='resume', description = "Resumes a currently paused song.")
    @is_dj()
    async def _resume(self, ctx):

        if ctx.voice_state.is_playing and ctx.voice_state.voice.is_paused():
            ctx.voice_state.voice.resume()
            await ctx.send(f"**:arrow_forward: Resuming** `{ctx.voice_state.current.source.title}`")

    @commands.command(name='stop', description = "Stops playing song and clears the queue.")
    @is_dj()
    async def _stop(self, ctx):

        ctx.voice_state.songs.clear()

        if ctx.voice_state.is_playing:
            ctx.voice_state.voice.stop()
            await ctx.send('**:stop_button: Song stopped and queue cleared.**')

    @commands.command(name='skip', description = "Vote to skip a song. The requester can automatically skip.")
    async def _skip(self, ctx):
        async def skip_song():
            await ctx.message.add_reaction('⏭')
            ctx.voice_state.skip()
        
        if not ctx.voice_state.is_playing:
            return await ctx.send("Nothing is playing. There is nothing to skip!")

        await self.votes(ctx, "skip", skip_song)

    @commands.command(name='queue', description = "Shows the player's queue. You can optionally select the page.", usage = "<page #>")
    async def _queue(self, ctx, *, page: int = 1):

        if len(ctx.voice_state.songs) == 0:
            return await ctx.send('Queue is empty. Nothing to display!')

        items_per_page = 10
        pages = math.ceil(len(ctx.voice_state.songs) / items_per_page)

        start = (page - 1) * items_per_page
        end = start + items_per_page

        queue = ''
        for i, song in enumerate(ctx.voice_state.songs[start:end], start=start):
            queue += f'`{i+1}.` **[{song.source.title}]({song.source.url})**\n'

        embed = discord.Embed(title = "Queue",description=f'**{len(ctx.voice_state.songs)} Song(s):**\n\n{queue}')
        embed.set_footer(text=f'Page {page} of {pages}')
        await ctx.send(embed=embed)

    @commands.command(name='shuffle', description = "Shuffles the queue.")
    async def _shuffle(self, ctx):

        async def shuffle_queue():
            ctx.voice_state.songs.shuffle()
            await ctx.send("**:twisted_rightwards_arrows: Shuffled songs**")

        if len(ctx.voice_state.songs) == 0:
            return await ctx.send('Queue is empty. Nothing to shuffle!')

        await self.votes(ctx, "shuffle", shuffle_queue)

    @commands.command(name='remove', description = "Removes a song from the queue at a given index.", usage = "[song #]")
    async def _remove(self, ctx, index: int):
        async def remove_song(index):
            to_be_removed = ctx.voice_state.songs[index - 1].source.title
            ctx.voice_state.songs.remove(index - 1)
            await ctx.send(f"**:wastebasket: Removed** `{to_be_removed}`")

        if len(ctx.voice_state.songs) == 0:
            return await ctx.send('Queue is empty. Nothing to remove!')
        
        await self.votes(ctx, "remove", remove_song, index)

    @commands.command(name='loop', description = "Loops/unloops the currently playing song.")
    async def _loop(self, ctx):

        return await ctx.send(":warning: :( Sorry, this feature is currently under maintenance. Check back later.")

        if not ctx.voice_state.is_playing:
            return await ctx.send('Nothing being played at the moment.')

        # Inverse boolean value to loop and unloop.
        ctx.voice_state.loop = not ctx.voice_state.loop
        if ctx.voice_state.loop:
            await ctx.send(f"**:repeat_one: Now looping** `{ctx.voice_state.current.source.title}`")
        else:
            await ctx.send(f"**:repeat_one: :x: No longer looping** `{ctx.voice_state.current.source.title}`")

    
    async def get_haste(self, url='https://hastebin.com'):
        if ".com" in url:
            args = url.split(".com/")
            args.insert(1, ".com/raw/")
        elif ".io" in url: # Pastie.io in particular
            args = url.split(".io/")
            args.insert(1, ".io/raw/")
        else:
            url += "/raw"
        url = "".join(args)
        try:
            async with timeout(10):
                async with self.bot.session.get(url) as resp:
                    f = await resp.read()
        except asyncio.TimeoutError:
            await ctx.send(":warning: Could not fetch data from hastebin. Is the site down? Try https://www.pastebin.com")
            return None
        async with self.bot.session.get(url) as resp:
            f = await resp.read()
            f = f.decode("utf-8")
            return f

    async def hastebin_playlist(self, ctx, search):
        output = await self.get_haste(search)
        if not output:
            return
        youtube_urls = "(?:https?://)?(?:www.)?(?:youtube.com|youtu.be)/(?:watch\?v=)?([^\s]+)"
        if output == "404: Not Found":
            return await ctx.send(":warning: This is not a hastebin or hastebin-like website.")
        if len(re.findall(youtube_urls, output)) == 0:
            return await ctx.send(":warning: There are no YouTube URLS in this bin.")
        videos = output.splitlines()
        for video in videos:
            try:
                source = await YTDLSource.create_source(ctx, video, loop=self.bot.loop)
            except YTDLError as e:
                await ctx.send(f"An error occurred while processing this request: ```py {str(e)}```")
            else:
                song = Song(source)

                await ctx.voice_state.songs.put(song)
                if ctx.voice_state.is_playing:
                    await ctx.send(f'**:page_facing_up: Enqueued** {str(source)}')


    @commands.command(name='play', description = "Search for a song and play it.", aliases = ['p', 'yt'], usage = "[song]")
    async def _play(self, ctx, *, search: str = None):

        if not search and ctx.voice_state.is_playing and ctx.voice_state.voice.is_paused()\
             and ctx.author.guild_permissions.manage_guild:
            ctx.voice_state.voice.resume()
            return await ctx.send(f"**:arrow_forward: Resuming** `{ctx.voice_state.current.source.title}`")
        if not search:
            return await ctx.send("Please specify a song to play/search for.")

        if not ctx.voice_state.voice:
            await ctx.invoke(self._join)

        urls = "http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"
        if len(re.findall(urls, search)) > 0:
            youtube_urls = "(?:https?://)?(?:www.)?(?:youtube.com|youtu.be)/(?:watch\?v=)?([^\s]+)"
            if len(re.findall(youtube_urls, search)) > 0:
                pass
            else:
                await ctx.send(f"**:green_book: Fetching hastebin** `{search}`")
                await self.hastebin_playlist(ctx, search)
                return

        await ctx.send(f"**:mag: Searching** `{search}`")

        async with ctx.typing():
            try:
                source = await YTDLSource.create_source(ctx, search, loop=self.bot.loop)
            except YTDLError as e:
                await ctx.send(f"An error occurred while processing this request: ```py {str(e)}```")
            else:
                song = Song(source)

                await ctx.voice_state.songs.put(song)
                if ctx.voice_state.is_playing:
                    await ctx.send(f'**:page_facing_up: Enqueued** {str(source)}')

    @_join.before_invoke
    @_play.before_invoke
    async def ensure_voice_state(self, ctx):
        if not ctx.author.voice or not ctx.author.voice.channel:
            raise commands.CommandError('You are not connected to any voice channel.')

        if ctx.voice_client:
            if ctx.voice_client.channel != ctx.author.voice.channel:
                raise commands.CommandError('Bot is already in a voice channel.')


def setup(bot):
    bot.add_cog(Music(bot))
