from datetime import datetime
from collections import Counter
import asyncio
import functools

import discord
from discord.ext import commands, tasks
import asyncpg
import humanize

from .utils.utils import get_lines_of_code
from .utils import db, colors


class Commands(db.Table):
    id = db.PrimaryKeyColumn()
    name = db.Column(db.String, index=True)
    guild_id = db.Column(db.Integer(big=True), index=True)
    channel_id = db.Column(db.Integer(big=True))
    author_id = db.Column(db.Integer(big=True), index=True)
    invoked_at = db.Column(db.Datetime, index=True)
    prefix = db.Column(db.String)
    failed = db.Column(db.Boolean, index=True)


class GuildConverter(commands.Converter):
    async def convert(self, ctx, argument):
        try:
            int_argument = int(argument)
            guild = ctx.bot.get_guild(argument)
            if guild:
                return guild

        except ValueError:
            pass

        guild = discord.utils.get(ctx.bot.guilds, name=argument)
        if not guild:
            raise commands.BadArgument("No matching guilds.")

        return guild


class Stats(commands.Cog):
    """Bot usage statistics."""

    def __init__(self, bot):
        self.bot = bot
        self.log = bot.log
        self.emoji = ":bar_chart:"
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

    @commands.group(
        description="View usage statistics for the current guild or a specified member.",
        usage="<member>",
        aliases=["statistics"],
        invoke_without_command=True,
    )
    @commands.guild_only()
    @commands.cooldown(1, 30.0, type=commands.BucketType.member)
    async def stats(self, ctx, member: discord.Member = None):
        await ctx.trigger_typing()

        places = (
            "`1.`",
            "`2.`",
            "`3.`",
            "`4.`",
            "`5.`",
        )

        if not member:
            query = """SELECT COUNT(*), MIN(invoked_at)
                    FROM commands
                    WHERE guild_id=$1;"""
            count = await ctx.db.fetchrow(query, ctx.guild.id)

            em = discord.Embed(
                title="Server Command Usage Stats",
                color=colors.PRIMARY,
                timestamp=count[1] or datetime.utcnow(),
            )

            em.description = f"There have been **{count[0]} commands used**."
            em.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon_url)
            em.set_footer(text=f"Tracking command usage since")

            query = """SELECT name,
                        COUNT(*) as "uses"
                FROM commands
                WHERE guild_id=$1
                GROUP BY name
                ORDER BY "uses" DESC
                LIMIT 5;
            """

            records = await ctx.db.fetch(query, ctx.guild.id)

            formatted = []
            for (index, (command, uses)) in enumerate(records):
                formatted.append(f"{places[index]} **{command}** ({uses} uses)")

            value = "\n".join(formatted) or "No Commands"

            em.add_field(name=":trophy: Top Commands", value=value, inline=True)

            query = """SELECT name,
                            COUNT(*) as "uses"
                    FROM commands
                    WHERE guild_id=$1
                    AND invoked_at > (CURRENT_TIMESTAMP - INTERVAL '1 day')
                    GROUP BY name
                    ORDER BY "uses" DESC
                    LIMIT 5;
                    """

            records = await ctx.db.fetch(query, ctx.guild.id)

            value = []
            for (index, (command, uses)) in enumerate(records):
                value.append(f"{places[index]} **{command}** ({uses} uses)")

            em.add_field(
                name=":clock1: Top Commands Today",
                value="\n".join(value) or "No Commands.",
                inline=True,
            )
            em.add_field(name="\u200b", value="\u200b", inline=True)

            query = """SELECT author_id,
                            COUNT(*) AS "uses"
                    FROM commands
                    WHERE guild_id=$1
                    GROUP BY author_id
                    ORDER BY "uses" DESC
                    LIMIT 5;
                    """
            records = await ctx.db.fetch(query, ctx.guild.id)

            value = []
            for (index, (author_id, uses)) in enumerate(records):
                author = ctx.guild.get_member(author_id)
                authorf = str(author) if author else f"<@!{author_id}>"
                value.append(f"{places[index]} **{authorf}** ({uses} uses)")

            em.add_field(
                name=":medal: Top Command Users",
                value="\n".join(value) or "None",
                inline=True,
            )

            query = """SELECT author_id,
                            COUNT(*) AS "uses"
                    FROM commands
                    WHERE guild_id=$1
                    AND invoked_at > (CURRENT_TIMESTAMP - INTERVAL '1 day')
                    GROUP BY author_id
                    ORDER BY "uses" DESC
                    LIMIT 5;
                    """
            records = await ctx.db.fetch(query, ctx.guild.id)

            value = []
            for (index, (author_id, uses)) in enumerate(records):
                author = ctx.guild.get_member(author_id)
                authorf = str(author) if author else f"<@!{author_id}>"
                value.append(f"{places[index]} **{authorf}** ({uses} uses)")

            em.add_field(
                name=":clock1: Top Command Users Today",
                value="\n".join(value) or "None",
                inline=True,
            )

            await ctx.send(embed=em)

        else:
            query = """SELECT COUNT(*), MIN(invoked_at)
                       FROM commands
                       WHERE author_id=$1;
                    """
            count = await ctx.db.fetchrow(query, member.id)

            em = discord.Embed(
                title=f"Member Command Usage Stats",
                description=f"Total commands used: {count[0]}",
                color=colors.PRIMARY,
                timestamp=count[1] or datetime.utcnow(),
            )

            em.set_author(name=f"{member} - {member.id}", icon_url=member.avatar_url)
            em.set_thumbnail(url=member.avatar_url)
            em.set_footer(text="First command used")

            query = """SELECT name, COUNT(*) AS "uses"
                       FROM commands
                       WHERE author_id=$1 AND guild_id = $2
                       GROUP BY name
                       ORDER BY "uses" DESC
                       LIMIT 5;
                    """
            records = await ctx.db.fetch(query, member.id, ctx.guild.id)

            value = []
            for (index, (name, uses)) in enumerate(records):
                value.append(f"{places[index]} **{name}** ({uses} uses)")

            em.add_field(
                name=":trophy: Top Command Uses",
                value="\n".join(value) or "None",
                inline=True,
            )

            query = """SELECT name, COUNT(*) AS "uses"
                       FROM commands
                       WHERE author_id=$1 AND guild_id=$2
                       AND invoked_at > (CURRENT_TIMESTAMP - INTERVAL '1 day')
                       GROUP BY name
                       ORDER BY "uses" DESC
                       LIMIT 5;
                    """
            records = await ctx.db.fetch(query, member.id, ctx.guild.id)

            value = []
            for (index, (name, uses)) in enumerate(records):
                value.append(f"{places[index]} **{name}** ({uses} uses)")

            em.add_field(
                name=":clock1: Top Command Uses Today",
                value="\n".join(value) or "None",
                inline=True,
            )

            await ctx.send(embed=em)

    @stats.command(name="global", description="Global command stats")
    @commands.is_owner()
    async def _global(self, ctx):
        query = "SELECT COUNT(*), MIN(invoked_at) FROM commands;"
        count = await ctx.db.fetchrow(query)

        em = discord.Embed(
            title="Global Command Usage Stats",
            description=f"Total commands used: **`{count[0]}`**",
            timestamp=count[1] or datetime.utcnow(),
            color=colors.PRIMARY,
        ).set_footer(text="Tracking command usage since")

        places = (
            "`1.`",
            "`2.`",
            "`3.`",
            "`4.`",
            "`5.`",
        )

        query = """SELECT name, COUNT(*) as "uses"
                   FROM commands
                   GROUP BY name
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """
        records = await ctx.db.fetch(query)

        value = []
        for i, (name, count) in enumerate(records):
            value.append(f"{places[i]} **{name}** ({count} uses)")

        em.add_field(name="Top Commands", value="\n".join(value) or "None")

        query = """SELECT guild_id, COUNT(*) as "uses"
                   FROM commands
                   GROUP BY guild_id
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """
        records = await ctx.db.fetch(query)

        value = []
        for i, (guild_id, count) in enumerate(records):
            guild = self.bot.get_guild(guild_id) or guild_id
            value.append(f"{places[i]} **{guild}** ({count} uses)")

        em.add_field(name="Top Guilds", value="\n".join(value) or "None")

        query = """SELECT author_id, COUNT(*) as "uses"
                   FROM commands
                   GROUP BY author_id
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """
        records = await ctx.db.fetch(query)

        value = []
        for i, (author_id, count) in enumerate(records):
            author = self.bot.get_user(author_id) or author_id
            value.append(f"{places[i]} **{author}** ({count} uses)")

        em.add_field(name="Top Users", value="\n".join(value) or "None")

        await ctx.send(embed=em)

    @stats.command(description="Get global stats for today")
    @commands.is_owner()
    async def today(self, ctx):
        query = """SELECT COUNT(*)
                   FROM commands
                   WHERE invoked_at > (CURRENT_TIMESTAMP - INTERVAL '1 day');
                """
        count = await ctx.db.fetchrow(query)

        em = discord.Embed(
            title="Global Command Usage Stats For Today",
            description=f"Total commands used today: **`{count[0]}`**",
            color=colors.PRIMARY,
        )

        places = (
            "`1.`",
            "`2.`",
            "`3.`",
            "`4.`",
            "`5.`",
        )

        query = """SELECT name, COUNT(*) as "uses"
                   FROM commands
                   WHERE invoked_at > (CURRENT_TIMESTAMP - INTERVAL '1 day')
                   GROUP BY name
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """
        records = await ctx.db.fetch(query)

        value = []
        for i, (name, count) in enumerate(records):
            value.append(f"{places[i]} **{name}** ({count} uses)")

        em.add_field(name="Top Commands", value="\n".join(value) or "None")

        query = """SELECT guild_id, COUNT(*) as "uses"
                   FROM commands
                   WHERE invoked_at > (CURRENT_TIMESTAMP - INTERVAL '1 day')
                   GROUP BY guild_id
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """
        records = await ctx.db.fetch(query)

        value = []
        for i, (guild_id, count) in enumerate(records):
            guild = self.bot.get_guild(guild_id) or guild_id
            value.append(f"{places[i]} **{guild}** ({count} uses)")

        em.add_field(name="Top Guilds", value="\n".join(value) or "None")

        query = """SELECT author_id, COUNT(*) as "uses"
                   FROM commands
                   WHERE invoked_at > (CURRENT_TIMESTAMP - INTERVAL '1 day')
                   GROUP BY author_id
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """
        records = await ctx.db.fetch(query)

        value = []
        for i, (author_id, count) in enumerate(records):
            author = self.bot.get_user(author_id) or author_id
            value.append(f"{places[i]} **{author}** ({count} uses)")

        em.add_field(name="Top Users", value="\n".join(value) or "None")

        await ctx.send(embed=em)

    @stats.command(name="guild", description="Get stats for a specific guild")
    @commands.is_owner()
    async def stats_guild(self, ctx, *, guild: GuildConverter):
        places = (
            "`1.`",
            "`2.`",
            "`3.`",
            "`4.`",
            "`5.`",
        )

        query = """SELECT COUNT(*), MIN(invoked_at)
                    FROM commands
                    WHERE guild_id=$1;"""
        count = await ctx.db.fetchrow(query, guild.id)

        em = discord.Embed(
            title="Guild Command Usage Stats",
            color=colors.PRIMARY,
            timestamp=count[1] or datetime.utcnow(),
        )

        em.description = f"There have been **{count[0]} commands used**."
        em.set_author(name=guild.name, icon_url=guild.icon_url)
        em.set_footer(text=f"Tracking command usage since")

        query = """SELECT name,
                    COUNT(*) as "uses"
            FROM commands
            WHERE guild_id=$1
            GROUP BY name
            ORDER BY "uses" DESC
            LIMIT 5;
        """

        records = await ctx.db.fetch(query, guild.id)

        formatted = []
        for (index, (command, uses)) in enumerate(records):
            formatted.append(f"{places[index]} **{command}** ({uses} uses)")

        value = "\n".join(formatted) or "No Commands"

        em.add_field(name=":trophy: Top Commands", value=value, inline=True)

        query = """SELECT name,
                        COUNT(*) as "uses"
                FROM commands
                WHERE guild_id=$1
                AND invoked_at > (CURRENT_TIMESTAMP - INTERVAL '1 day')
                GROUP BY name
                ORDER BY "uses" DESC
                LIMIT 5;
                """

        records = await ctx.db.fetch(query, guild.id)

        value = []
        for (index, (command, uses)) in enumerate(records):
            value.append(f"{places[index]} **{command}** ({uses} uses)")

        em.add_field(
            name=":clock1: Top Commands Today",
            value="\n".join(value) or "No Commands.",
            inline=True,
        )
        em.add_field(name="\u200b", value="\u200b", inline=True)

        query = """SELECT author_id,
                        COUNT(*) AS "uses"
                FROM commands
                WHERE guild_id=$1
                GROUP BY author_id
                ORDER BY "uses" DESC
                LIMIT 5;
                """
        records = await ctx.db.fetch(query, guild.id)

        value = []
        for (index, (author_id, uses)) in enumerate(records):
            author = guild.get_member(author_id)
            authorf = str(author) if author else f"<@!{author_id}>"
            value.append(f"{places[index]} **{authorf}** ({uses} uses)")

        em.add_field(
            name=":medal: Top Command Users",
            value="\n".join(value) or "None",
            inline=True,
        )

        query = """SELECT author_id,
                        COUNT(*) AS "uses"
                FROM commands
                WHERE guild_id=$1
                AND invoked_at > (CURRENT_TIMESTAMP - INTERVAL '1 day')
                GROUP BY author_id
                ORDER BY "uses" DESC
                LIMIT 5;
                """
        records = await ctx.db.fetch(query, guild.id)

        value = []
        for (index, (author_id, uses)) in enumerate(records):
            author = guild.get_member(author_id)
            authorf = str(author) if author else f"<@!{author_id}>"
            value.append(f"{places[index]} **{authorf}** ({uses} uses)")

        em.add_field(
            name=":clock1: Top Command Users Today",
            value="\n".join(value) or "None",
            inline=True,
        )

        await ctx.send(embed=em)

    @commands.command(
        name="about", description="Display info about the bot", aliases=["info"],
    )
    async def about(self, ctx):
        em = discord.Embed(
            title="About", color=colors.PRIMARY, timestamp=datetime.utcnow()
        )
        em.set_thumbnail(url=self.bot.user.avatar_url)

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

        partial = functools.partial(get_lines_of_code)
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
