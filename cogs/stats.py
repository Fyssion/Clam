from datetime import datetime
from collections import Counter
import asyncio
import functools

import discord
from discord.ext import commands, tasks
import asyncpg
import humanize

from .utils.utils import get_lines_of_code
from .utils import db


class Commands(db.Table):
    id = db.PrimaryKeyColumn()
    name = db.Column(db.String, index=True)
    guild_id = db.Column(db.Integer(big=True), index=True)
    channel_id = db.Column(db.Integer(big=True))
    author_id = db.Column(db.Integer(big=True), index=True)
    invoked_at = db.Column(db.Datetime, index=True)
    prefix = db.Column(db.String)
    failed = db.Column(db.Boolean, index=True)


# Command Table:
# command_name
# guild_id
# author_id
# channel_id
# invoked_at
# prefix
# failed


class Stats(commands.Cog):
    """Bot usage statistics."""

    def __init__(self, bot):
        self.bot = bot
        self.log = bot.log
        self._batch_lock = asyncio.Lock(loop=bot.loop)
        self._data_batch = []
        self.bulk_insert_loop.add_exception_type(asyncpg.PostgresConnectionError)
        self.bulk_insert_loop.start()

    async def bulk_insert(self):
        query = """INSERT INTO commands (name, guild_id, channel_id, author_id, invoked_at, prefix, failed)
                   SELECT x.name, x.guild, x.channel, x.author, x.invoked_at, x.prefix, x.failed
                   FROM jsonb_to_recordset($1::jsonb) AS
                   x(name TEXT, guild BIGINT, channel BIGINT, author BIGINT, invoked_at TIMESTAMP, prefix TEXT, failed BOOLEAN)
                """

        if self._data_batch:
            await self.bot.pool.execute(query, self._data_batch)
            total = len(self._data_batch)
            if total > 1:
                self.log.info("Registered %s commands to the database.", total)
            self._data_batch.clear()

    def cog_unload(self):
        self.bulk_insert_loop.stop()

    @tasks.loop(seconds=10.0)
    async def bulk_insert_loop(self):
        async with self._batch_lock:
            await self.bulk_insert()

    async def register_command(self, ctx):
        if ctx.command is None:
            return

        command = ctx.command.qualified_name
        self.bot.command_stats[command] += 1
        message = ctx.message
        destination = None
        if ctx.guild is None:
            destination = "Private Message"
            guild_id = None
        else:
            destination = f"#{message.channel} ({message.guild})"
            guild_id = ctx.guild.id

        self.log.info(
            f"{message.created_at}: {message.author} in {destination}: {message.content}"
        )
        async with self._batch_lock:
            self._data_batch.append(
                {
                    "name": command,
                    "guild": guild_id,
                    "channel": ctx.channel.id,
                    "author": ctx.author.id,
                    "invoked_at": message.created_at.isoformat(),
                    "prefix": ctx.prefix,
                    "failed": ctx.command_failed,
                }
            )

    @commands.command(
        name="about", description="Display info about the bot", aliases=["info"],
    )
    async def stats(self, ctx):
        em = discord.Embed(title="About", color=0xFF95B0, timestamp=datetime.utcnow())
        em.set_thumbnail(url=self.bot.user.avatar_url)
        em.set_footer(
            text=f"Requested by {str(ctx.author)}", icon_url=self.bot.user.avatar_url
        )

        dev = self.bot.get_user(224513210471022592)
        up = datetime.now() - self.bot.startup_time
        em.add_field(name=":gear: Developer", value=str(dev))
        em.add_field(name=":adult: User Count", value=len(self.bot.users))
        em.add_field(name=":family: Server Count", value=len(self.bot.guilds))
        em.add_field(
            name=":speech_balloon: Channel Count",
            value=len(list(self.bot.get_all_channels())),
        )
        em.add_field(
            name="<:online:649270802088460299> Uptime", value=humanize.naturaldelta(up),
        )

        partial = functools.partial(self.get_lines_of_code)
        lines = await self.bot.loop.run_in_executor(None, partial)
        em.add_field(name=":page_facing_up: Code", value=lines, inline=False)

        await ctx.send(embed=em)

    @commands.command(
        name="ping", description="Get the bot's latency.", aliases=["latency"]
    )
    async def ping_command(self, ctx):
        latency = (self.bot.latency) * 1000
        latency = int(latency)
        await ctx.send(f"My latency is {latency}ms.")

    @commands.command(
        name="uptime", description="Get the bot's uptime", aliases=["up"],
    )
    async def uptime(self, ctx):
        up = datetime.now() - self.bot.startup_time
        await ctx.send(
            f"<:online:649270802088460299> I booted up {humanize.naturaltime(up)}."
        )

    @commands.Cog.listener()
    async def on_command_completion(self, ctx):
        await self.register_command(ctx)


def setup(bot):
    if not hasattr(bot, "command_stats"):
        bot.command_stats = Counter()

    bot.add_cog(Stats(bot))
