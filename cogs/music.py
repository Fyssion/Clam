from discord.ext import commands, menus
import discord

import asyncio
from async_timeout import timeout
import re
import functools
from urllib.parse import urlparse
import logging
import sys
import os
import traceback
import youtube_dl
import importlib

from .utils import ytdl
from .utils import music_player


log = logging.getLogger("clam.music")
bin_log = logging.getLogger("clam.music.bin")


class BinFetchingError(Exception):
    pass


def is_dj():
    def predicate(ctx):
        dev = 224513210471022592
        author = ctx.author
        upper = discord.utils.get(ctx.guild.roles, name="DJ")
        lower = discord.utils.get(ctx.guild.roles, name="dj")
        return (
            author.guild_permissions.manage_guild
            or upper in author.roles
            or lower in author.roles
            or author.id == dev
        )

    return commands.check(predicate)


class Music(commands.Cog):
    """Play music in a voice channel through the bot"""

    def __init__(self, bot):
        self.bot = bot
        self.emoji = ":notes:"
        self.private = True
        self.private_user_overrides = [612816777994305566]
        self.private_guild_overrides = [722184677984698398, 592510013222682669, 704692704113721426]

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
        player = music_player.Player(self.bot, ctx)
        self.players[ctx.guild.id] = player
        ctx.player = player
        return player

    def cog_check(self, ctx):
        if not ctx.guild:
            raise commands.NoPrivateMessage(
                "This command can't be used in DM channels."
            )

        return True

    async def cog_before_invoke(self, ctx):
        ctx.player = self.get_player(ctx)

    async def cog_command_error(self, ctx, error: commands.CommandError):
        if isinstance(error, music_player.VoiceError) or isinstance(error, ytdl.YTDLError):
            await ctx.send(str(error))
            ctx.handled = True

    async def stop_all_players(self):
        for player in self.players.values():
            await player.stop()

        self.bot.players.clear()

        for voice in self.bot.voice_clients:
            await voice.disconnect()

    def delete_all_songs(self):
        for file in os.listdir("cache"):
            if file.endswith(".webm"):
                os.remove(file)

    @commands.command()
    @commands.is_owner()
    async def reload_music_player(self, ctx):
        importlib.reload(music_player)

        await ctx.send("Reloaded music_player")

    @commands.command()
    @commands.is_owner()
    async def reload_ytdl(self, ctx):
        importlib.reload(ytdl)

        await ctx.send("Reloaded ytdl")

    @commands.command()
    @commands.is_owner()
    async def stopall(self, ctx):
        """Stop all players"""

        confirm = await ctx.confirm("Are you sure you want to stop all players?")
        if confirm:
            await self.stop_all_players()
            await ctx.send("Stopped all players.")

        else:
            await ctx.send("Aborted.")

    @commands.command(aliases=["deleteall"])
    @commands.is_owner()
    async def deletesongs(self, ctx):
        """Delete all songs"""
        if self.players:
            return await ctx.send("There are active players. Please use `stopall` first.")

        confirm = await ctx.confirm("Are you sure you want to delete all songs in cache?")
        if confirm:
            self.delete_all_songs()

            await ctx.send("Deleted all songs.")

    @commands.command()
    @commands.is_owner()
    async def allplayers(self, ctx):
        """View all players"""

        players = []

        for player in self.players.values():
            guild_name = discord.utils.escape_mentions(player.ctx.guild.name)
            channel = f"Connected: {player.voice.channel} | " if player.voice else ""
            channel += f"Bound: {player.text_channel}"
            players.append(f"**{guild_name}** - `{channel}`")

        if not players:
            return await ctx.send("No players")

        await ctx.send("\n".join(players))

    @commands.command(
        aliases=["fdisconnect", "fdc"],
    )
    @commands.is_owner()
    async def forcedisconnect(self, ctx):
        """Force disconnect the voice client in this server"""
        if not ctx.voice_client:
            return await ctx.send("Not connected to a voice channel in this server.")

        await ctx.voice_client.disconnect()

        await ctx.send("Disconnected bot from voice.")

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

        if not player.voice:
            return

        if len(player.voice.channel.members) == 1:
            player.pause()

            try:
                await self.bot.wait_for("voice_state_update", timeout=120, check=check)
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
                            "Sorry, I couldn't save your queue."
                        )
                    await player.text_channel.send(
                        "**I saved your queue!**\n"
                        f"To resume where you left off, use this link with the `playbin` command: **{url}**"
                    )
            player.resume()

    async def votes(self, ctx, cmd: str, func, param=None):
        async def run_func():
            if param:
                await func(param)
            else:
                await func()

        voter = ctx.message.author

        if_is_requester = voter == ctx.player.current.requester
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
                await ctx.send(
                    f"{cmd.capitalize()} vote added, "
                    f"currently at `{total_votes}/{required_votes}`"
                )

        else:
            await ctx.send(f"You have already voted to {cmd}.")

    @commands.command(
        name="join",
        aliases=["connect"],
        invoke_without_subcommand=True,
    )
    async def _join(self, ctx):
        """Joins a voice channel."""
        if not ctx.player:
            player = self.create_player(ctx)

        destination = ctx.author.voice.channel
        ctx.player.text_channel = ctx.channel
        if ctx.player.voice:
            await ctx.player.voice.move_to(destination)
        else:
            ctx.player.voice = await destination.connect()
        v_emote = "<:voice_channel:665577300552843294>"
        t_emote = "<:text_channel:661798072384225307>"
        await ctx.send(
            f"**Connected to ** {v_emote}`{destination}` and **bound to** {t_emote}`{ctx.channel}`"
        )

    @commands.command(
        name="summon",
        description="Summons the bot to a voice channel. \
            If no channel was specified, it joins your channel.",
    )
    @is_dj()
    async def _summon(self, ctx, *, channel: discord.VoiceChannel = None):
        if not ctx.player:
            player = self.create_player(ctx)

        if not channel and not ctx.author.voice:
            raise music_player.VoiceError(
                "You are neither connected to a voice channel nor specified a channel to join."
            )

        destination = channel or ctx.author.voice.channel
        ctx.player.text_channel = ctx.channel
        if ctx.player.voice:
            await ctx.player.voice.move_to(destination)
        else:
            ctx.player.voice = await destination.connect()
        v_emote = "<:voice_channel:665577300552843294>"
        t_emote = "<:text_channel:661798072384225307>"
        await ctx.send(
            f"**Connected to ** {v_emote}`{destination}` and **bound to** {t_emote}`{ctx.channel}`"
        )

    async def post(self, content, url="https://mystb.in"):
        async with self.bot.session.post(
            f"{url}/documents", data=content.encode("utf-8"), headers={"User-Agent": "Clam Music Cog"}
        ) as post:
            return url + "/" + (await post.json())["key"]

    @commands.command(
        name="leave",
        aliases=["disconnect"],
    )
    @is_dj()
    async def _leave(self, ctx):
        """Clears the queue and leaves the voice channel."""
        if not ctx.player:
            return await ctx.send("This server doesn't have a player.")

        if not ctx.player.voice:
            if ctx.voice_client:
                ctx.player.voice = ctx.voice_client

            else:
                return await ctx.send("Not connected to any voice channel.")

        if len(ctx.player.songs) > 0:
            songs = ctx.player.songs.to_list()
            songs = [s.url for s in songs]
            songs.insert(0, ctx.player.current.url)

        else:
            songs = None

        await ctx.player.stop()

        del self.players[ctx.guild.id]

        if songs:
            url = await self.post("\n".join(songs))
            if url is None:
                return await ctx.send("Sorry, I couldn't save your queue.")

            await ctx.send(
                "**I saved your queue!**\n"
                f"To resume where you left off, use this link with the `playbin` command: **{url}**"
            )

    def get_volume_emoji(self, volume):
        if volume >= 50:
            return ":loud_sound:"
        else:
            return ":sound:"

    @commands.command(name="volume")
    async def _volume(self, ctx, *, volume: int = None):
        """Sets the volume of the player. Must be between 1 and 100."""
        if not ctx.player:
            return await ctx.send("This server doesn't have a player.")

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

        await ctx.send(f"**{self.get_volume_emoji(volume)} Volume:** `{volume}%`")

    @commands.command(
        name="now",
        aliases=["current", "playing", "np"],
    )
    async def _now(self, ctx):
        """Displays the currently playing song."""
        if not ctx.player:
            return await ctx.send("This server doesn't have a player.")

        if not ctx.player.is_playing:
            return await ctx.send("Not currently playing a song.")

        if ctx.player.voice.is_paused():
            em = ctx.player.now_playing_embed(
                "Currently Paused", ctx.player.duration.get_time()
            )

        else:
            em = ctx.player.now_playing_embed(
                duration=ctx.player.duration.get_time()
            )

        await ctx.send(embed=em)

    @commands.command(name="pause")
    @is_dj()
    async def _pause(self, ctx):
        """Pauses the currently playing song."""
        if not ctx.player:
            return await ctx.send("This server doesn't have a player.")

        if ctx.player.is_playing and ctx.player.voice.is_playing():
            ctx.player.pause()
            song = ctx.player.current.title
            await ctx.send(f"**:pause_button: Paused** `{song}`")

    @commands.command(
        name="resume",
        aliases=["unpause"],
    )
    @is_dj()
    async def _resume(self, ctx):
        """Resumes a currently paused song."""
        if not ctx.player:
            return await ctx.send("This server doesn't have a player.")

        if ctx.player.is_playing and ctx.player.voice.is_paused():
            ctx.player.resume()
            song = ctx.player.current.title
            await ctx.send(f"**:arrow_forward: Resuming** `{song}`")

    @commands.command(
        name="stop"
    )
    @is_dj()
    async def _stop(self, ctx):
        """Stops playing song and clears the queue."""
        if not ctx.player:
            return await ctx.send("This server doesn't have a player.")

        if len(ctx.player.songs) > 0:
            songs = ctx.player.songs.to_list()
            songs = [s.url for s in songs]
            songs.insert(0, ctx.player.current.url)
        else:
            songs = None
        if ctx.player.is_playing:
            ctx.player.voice.stop()

        filenames = [s.filename for s in ctx.player.songs._queue]
        filenames.insert(0, ctx.player.current.filename)

        ctx.player.songs.clear()
        ctx.player.loop = False
        ctx.player.loop_queue = False

        await ctx.send("**:stop_button: Song stopped and queue cleared.**")
        if songs:
            url = await self.post("\n".join(songs))
            if url is None:
                return await ctx.send("Sorry, I couldn't save your queue.")
            await ctx.send(
                "**I saved your queue!**\n"
                f"To resume where you left off, use this link with the `playbin` command: **{url}**"
            )

    @commands.command(
        name="skip",
        aliases=["next"],
    )
    async def _skip(self, ctx):
        """Vote to skip a song. The requester can automatically skip."""
        if not ctx.player:
            return await ctx.send("This server doesn't have a player.")

        async def skip_song():
            await ctx.message.add_reaction("⏭")
            ctx.player.skip()

        if not ctx.player.is_playing:
            return await ctx.send("Nothing is playing. There is nothing to skip!")

        await self.votes(ctx, "skip", skip_song)

    @commands.command(usage="[position]")
    @is_dj()
    async def skipto(self, ctx, *, position: int):
        if not ctx.player:
            return await ctx.send("This server doesn't have a player.")

        if len(ctx.player.songs) < position:
            return await ctx.send(f"The queue has less than {position} song(s).")

        for i in range(position - 1):
            current = await ctx.player.songs.get()

            if ctx.player.loop_queue:
                await ctx.player.songs.put(current)

        ctx.player.skip()

        await ctx.send(f"Skipped to song at position `{position}`")

    @commands.group(
        name="queue",
        aliases=["playlist"],
        invoke_without_command=True,
    )
    async def _queue(self, ctx):
        """Shows the player's queue. You can optionally select the page."""
        if not ctx.player:
            return await ctx.send("This server doesn't have a player.")

        if len(ctx.player.songs) == 0:
            return await ctx.send("Queue is empty. Nothing to display!")

        queue = ctx.player.songs._queue
        total_duration = sum(int(s.data.get("duration")) for s in queue)
        total_duration = ytdl.Song.parse_duration(total_duration)

        pages = menus.MenuPages(
            source=music_player.SearchPages(ctx.player.songs, total_duration), clear_reactions_after=True
        )
        return await pages.start(ctx)

    @_queue.command(
        name="save", description="Save the queue to a bin!", aliases=["upload"]
    )
    @commands.cooldown(1, 10)
    async def _save_queue(self, ctx):
        if not ctx.player:
            return await ctx.send("This server doesn't have a player.")

        if len(ctx.player.songs) == 0:
            return await ctx.send("Queue is empty! Nothing to save.")
        songs = ctx.player.songs.to_list()
        songs = [s.url for s in songs]
        songs.insert(0, ctx.player.current.url)
        url = await self.post("\n".join(songs))
        if url is None:
            return await ctx.send("Sorry, I couldn't save your queue.")
        await ctx.send(f"**Current queue: {url}**")

    @commands.command()
    async def clear(self, ctx):
        """Clears the queue"""
        if not ctx.player:
            return await ctx.send("This server doesn't have a player.")

        ctx.player.songs.clear()

        await ctx.send("**:wastebasket: Cleared queue**")

    @commands.command(name="shuffle")
    async def _shuffle(self, ctx):
        """Shuffles the queue"""
        if not ctx.player:
            return await ctx.send("This server doesn't have a player.")

        async def shuffle_queue():
            ctx.player.songs.shuffle()
            await ctx.send("**:twisted_rightwards_arrows: Shuffled songs**")

        if len(ctx.player.songs) == 0:
            return await ctx.send("Queue is empty. Nothing to shuffle!")

        await self.votes(ctx, "shuffle", shuffle_queue)

    @commands.command(
        name="remove",
        description="Removes a song from the queue at a given index.",
        usage="[song #]",
    )
    async def _remove(self, ctx, index: int):
        if not ctx.player:
            return await ctx.send("This server doesn't have a player.")

        async def remove_song(index):
            to_be_removed = ctx.player.songs[index - 1].title
            ctx.player.songs.remove(index - 1)
            await ctx.send(f"**:wastebasket: Removed** `{to_be_removed}`")

        if len(ctx.player.songs) == 0:
            return await ctx.send("Queue is empty. Nothing to remove!")

        await self.votes(ctx, "remove", remove_song, index)

    @commands.command()
    async def notify(self, ctx):
        if not ctx.player:
            return await ctx.send("This server doesn't have a player.")

        ctx.player.notify = not ctx.player.notify

        if ctx.player.notify:
            await ctx.send("**:bell: Now playing notifications enabled**")

        else:
            await ctx.send("**:no_bell: Now playing notifications disabled**")

    @commands.group(
        name="loop",
        description="Loops/unloops the currently playing song.",
        invoke_without_command=True,
    )
    async def _loop(self, ctx):
        """Loop a single song. To loop the queue use loop queue"""
        if not ctx.player:
            return await ctx.send("This server doesn't have a player.")

        # return await ctx.send(":warning: :( Sorry, this feature is \
        # currently under maintenance. Check back later.")

        if not ctx.player.is_playing and not ctx.player.loop:
            return await ctx.send("Nothing being played at the moment.")

        # Inverse boolean value to loop and unloop.
        ctx.player.loop = not ctx.player.loop
        ctx.player.loop_queue = False
        if ctx.player.loop:
            await ctx.send(
                "**:repeat_one: Now looping** " f"`{ctx.player.current.title}`"
            )
        else:
            await ctx.send(
                "**:repeat_one: :x: No longer looping** "
                f"`{ctx.player.current.title}`"
            )

    @_loop.command(
        name="queue", description="Loop the entire queue.", aliases=["playlist"]
    )
    async def _loop_queue(self, ctx):
        if not ctx.player:
            return await ctx.send("This server doesn't have a player.")

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

    async def get_haste(self, url="https://mystb.in"):
        parsed = urlparse(url)
        newpath = "/raw" + parsed.path
        url = parsed.scheme + "://" + parsed.netloc + newpath

        try:
            async with timeout(10):
                async with self.bot.session.get(url, headers={"User-Agent": "Clam Music Cog"}) as resp:
                    if resp.status != 200:
                        raise BinFetchingError("There was an error while fetching that bin.")
                    f = await resp.read()
        except asyncio.TimeoutError:
            raise TimeoutError(
                ":warning: Could not fetch data from mystbin. \
            Is the site down? Try https://www.pastebin.com"
            )
            return None
        async with self.bot.session.get(url, headers={"User-Agent": "Clam Music Cog"}) as resp:
            f = await resp.read()
            f = f.decode("utf-8")
            return f

    @commands.command(description="Start the current song over from the beginning")
    async def startover(self, ctx):
        if not ctx.player:
            return await ctx.send("This server doesn't have a player.")

        if not ctx.player.is_playing:
            return await ctx.send("Nothing being played at the moment.")

        current = ctx.player.current

        song = ytdl.Song(ctx, data=current.data, filename=current.filename,)

        ctx.player.startover = True

        ctx.player.songs._queue.appendleft(song)
        ctx.player.skip()

        await ctx.send("**⏪ Starting song over**")

    async def hastebin_playlist(self, ctx, search):
        bin_log.info(f"Fetching from bin: '{search}'")
        output = await self.get_haste(search)
        if not output or output == """{"message":"Document not found."}""":
            return await ctx.send("That bin doesn't exist.")
        yt_urls = (
            "(?:https?://)?(?:www.)?(?:youtube.com|youtu.be)/(?:watch\?v=)?([^\s]+)"
        )
        if output == "404: Not Found":
            return await ctx.send(
                ":warning: This is not a hastebin or hastebin-like website."
            )
        if len(re.findall(yt_urls, output)) == 0:
            await ctx.send(
                ":warning: There are no YouTube URLs in this bin. "
                "Are you sure this is the correct site?\n**Continuing download...**"
            )
        videos = output.splitlines()
        if len(videos) > 50:
            confirm = await ctx.confirm("I found more than 50 lines in this hastebin. Continue?")
            if not confirm:
                bin_log.info("User denied bin. Cancelling...")
                return await ctx.send("Cancelled.")

        bin_log.info(f"Fetching {len(videos)} songs...")
        playlist = []
        failed_songs = 0
        for video in videos:
            try:
                song = await ytdl.Song.get_song(
                    ctx, video, loop=self.bot.loop, send_errors=False
                )
            except ytdl.YTDLError as e:
                await ctx.send(
                    f"An error occurred while processing this request: ```py {str(e)}```"
                )
            else:
                if song:

                    bin_log.info(f"Adding '{song.title}' to queue...")
                    await ctx.player.songs.put(song)
                    playlist.append(song)
                else:
                    failed_songs += 1

        em = discord.Embed(
            title="**:page_facing_up: Enqueued:**", color=discord.Color.green(),
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

        total_duration = ytdl.Song.parse_duration(total_duration)
        description += f"\nTotal duration: {total_duration}"
        if failed_songs > 0:
            description += (
                f"\n:warning: Sorry, {failed_songs} song(s) failed to download."
            )

        em.description = description
        await ctx.send(":white_check_mark: **Finished downloading songs from bin**", embed=em)

    async def fetch_yt_playlist(self, ctx, url):
        try:
            playlist, failed_songs = await ytdl.Song.get_playlist(
                ctx, url, loop=self.bot.loop
            )
        except ytdl.YTDLError as e:
            print(e)
            await ctx.send(
                f"An error occurred while processing this request: ```py {str(e)}```"
            )
        else:
            em = discord.Embed(title="**:page_facing_up: Enqueued:**", color=0xFF0000,)
            description = ""
            total_duration = 0
            for i, song in enumerate(playlist):
                if not song:
                    failed_songs += 1
                    continue

                await ctx.player.songs.put(song)
                total_duration += int(song.data.get("duration"))
                if i < 9:
                    description += (
                        f"\n• [{song.title}]({song.url}) `{song.duration}`"
                    )
                elif i == 9 and len(playlist) > 10:
                    songs_left = len(playlist) - (i + 1)
                    description += f"\n• [{song.title}]({song.url}) \
                    `{song.duration}`\n...and {songs_left} more song(s)"

            total_duration = ytdl.Song.parse_duration(total_duration)
            description += f"\nTotal duration: {total_duration}"
            if failed_songs > 0:
                description += (
                    f"\n:warning: Sorry, {failed_songs} song(s) failed to download."
                )

            em.description = description
            await ctx.send(embed=em)

    @commands.command(aliases=["pb"])
    async def playbin(self, ctx, *, url):
        if not ctx.player:
            player = self.create_player(ctx)

        if not ctx.player.voice:
            await ctx.invoke(self._join)

        """Add a list of songs from a hastebin-like site to the queue"""
        urls = "http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"
        if len(re.findall(urls, url)) == 0:
            return await ctx.send("You must provide a URL.")

        await ctx.send(
            "**:globe_with_meridians: Fetching from bin** "
            f"`{url}`\nThis make take awhile depending on amount of songs."
        )
        await self.hastebin_playlist(ctx, url)

    @commands.command(
        name="play",
        aliases=["p", "yt"],
        usage="[song]",
    )
    async def _play(self, ctx, *, search: str = None):
        """Search for a song and play it"""
        if not ctx.player:
            player = self.create_player(ctx)

        if (
            not search
            and ctx.player.is_playing
            and ctx.player.voice.is_paused()
            and ctx.author.guild_permissions.manage_guild
        ):
            ctx.player.resume()
            return await ctx.send(
                f"**:arrow_forward: Resuming** `{ctx.player.current.title}`"
            )
        if not search:
            return await ctx.send("Please specify a song to play/search for.")

        if not ctx.player.voice:
            await ctx.invoke(self._join)

        if search.startswith("<") and search.endswith(">"):
            search = search.strip("<>")

        urls = re.compile("http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+")
        if urls.match(search):
            youtube_urls = re.compile(
                "(?:https?://)?(?:www.)?(?:youtube.com|youtu.be)/(?:watch\?v=)?([^\s]+)"
            )
            if youtube_urls.match(search):
                if "list=" in search:
                    await ctx.send(
                        "**<:youtube:667536366447493120> Fetching YouTube playlist** "
                        f"`{search}`\nThis make take awhile depending on playlist size."
                    )

                    await self.fetch_yt_playlist(ctx, search)
                    return
            elif "soundcloud" in search:
                pass

        await ctx.send(f"**:mag: Searching** `{search}`")

        async with ctx.typing():
            try:
                song = await ytdl.Song.get_song(ctx, search, loop=self.bot.loop)
            except ytdl.YTDLError as e:
                print(e)
                await ctx.send(
                    f"An error occurred while processing this request: ```py {str(e)}```"
                )
            else:
                if not song:
                    return await ctx.send(
                        "Sorry. I couldn't fetch that song. Possibly being ratelimited."
                    )

                await ctx.player.songs.put(song)
                if ctx.player.is_playing:
                    await ctx.send(f"**:page_facing_up: Enqueued** {str(song)}")

    @commands.command(
        name="ytdl", description="Test YTDL to see if it works", hidden=True
    )
    @commands.is_owner()
    async def _ytdl_test(self, ctx):
        if not ctx.player:
            player = self.create_player(ctx)

        partial = functools.partial(
            ytdl.Song.ytdl.extract_info,
            "hat kid electro",
            download=False,
            process=False,
        )
        try:
            data = await self.bot.loop.run_in_executor(None, partial)
        except youtube_dl.DownloadError as e:
            print("Could not connect to YouTube")
            traceback.print_exception(type(e), e, e.__traceback__, file=sys.stderr)
            error = "".join(traceback.format_exception(type(e), e, e.__traceback__, 1))
            return await ctx.send(f"Could not connect to YouTube!```py\n{error}```")
        if not data:
            return await ctx.send("YouTube did not return any data.")
        await ctx.send("Successfully connected to YouTube with youtube_dl")

    @_join.before_invoke
    @_play.before_invoke
    async def ensure_player(self, ctx):
        if not ctx.author.voice or not ctx.author.voice.channel:
            raise commands.CommandError("You are not connected to a voice channel.")

        if ctx.voice_client:
            if ctx.voice_client.channel != ctx.author.voice.channel:
                raise commands.CommandError("Bot is already in a voice channel.")


def setup(bot):
    bot.add_cog(Music(bot))
