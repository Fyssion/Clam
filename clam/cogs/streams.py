from __future__ import annotations

import datetime
import itertools
import logging
import re
import urllib.parse
from typing import TYPE_CHECKING, Any, Optional

import aiohttp
import asyncpg
import dateutil.parser
import discord
from discord.ext import commands, tasks

from clam.utils import cache, db, humantime

if TYPE_CHECKING:
    from typing_extensions import Self

    from clam.bot import Clam
    from clam.utils.context import Context, GuildContext

log = logging.getLogger("clam.streams")


class StreamsTable(db.Table, table_name="streams"):
    guild_id = db.Column(db.Integer(big=True), primary_key=True)
    channel_id = db.Column(db.Integer(big=True), nullable=False)
    role_id = db.Column(db.Integer(big=True))

    user_id = db.Column(db.String, primary_key=True)  # TODO: support youtube?
    username = db.Column(db.String, nullable=False)
    display_name = db.Column(db.String)
    current_stream_id = db.Column(db.String)

    created_at = db.Column(db.Datetime, default="now() at time zone 'utc'")


class Stream:
    guild_id: int
    channel_id: int
    role_id: Optional[int]
    user_id: str
    username: str
    display_name: Optional[str]
    current_stream_id: Optional[str]
    created_at: datetime.datetime
    guild: discord.Guild

    @classmethod
    def from_record(cls, record: asyncpg.Record, guild: discord.Guild) -> Self:
        self = cls()

        self.guild_id = record["guild_id"]
        self.channel_id = record["channel_id"]
        self.role_id = record["role_id"]
        self.user_id = record["user_id"]
        self.username = record["username"]
        self.display_name = record["display_name"]
        self.current_stream_id = record["current_stream_id"]
        self.created_at = record["created_at"]
        self.guild = guild

        return self

    @property
    def channel(self) -> discord.TextChannel:
        channel = self.guild.get_channel(self.channel_id)
        assert isinstance(channel, discord.TextChannel)
        return channel

    @property
    def role(self) -> Optional[discord.Role]:
        if not self.role_id:
            return None
        return self.guild.get_role(self.role_id)

    @property
    def url(self) -> str:
        return f"https://www.twitch.tv/{self.username}"


class CachedToken:
    token: str
    expires_at: datetime.datetime
    type: str

    def __init__(self, token: str, expires_at: datetime.datetime, type: str):
        self.token = token
        self.expires_at = expires_at
        self.type = type

    def is_invalid(self) -> bool:
        if datetime.datetime.utcnow() >= self.expires_at:
            return True
        return False

    def __str__(self) -> str:
        return f"Bearer {self.token}"


class Streams(commands.Cog):
    """Send Twitch stream notifications to your server."""

    def __init__(self, bot: Clam):
        self.bot = bot
        self.emoji = "\N{BELL}"

        self.cached_token: Optional[CachedToken] = None
        self.check_twitch_loop.start()

    def cog_unload(self):
        self.check_twitch_loop.cancel()


    async def cog_check(self, ctx: Context) -> bool:
        await commands.has_permissions(manage_messages=True).predicate(ctx)
        return True

    @cache.cache()
    async def get_streams(self, guild_id: Optional[int] = None) -> list[Stream]:
        if guild_id is not None:
            query = "SELECT * FROM streams WHERE guild_id=$1;"
            records = await self.bot.pool.fetch(query, guild_id)
        else:
            query = "SELECT * FROM streams;"
            records = await self.bot.pool.fetch(query)

        streams: list[Stream] = []

        for record in records:
            guild = self.bot.get_guild(record["guild_id"])

            if not guild:
                continue

            streams.append(Stream.from_record(record, guild))

        return streams

    @commands.group(invoke_without_command=True)
    async def stream(self, ctx: GuildContext):
        """Commands to manage stream notifications."""

        await ctx.send_help(ctx.command)

    TWITCH_URLS = re.compile(r'https?:\/\/(?:www\.)?twitch\.tv\/([^\/]+)')

    async def make_request(self, method: str, url: str, **kwargs) -> Any:
        """Makes a request to the Twitch API."""

        if not self.cached_token or self.cached_token.is_invalid():
            # authentication logic
            data = {
                "client_id": self.bot.config.twitch_client_id,
                "client_secret": self.bot.config.twitch_client_secret,
                "grant_type": "client_credentials",
            }
            async with await self.bot.session.post("https://id.twitch.tv/oauth2/token", data=data) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Failed to request a Twitch token (status code {resp.status}).")

                data = await resp.json()
                expires_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=data["expires_in"])
                self.cached_token = CachedToken(data["access_token"], expires_at, data["token_type"])

        headers = {
            "Authorization": str(self.cached_token),
            "Client-Id": self.bot.config.twitch_client_id,
        }

        if "headers" in kwargs:
            kwargs["headers"].extend(headers)
        else:
            kwargs["headers"] = headers

        async with await self.bot.session.request(method, url, **kwargs) as resp:
            if resp.status != 200:
                log.error(f"Request failed to {url}. Status code: {resp.status} Extra: {await resp.text()}")
                raise RuntimeError(f"Failed to make a request to Twitch (status code {resp.status})")

            data = await resp.json()

        return data["data"]

    @stream.command(name="add")
    async def stream_add(self, ctx: GuildContext, channel: discord.TextChannel, user: str, role: Optional[discord.Role] = None):
        """Adds a Twitch stream to watch for.

        A text channel for notifications and streamer must be provided.
        You can supply the streamer's username or a link to their stream.
        Currently only Twitch streamers are supported.
        You may optionally provide a role to be pinged when a stream is started.
        """

        url = self.TWITCH_URLS.match(user)
        if url is not None:
            user = url.group(1)

        user_quoted = urllib.parse.quote(user)
        resp = await self.make_request("GET", f"https://api.twitch.tv/helix/users?login={user_quoted}")

        if not resp:
            raise commands.BadArgument(f"Failed to find a Twitch user with username `{user}`.")

        twitch_user = resp[0]

        query = """INSERT INTO streams (guild_id, channel_id, role_id, user_id, username, display_name)
                   VALUES ($1, $2, $3, $4, $5, $6);
        """

        try:
            await ctx.db.execute(
                query,
                ctx.guild.id,
                channel.id,
                role.id if role else None,
                twitch_user["id"],
                twitch_user["login"],
                twitch_user["display_name"]
            )
        except asyncpg.UniqueViolationError:
            raise commands.BadArgument("Stream notifications are already enabled for this user.")

        self.get_streams.invalidate(self, ctx.guild.id)

        pinging = f" (pinging {role.mention})" if role else ""
        await ctx.send(
            ctx.tick(True, f"Now sending stream notifications for {twitch_user['login']} to {channel.mention}{pinging}."),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @stream.command(name="remove")
    async def stream_remove(self, ctx: GuildContext, *, user: str):
        """Removes a stream from the server.

        You must provide the username of the streamer,
        which can be found using `{prefix}stream list`.
        """

        query = "DELETE FROM streams WHERE guild_id=$1 AND username=$2 RETURNING username;"
        username = await ctx.db.fetchval(query, ctx.guild.id, user)

        if not username:
            raise commands.BadArgument("Invalid user provided.")

        self.get_streams.invalidate(self, ctx.guild.id)

        await ctx.send(ctx.tick(True, f"No longer sending stream notifications for {username}."))

    @stream.command(name="list")
    async def stream_list(self, ctx: GuildContext):
        streams = await self.get_streams(ctx.guild.id)

        if not streams:
            return await ctx.send("No streams registered.")

        streams_list = []

        for stream in streams:
            formatted = f"`{stream.username}` \N{LONG RIGHTWARDS ARROW} {stream.channel.mention}"
            if stream.role:
                formatted = f"{formatted} (pinging {stream.role.mention})"
            streams_list.append(formatted)

        final = "\n".join(streams_list)
        await ctx.send(f"Sending stream notifications for:\n{final}", allowed_mentions=discord.AllowedMentions.none())

    @tasks.loop(minutes=5)
    async def check_twitch_loop(self):
        _users = await self.get_streams()

        if not _users:
            return

        users: dict[str, list[Stream]] = {}
        for user_id, user in itertools.groupby(_users, key=lambda u: u.user_id):
            users[user_id] = list(user)

        # I'm aware that you can only request 100 IDs at once, but the likelihood of that
        # happening is very slim.
        user_ids = "&user_id=".join(users.keys())
        data = await self.make_request("GET", f"https://api.twitch.tv/helix/streams?first=100&user_id={user_ids}")

        for stream in data:
            if users[stream["user_id"]][0].current_stream_id == stream["id"]:
                continue

            for stream_channel in users[stream["user_id"]]:
                role_mention = stream_channel.role.mention if stream_channel.role else ""
                name = stream["user_name"] or stream["user_login"]
                message = f"{role_mention} {discord.utils.escape_markdown(name)} is now live!"

                started_at = dateutil.parser.isoparse(stream["started_at"])
                em = discord.Embed(title=stream_channel.url, color=0x9146FF)
                em.set_author(name=f"{name} is now streaming")
                em.add_field(name="Playing", value=stream["game_name"])
                em.add_field(name="Started Streaming", value=discord.utils.format_dt(started_at, style="R"))
                em.set_image(url=stream["thumbnail_url"].format(width=640, height=330))  # hardcoded >:)

                await stream_channel.channel.send(message, embed=em)

            query = "UPDATE streams SET current_stream_id=$1 WHERE user_id=$2;"
            await self.bot.pool.execute(query, stream["id"], stream["user_id"])

        self.get_streams.invalidate(self, None)

async def setup(bot: Clam):
    if not all((bot.config.twitch_client_id, bot.config.twitch_client_secret)):
        log.error("Missing Twitch login credentials. Streams cog will not be registered.")
        return

    await bot.add_cog(Streams(bot))
