from discord.ext import commands, menus, flags
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
import datetime
import importlib
import enum
import typing

from .utils import db, ytdl, music_player, colors, human_time
from .utils.emojis import GREEN_TICK, RED_TICK, LOADING
from .utils.menus import UpdatingMessage
from .utils.human_time import plural


log = logging.getLogger("clam.music")
bin_log = logging.getLogger("clam.music.bin")


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
        self.total_duration = ytdl.Song.parse_duration(total_duration)

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

        if menu.current_page == 0:
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


def is_dj(*, only_member_check=False):
    def predicate(ctx):
        author = ctx.author
        upper = discord.utils.get(ctx.guild.roles, name="DJ")
        lower = discord.utils.get(ctx.guild.roles, name="dj")

        player = ctx.cog.get_player(ctx)

        if player and player.voice and player.voice.channel:
            members = [m for m in player.voice.channel.members if not m.bot]

        else:
            members = []

        is_only_member = len(members) == 1 and ctx.author in members

        return (
            author.guild_permissions.manage_guild
            or upper in author.roles
            or lower in author.roles
            or author.id == ctx.bot.owner_id
            or only_member_check
            and is_only_member
        )

    return commands.check(predicate)


def is_listening():
    async def predicate(ctx):
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

        if await is_dj().predicate(ctx):
            return True

        if author.voice.self_deaf or author.voice.deaf:
            raise NotListeningError("You must be undeafened to use this command.")

        return True

    return commands.check(predicate)


class Music(commands.Cog):
    """Play music in a voice channel through the bot"""

    def __init__(self, bot):
        self.bot = bot
        self.emoji = "\N{MULTIPLE MUSICAL NOTES}"
        self.private = True
        self.private_user_overrides = [612816777994305566]
        self.private_guild_overrides = [
            722184677984698398,
            592510013222682669,
            704692704113721426,
            764327674649903104,
            464484469215199243,
        ]

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
        overridden_errors = (
            music_player.VoiceError,
            ytdl.YTDLError,
            NoPlayerError,
            NotListeningError,
            CannotJoinVoice,
            AlreadyActivePlayer
        )

        if isinstance(error, overridden_errors):
            await ctx.send(str(error))
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
    async def reload_music(self, ctx):
        modules = [music_player, ytdl]

        output = []

        for module in modules:
            try:
                importlib.reload(module)

            except Exception as e:
                formatted = "".join(
                    traceback.format_exception(type(e), e, e.__traceback__, 1)
                )
                output.append(
                    ctx.tick(
                        False,
                        f"Failed to reload `{module.__name__}`"
                        f"\n```py\n{formatted}\n```",
                    )
                )

            else:
                output.append(ctx.tick(True, f"Reloaded `{module.__name__}`"))

        await ctx.send("\n".join(output))

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
        """Delete all songs"""
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
            music_player.PlayerStatus.PLAYING: "🎶",
            music_player.PlayerStatus.PAUSED: "⏸️",
            music_player.PlayerStatus.WAITING: "🕐",
            music_player.PlayerStatus.CLOSED: "💤",
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

    @commands.command(
        aliases=["fdisconnect", "fdc"],
    )
    @commands.is_owner()
    async def forcedisconnect(self, ctx):
        """Force disconnect the voice client in this server"""
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

        if not before.channel and after.channel:
            await member.guild.change_voice_state(
                channel=player.voice.channel, self_deaf=True
            )

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
            try:
                await self.bot.wait_for("voice_state_update", check=check, timeout=5)

            except asyncio.TimeoutError:
                if not player.closed:
                    log.info(
                        f"{member.guild}: Bot left voice for 5 seconds, killing player."
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
            if not mem.bot and af.channel and af.channel == player.voice.channel:
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

    @commands.command(
        name="join",
        aliases=["connect"],
        invoke_without_subcommand=True,
    )
    async def join(self, ctx):
        """Joins a voice channel."""
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

        if ctx.player.voice:
            await ctx.player.voice.move_to(destination)

        else:
            ctx.player.voice = await destination.connect()
            await ctx.guild.change_voice_state(channel=destination, self_deaf=True)

        await ctx.send(
            ctx.tick(
                True,
                f"**Connected to ** {v_emote}`{destination}` and **bound to** {t_emote}`{ctx.channel}`",
            )
        )

    @commands.command(
        name="summon",
        description="Summons the bot to a voice channel. \
            If no channel was specified, it joins your channel.",
    )
    @is_dj()
    async def summon(self, ctx, *, channel: discord.VoiceChannel = None):
        if not ctx.player:
            player = self.create_player(ctx)

        if not channel and not ctx.author.voice:
            raise music_player.VoiceError(
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

        if ctx.player.voice:
            await ctx.player.voice.move_to(destination)
        else:
            ctx.player.voice = await destination.connect()
            await ctx.guild.change_voice_state(channel=destination, self_deaf=True)

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

    @commands.command(
        name="leave",
        aliases=["disconnect"],
    )
    @is_dj()
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

    @commands.command(name="volume")
    @is_dj(only_member_check=True)
    async def volume(self, ctx, *, volume: int = None):
        """Sets the volume of the player. Must be between 1 and 100."""
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
    async def now(self, ctx):
        """Displays the currently playing song."""
        if not ctx.player.is_playing:
            return await ctx.send("Not currently playing a song.")

        if ctx.player.voice.is_paused():
            em = ctx.player.now_playing_embed(
                "Currently Paused", ctx.player.duration.get_time()
            )

        else:
            em = ctx.player.now_playing_embed(duration=ctx.player.duration.get_time())

        await ctx.send(embed=em)

    @commands.command(name="pause")
    @is_dj()
    async def pause(self, ctx):
        """Pauses the currently playing song."""
        if ctx.player.is_playing and ctx.player.voice.is_playing():
            ctx.player.pause()
            song = ctx.player.current.title
            await ctx.send(f"**:pause_button: Paused** `{song}`")

        else:
            await ctx.send("Not currently playing.")

    @commands.command(
        name="resume",
        aliases=["unpause"],
    )
    @is_dj()
    async def resume(self, ctx):
        """Resumes a currently paused song."""
        if ctx.player.is_playing and ctx.player.voice.is_paused():
            ctx.player.resume()
            song = ctx.player.current.title
            await ctx.send(f"**:arrow_forward: Resuming** `{song}`")

        else:
            await ctx.send("Not currently paused.")

    @commands.command(name="stop")
    @is_dj()
    async def stop(self, ctx):
        """Stops playing song and clears the queue."""
        if ctx.player.is_playing:
            ctx.player.voice.stop()

        ctx.player.songs.clear()
        ctx.player.loop = False
        ctx.player.loop_queue = False

        await ctx.send("**\N{BLACK SQUARE FOR STOP} Song stopped and queue cleared.**")

    @commands.command(
        name="skip",
        aliases=["next", "s"],
    )
    @is_listening()
    async def skip(self, ctx):
        """Vote to skip a song. The requester can automatically skip."""

        async def skip_song(total, required):
            await ctx.message.add_reaction("⏭")

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

    @commands.command(usage="[position]")
    async def skipto(self, ctx, *, position: int):
        """Skip to a song in the queue"""
        if len(ctx.player.songs) < position:
            raise commands.BadArgument(f"The queue has less than {position} song(s).")

        async def skipto_song(total, required):
            song = ctx.player.songs[position - 1]

            for i in range(position - 1):
                current = await ctx.player.songs.get()

                if ctx.player.loop_queue:
                    await ctx.player.songs.put(current)

            ctx.player.skip()

            votes = (
                f"Required votes met `({total}/{required})`.\n" if required != 1 else ""
            )
            await ctx.send(f"{votes}**⏩ Skipped to** `{song}`")

        await self.votes(ctx, "skipto", skipto_song)

    @commands.group(
        name="queue",
        aliases=["playlist"],
        invoke_without_command=True,
    )
    async def queue(self, ctx):
        """View the player's queue"""
        pages = menus.MenuPages(
            source=QueuePages(ctx.player),
            clear_reactions_after=True,
        )
        return await pages.start(ctx)

    @queue.command(
        name="save", description="Save the queue to a bin", aliases=["upload"]
    )
    @commands.cooldown(1, 10)
    async def queue_save(self, ctx):
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
    async def queue_clear(self, ctx):
        """Clears the queue"""
        ctx.player.songs.clear()

        await ctx.send("**\N{WASTEBASKET} Cleared queue**")

    @commands.command(name="shuffle")
    @is_listening()
    async def shuffle(self, ctx):
        """Shuffles the queue"""

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

    @queue.command(
        name="remove",
        description="Removes a song from the queue at a given index.",
        usage="[song #]",
        aliases=["delete"],
    )
    @is_listening()
    async def queue_remove(self, ctx, index: int):
        async def remove_song(total, required):
            if required != 1:
                votes_msg = f"Required votes met `({total}/{required})`. "

            else:
                votes_msg = ""

            to_be_removed = ctx.player.songs[index - 1].title
            ctx.player.songs.remove(index - 1)
            await ctx.send(f"{votes_msg}**\N{WASTEBASKET} Removed** `{to_be_removed}`")

        if len(ctx.player.songs) == 0:
            return await ctx.send("Queue is empty.")

        if index > len(ctx.player.songs):
            length = len(ctx.player.songs)
            raise commands.BadArgument(
                f"There is no song at position {index}. Queue length is only {length}."
            )

        await self.votes(ctx, "remove", remove_song)

    @commands.command()
    async def notify(self, ctx):
        """Enable or disable now playing notifications"""
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
    async def loop(self, ctx):
        """Loop a single song. To loop the queue use loop queue"""
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

    @loop.command(
        name="queue", description="Loop the entire queue.", aliases=["playlist"]
    )
    async def loop_queue(self, ctx):
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

    @commands.command(description="Start the current song over from the beginning")
    async def startover(self, ctx):
        if not ctx.player.is_playing:
            return await ctx.send("Nothing is being played at the moment.")

        async def startover_song(total, required):
            current = ctx.player.current

            song = ytdl.Song(
                ctx,
                data=current.data,
                filename=current.filename,
            )

            ctx.player.startover = True

            if not ctx.player.loop and not ctx.player.loop_queue:
                ctx.player.songs._queue.appendleft(song)

            ctx.player.skip()

            votes = (
                f"Required votes met `({total}/{required})`.\n" if required != 1 else ""
            )
            await ctx.send(f"{votes}**⏪ Starting song over**")

        await self.votes(ctx, "startover", startover_song)

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
            playlist, failed_songs = await ytdl.Song.get_playlist(
                ctx, url, progress_message, loop=self.bot.loop
            )

        except ytdl.YTDLError as e:
            print(e)
            await ctx.send(
                f"An error occurred while processing this request: ```py\n{str(e)}\n```"
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

            total_duration = ytdl.Song.parse_duration(total_duration)
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
                        song = await ytdl.Song.get_song_from_db(
                            ctx, query, loop=self.bot.loop
                        )
                    else:
                        song = await ytdl.Song.get_song(
                            ctx, query, loop=self.bot.loop, skip_resolve=skip_resolve
                        )

            except ytdl.YTDLError as e:
                print(e)
                await ctx.send(
                    f"An error occurred while processing this request: ```py {str(e)}```"
                )

            except asyncio.TimeoutError:
                await ctx.send("Timed out while fetching song. Sorry.")

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

        total_duration = ytdl.Song.parse_duration(total_duration)
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
    async def playbin(self, ctx, *, url):
        """Load songs from a pastebin"""
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

    @commands.command(
        name="play",
        aliases=["p", "yt"],
        usage="[song]",
    )
    async def play(self, ctx, *, search=None):
        """Play a song

        You can specify where to search for the song with `source: search`
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
        if not ctx.player:
            player = self.create_player(ctx)
            ctx.player = player

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

        query, location_type = self.parse_search(search)

        await self.play_song(ctx, location_type, query)

    @commands.command()
    async def search(self, ctx, limit: typing.Optional[int], *, search):
        """Search Youtube or Soundcloud and select a song to play

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
            song = await ytdl.Song.search_ytdl(ctx, query)

        except ytdl.YTDLError as e:
            print(e)
            await ctx.send(
                f"An error occurred while processing this request: ```py {str(e)}```"
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
                dj = await is_dj().predicate(ctx)
                hint = (
                    f" Use `{ctx.prefix}summon` to summon the bot to a channel."
                    if dj
                    else ""
                )
                raise NotListeningError(f"Bot is in another voice channel.{hint}")

    # music db management commands

    @commands.group(aliases=["mdb"], invoke_without_command=True)
    @commands.is_owner()
    async def musicdb(self, ctx):
        """Commands to manage the music db"""
        query = "SELECT COUNT(*), SUM(plays) FROM songs;"
        count, plays = await ctx.db.fetchrow(query)

        await ctx.send(
            f"Music database contains **{count} songs** with a total of **{plays} plays**."
        )

    @musicdb.command(name="list", aliases=["all"])
    @commands.is_owner()
    async def musicdb_list(self, ctx):
        """List all songs in the database"""
        query = "SELECT id, title, plays, last_updated FROM songs;"
        records = await ctx.db.fetch(query)

        songs = []
        for song_id, title, plays, last_updated in records:
            formatted = human_time.human_timedelta(last_updated, brief=True, accuracy=1)
            songs.append(
                f"{title} # ID: {song_id} ({plays } plays) last updated {formatted}"
            )

        pages = ctx.pages(songs, per_page=10, title="Music Database")
        await pages.start(ctx)

    @musicdb.command(name="search", aliases=["find"])
    @commands.is_owner()
    async def musicdb_search(self, ctx, *, song):
        """Search the database for songs"""
        query = """SELECT id, title, plays, last_updated
                   FROM songs
                   ORDER BY similarity(title, $1) DESC
                   LIMIT 10;
                """

        records = await ctx.db.fetch(query, song)

        songs = []
        for song_id, title, plays, last_updated in records:
            formatted = human_time.human_timedelta(last_updated, brief=True, accuracy=1)
            songs.append(
                f"{title} # ID: {song_id} ({plays } plays) last updated {formatted}"
            )

        pages = ctx.pages(songs, per_page=10, title=f"Results for '{song}'")
        await pages.start(ctx)

    @flags.add_flag("--delete-file", action="store_true")
    @musicdb.command(name="delete", aliases=["remove"], cls=flags.FlagCommand)
    @commands.is_owner()
    async def musicdb_delete(self, ctx, song_id: int, **flags):
        """Delete a song from the database"""
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
            ytdl.Song.ytdl.extract_info, old_info["webpage_url"], download=False
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
        """Refetch information about a song"""
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
        """View stats about the database"""
        await ctx.trigger_typing()

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

        em.description = f"Music database contains **{count[0]} songs** with a total of **{count[1]} plays**."
        em.set_footer(text=f"First song registered")

        query = """SELECT title, plays
            FROM songs
            ORDER BY plays DESC
            LIMIT 5;
        """

        records = await ctx.db.fetch(query)

        formatted = []
        for (i, (title, plays)) in enumerate(records):
            formatted.append(f"{places[i]} **{title}** ({plays} plays)")

        value = "\n".join(formatted) or "None"

        em.add_field(name=":trophy: Top Songs", value=value, inline=True)

        await ctx.send(embed=em)


def setup(bot):
    bot.add_cog(Music(bot))
