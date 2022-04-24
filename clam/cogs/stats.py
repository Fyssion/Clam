import asyncio
import datetime
import functools
import io
import json
import logging
import os
import pkg_resources
import typing
from collections import Counter, defaultdict

import asyncpg
import discord
import git
import psutil
from discord.ext import commands, tasks, flags
from jishaku.features.root_command import natural_size

from clam.utils import colors, db, humantime
from clam.utils.emojis import VOICE_CHANNEL, TEXT_CHANNEL
from clam.utils.flags import NoUsageFlagCommand
from clam.utils.formats import plural, TabularData
from clam.utils.utils import get_lines_of_code


log = logging.getLogger("clam.stats")


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
            guild = ctx.bot.get_guild(int_argument)
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
        self.emoji = "\N{BAR CHART}"
        self._batch_lock = asyncio.Lock(loop=bot.loop)
        self._data_batch = []
        self.bulk_insert_loop.add_exception_type(asyncpg.PostgresConnectionError)
        self.bulk_insert_loop.start()

        if not hasattr(bot, "command_stats"):
            self.bot.command_stats = Counter()

        if not hasattr(bot, "socket_stats"):
            self.bot.socket_stats = Counter()

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
                log.info("Registered %s commands to the database.", total)
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

        if ctx.interaction is None:
            command_content = message.content
        else:
            args = " ".join((f"{k}: {v}" for k, v in ctx.interaction.namespace))
            command_content = f"{ctx.prefix}{command} {args}"

        log.info(
            f"{message.created_at}: {message.author} in {destination}: {command_content}"
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

    @commands.group(aliases=["statistics"], invoke_without_command=True)
    @commands.guild_only()
    @commands.cooldown(1, 30.0, type=commands.BucketType.member)
    async def stats(self, ctx, *, member: discord.Member = None):
        """Shows bot usage stats for the server or a member."""

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
                timestamp=count[1] or datetime.datetime.utcnow(),
            )

            em.description = f"There have been **{plural(count[0], pretty=True):command} used**."
            icon = ctx.guild.icon.url if ctx.guild.icon else None
            em.set_author(name=ctx.guild.name, icon_url=icon)
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
                formatted.append(f"{places[index]} **{command}** ({plural(uses, pretty=True):use})")

            value = "\n".join(formatted) or "None"

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
                value.append(f"{places[index]} **{command}** ({plural(uses, pretty=True):use})")

            em.add_field(
                name=":clock1: Top Commands Today",
                value="\n".join(value) or "None",
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
                value.append(f"{places[index]} **{authorf}** ({plural(uses, pretty=True):use})")

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
                value.append(f"{places[index]} **{authorf}** ({plural(uses, pretty=True):use})")

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
                description=f"There have been **{plural(count[0], pretty=True):command} used**.",
                color=colors.PRIMARY,
                timestamp=count[1] or datetime.datetime.utcnow(),
            )

            em.set_author(name=f"{member} - {member.id}", icon_url=member.display_avatar.url)
            em.set_thumbnail(url=member.display_avatar.url)
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
                value.append(f"{places[index]} **{name}** ({plural(uses, pretty=True):use})")

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
                value.append(f"{places[index]} **{name}** ({plural(uses, pretty=True):use})")

            em.add_field(
                name=":clock1: Top Command Uses Today",
                value="\n".join(value) or "None",
                inline=True,
            )

            await ctx.send(embed=em)

    @stats.command(name="global")
    @commands.cooldown(1, 30.0, type=commands.BucketType.member)
    async def stats_global(self, ctx):
        """Shows global command usage stats."""

        query = "SELECT COUNT(*), MIN(invoked_at) FROM commands;"
        count = await ctx.db.fetchrow(query)

        em = discord.Embed(
            title="Global Command Usage Stats",
            description=f"There have been **{plural(count[0], pretty=True):command} used**.",
            timestamp=count[1] or datetime.datetime.utcnow(),
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
            value.append(f"{places[i]} **{name}** ({plural(count, pretty=True):use})")

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
            value.append(f"{places[i]} **{guild}** ({plural(count, pretty=True):use})")

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
            value.append(f"{places[i]} **{author}** ({plural(count, pretty=True):use})")

        em.add_field(name="Top Users", value="\n".join(value) or "None")

        await ctx.send(embed=em)

    @stats.command()
    @commands.cooldown(1, 30.0, type=commands.BucketType.member)
    async def today(self, ctx):
        """Shows today's global command usage stats."""

        query = """SELECT COUNT(*)
                   FROM commands
                   WHERE invoked_at > (CURRENT_TIMESTAMP - INTERVAL '1 day');
                """
        count = await ctx.db.fetchrow(query)

        em = discord.Embed(
            title="Global Command Usage Stats For Today",
            description=f"There have been **{plural(count[0], pretty=True):command} used today**.",
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
            value.append(f"{places[i]} **{name}** ({plural(count, pretty=True):use})")

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
            value.append(f"{places[i]} **{guild}** ({plural(count, pretty=True):use})")

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
            value.append(f"{places[i]} **{author}** ({plural(count, pretty=True):use})")

        em.add_field(name="Top Users", value="\n".join(value) or "None")

        await ctx.send(embed=em)

    @stats.command(name="guild")
    @commands.is_owner()
    async def stats_guild(self, ctx, *, guild: GuildConverter):
        """Shows command usage stats for a specific guild."""

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
            timestamp=count[1] or datetime.datetime.utcnow(),
        )

        em.description = f"There have been **{plural(count[0], pretty=True):command} used**."
        icon = guild.icon.url if guild.icon else None
        em.set_author(name=guild.name, icon_url=icon)
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
            formatted.append(f"{places[index]} **{command}** ({plural(uses, pretty=True):use})")

        value = "\n".join(formatted) or "None"

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
            value.append(f"{places[index]} **{command}** ({plural(uses, pretty=True):use})")

        em.add_field(
            name=":clock1: Top Commands Today",
            value="\n".join(value) or "None",
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
            value.append(f"{places[index]} **{authorf}** ({plural(uses, pretty=True):use})")

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
            value.append(f"{places[index]} **{authorf}** ({plural(uses, pretty=True):use})")

        em.add_field(
            name=":clock1: Top Command Users Today",
            value="\n".join(value) or "None",
            inline=True,
        )

        await ctx.send(embed=em)

    @stats.command(name="user")
    @commands.is_owner()
    async def stats_user(self, ctx, *, user: discord.User):
        """Shows command usage stats for a specific user."""

        places = (
            "`1.`",
            "`2.`",
            "`3.`",
            "`4.`",
            "`5.`",
        )

        query = """SELECT COUNT(*), MIN(invoked_at)
                    FROM commands
                    WHERE author_id=$1;"""
        count = await ctx.db.fetchrow(query, user.id)

        em = discord.Embed(
            title="User Command Usage Stats",
            color=colors.PRIMARY,
            timestamp=count[1] or datetime.datetime.utcnow(),
        )
        em.set_author(name=str(user), icon_url=user.display_avatar.url)

        em.description = f"{user} has used **{plural(count[0], pretty=True):command}**."
        em.set_footer(text="Tracking command usage since")

        query = """SELECT name,
                    COUNT(*) as "uses"
            FROM commands
            WHERE author_id=$1
            GROUP BY name
            ORDER BY "uses" DESC
            LIMIT 5;
        """

        records = await ctx.db.fetch(query, user.id)

        formatted = []
        for (index, (command, uses)) in enumerate(records):
            formatted.append(f"{places[index]} **{command}** ({plural(uses, pretty=True):use})")

        value = "\n".join(formatted) or "None"

        em.add_field(name=":trophy: Top Commands", value=value, inline=True)

        query = """SELECT name,
                        COUNT(*) as "uses"
                FROM commands
                WHERE author_id=$1
                AND invoked_at > (CURRENT_TIMESTAMP - INTERVAL '1 day')
                GROUP BY name
                ORDER BY "uses" DESC
                LIMIT 5;
                """

        records = await ctx.db.fetch(query, user.id)

        value = []
        for (index, (command, uses)) in enumerate(records):
            value.append(f"{places[index]} **{command}** ({plural(uses, pretty=True):use})")

        em.add_field(
            name=":clock1: Top Commands Today",
            value="\n".join(value) or "None",
            inline=True,
        )
        em.add_field(name="\u200b", value="\u200b", inline=True)

        query = """SELECT guild_id,
                        COUNT(*) AS "uses"
                FROM commands
                WHERE author_id=$1
                GROUP BY guild_id
                ORDER BY "uses" DESC
                LIMIT 5;
                """
        records = await ctx.db.fetch(query, user.id)

        value = []
        for (index, (guild_id, uses)) in enumerate(records):
            if not guild_id:
                formatted = "None (DMs)"
            else:
                guild = self.bot.get_guild(guild_id)
                formatted = str(guild) if guild else f"Unknown guild with ID {guild_id}"
            value.append(f"{places[index]} **{formatted}** ({plural(uses, pretty=True):use})")

        em.add_field(
            name=f":medal: {user} Top Guilds",
            value="\n".join(value) or "None",
            inline=True,
        )

        query = """SELECT guild_id,
                        COUNT(*) AS "uses"
                FROM commands
                WHERE author_id=$1
                AND invoked_at > (CURRENT_TIMESTAMP - INTERVAL '1 day')
                GROUP BY guild_id
                ORDER BY "uses" DESC
                LIMIT 5;
                """
        records = await ctx.db.fetch(query, user.id)

        value = []
        for (index, (guild_id, uses)) in enumerate(records):
            if not guild_id:
                formatted = "None (DMs)"
            else:
                guild = self.bot.get_guild(guild_id)
                formatted = str(guild) if guild else f"Unknown guild with ID {guild_id}"
            value.append(f"{places[index]} **{formatted}** ({plural(uses, pretty=True):use})")

        em.add_field(
            name=f":clock1: {user} Top Guilds Today",
            value="\n".join(value) or "None",
            inline=True,
        )

        await ctx.send(embed=em)

    @stats.command(name="command")
    @commands.is_owner()
    async def stats_command(self, ctx, *, command=None):
        """Shows command usage stats for a specific command."""

        places = (
            "`1.`",
            "`2.`",
            "`3.`",
            "`4.`",
            "`5.`",
        )

        query = """SELECT COUNT(*), MIN(invoked_at)
                    FROM commands
                    WHERE name=$1;"""
        count = await ctx.db.fetchrow(query, command)

        em = discord.Embed(
            title=f"`{command}` Command Usage Stats",
            color=colors.PRIMARY,
            timestamp=count[1] or datetime.datetime.utcnow(),
        )

        em.description = f"`{command}` has **{plural(count[0], pretty=True):use}**."
        em.set_footer(text="Tracking command usage since")

        query = """SELECT guild_id,
                        COUNT(*) AS "uses"
                FROM commands
                WHERE name=$1
                GROUP BY guild_id
                ORDER BY "uses" DESC
                LIMIT 5;
                """
        records = await ctx.db.fetch(query, command)

        value = []
        for (index, (guild_id, uses)) in enumerate(records):
            if not guild_id:
                formatted = "None (DMs)"
            else:
                guild = self.bot.get_guild(guild_id)
                formatted = str(guild) if guild else f"Unknown guild with ID {guild_id}"
            value.append(f"{places[index]} **{formatted}** ({plural(uses, pretty=True):use})")

        em.add_field(
            name=":medal: Top Guilds",
            value="\n".join(value) or "None",
            inline=True,
        )

        query = """SELECT guild_id,
                        COUNT(*) AS "uses"
                FROM commands
                WHERE name=$1
                AND invoked_at > (CURRENT_TIMESTAMP - INTERVAL '1 day')
                GROUP BY guild_id
                ORDER BY "uses" DESC
                LIMIT 5;
                """
        records = await ctx.db.fetch(query, command)

        value = []
        for (index, (guild_id, uses)) in enumerate(records):
            if not guild_id:
                formatted = "None (DMs)"
            else:
                guild = self.bot.get_guild(guild_id)
                formatted = str(guild) if guild else f"Unknown guild with ID {guild_id}"
            value.append(f"{places[index]} **{formatted}** ({plural(uses, pretty=True):use})")

        em.add_field(
            name=":clock1: Top Guilds Today",
            value="\n".join(value) or "None",
            inline=True,
        )
        em.add_field(name="\u200b", value="\u200b", inline=True)

        query = """SELECT author_id,
                        COUNT(*) AS "uses"
                FROM commands
                WHERE name=$1
                GROUP BY author_id
                ORDER BY "uses" DESC
                LIMIT 5;
                """
        records = await ctx.db.fetch(query, command)

        value = []
        for (index, (author_id, uses)) in enumerate(records):
            author = self.bot.get_user(author_id)
            authorf = str(author) if author else f"<@!{author_id}>"
            value.append(f"{places[index]} **{authorf}** ({plural(uses, pretty=True):use})")

        em.add_field(
            name=":medal: Top Command Users",
            value="\n".join(value) or "None",
            inline=True,
        )

        query = """SELECT author_id,
                        COUNT(*) AS "uses"
                FROM commands
                WHERE name=$1
                AND invoked_at > (CURRENT_TIMESTAMP - INTERVAL '1 day')
                GROUP BY author_id
                ORDER BY "uses" DESC
                LIMIT 5;
                """
        records = await ctx.db.fetch(query, command)

        value = []
        for (index, (author_id, uses)) in enumerate(records):
            author = self.bot.get_user(author_id)
            authorf = str(author) if author else f"<@!{author_id}>"
            value.append(f"{places[index]} **{authorf}** ({plural(uses, pretty=True):use})")

        em.add_field(
            name=":clock1: Top Command Users Today",
            value="\n".join(value) or "None",
            inline=True,
        )

        await ctx.send(embed=em)

    def format_commit(self, commit):
        short = commit.summary
        short_sha2 = commit.name_rev[0:6]

        # [`hash`](url) message (offset)
        offset = humantime.timedelta(
            commit.committed_datetime.astimezone(datetime.timezone.utc).replace(tzinfo=None),
            accuracy=1,
        )
        commit_hex = commit.name_rev.split()[0]
        return f"[`{short_sha2}`](https://github.com/Fyssion/Clam/commit/{commit_hex}) {short} ({offset})"

    def get_latest_commits(self, count=3):
        repo = git.Repo(".")
        commits = list(list(repo.iter_commits("main", max_count=count)))
        return "\n".join(self.format_commit(c) for c in commits)

    @commands.command(aliases=["info"])
    async def about(self, ctx):
        """Shows info about the bot."""

        revisions = self.get_latest_commits()
        em = discord.Embed(
            title="About",
            description=f"Latest changes:\n{revisions}",
            color=colors.PRIMARY,
        )

        version = pkg_resources.get_distribution("discord.py").version
        em.set_footer(
            text=f"Made with \N{HEAVY BLACK HEART} using discord.py v{version}"
        )

        em.set_thumbnail(url=self.bot.user.display_avatar.url)

        dev = self.bot.get_user(224513210471022592)
        em.add_field(name=":gear: Creator", value=str(dev))
        em.add_field(name=":adult: User Count", value=f"{len(self.bot.users):,}")
        em.add_field(name=":family: Server Count", value=f"{len(self.bot.guilds):,}")

        channels = 0
        text_channels = 0
        voice_channels = 0

        for channel in self.bot.get_all_channels():
            channels += 1

            if isinstance(channel, discord.TextChannel):
                text_channels += 1

            elif isinstance(channel, discord.VoiceChannel):
                voice_channels += 1

        em.add_field(
            name=":speech_balloon: Channel Count",
            value=f"{channels} ({TEXT_CHANNEL}{text_channels} {VOICE_CHANNEL}{voice_channels})",
        )

        em.add_field(
            name="<:online:649270802088460299> Uptime",
            value=humantime.timedelta(self.bot.uptime, brief=True, discord_fmt=False, suffix=False).capitalize(),
        )
        cpu = psutil.cpu_percent()

        proc = psutil.Process()
        mem = proc.memory_full_info()
        used = natural_size(mem.uss)
        em.add_field(name="Process", value=f"{cpu}% CPU\n{used} memory")

        partial = functools.partial(get_lines_of_code)
        lines = await self.bot.loop.run_in_executor(None, partial)
        em.add_field(name=":page_facing_up: Code", value=lines, inline=False)

        await ctx.send(embed=em)

    @commands.command(aliases=["changes", "latest", "news"])
    async def changelog(self, ctx):
        """Shows the bot's latest changes."""

        async with ctx.typing():
            revisions = self.get_latest_commits(10)

        async with ctx.typing():
            repo = git.Repo(".")
            count = int(repo.git.rev_list("--count", "HEAD"))

        description = (
            f"Each change is a [git commit.](https://git-scm.com/docs/git-commit)\n"
            f"{revisions}\n"
            f"[...view all {count:,} commits](https://github.com/Fyssion/Clam/commits/main)"
        )

        em = discord.Embed(
            title="Latest changes",
            description=description,
            color=colors.PRIMARY,
        )
        await ctx.send(embed=em)

    @commands.command(name="ping", aliases=["latency"])
    async def ping_command(self, ctx):
        """Shows the bot's gateway latency."""

        latency = (self.bot.latency) * 1000
        latency = int(latency)
        await ctx.send(f"My latency is {latency}ms.")

    @commands.command(aliases=["up"])
    async def uptime(self, ctx):
        """Shows the bot's uptime."""

        uptime = humantime.timedelta(
            self.bot.uptime, source=ctx.message.created_at, discord_fmt=False
        )
        await ctx.send(f"<:online:649270802088460299> I booted up **{uptime}**")

    @commands.Cog.listener()
    async def on_command_completion(self, ctx):
        await self.register_command(ctx)

    # https://github.com/Rapptz/RoboDanny/blob/7a7e75dfaee2057aefc3ff5f4cf23b1fc43afe70/cogs/stats.py#L838-L1047
    async def tabulate_query(self, ctx, query, *args):
        records = await ctx.db.fetch(query, *args)

        if len(records) == 0:
            return await ctx.send("No results found.")

        headers = list(records[0].keys())
        table = TabularData()
        table.set_columns(headers)
        table.add_rows(list(r.values()) for r in records)
        render = table.render()

        fmt = f"```\n{render}\n```"
        if len(fmt) > 2000:
            fp = io.BytesIO(fmt.encode("utf-8"))
            await ctx.send("Too many results...", file=discord.File(fp, "results.txt"))
        else:
            await ctx.send(fmt)

    @commands.group(hidden=True, invoke_without_command=True)
    @commands.is_owner()
    async def command_history(self, ctx):
        """Shows command history."""

        query = """SELECT
                        CASE failed
                            WHEN TRUE THEN name || ' [!]'
                            ELSE name
                        END AS "command",
                        to_char(invoked_at, 'Mon DD HH12:MI:SS AM') AS "invoked",
                        author_id,
                        guild_id
                   FROM commands
                   ORDER BY invoked_at DESC
                   LIMIT 15;
                """
        await self.tabulate_query(ctx, query)

    @command_history.command(name="for")
    @commands.is_owner()
    async def command_history_for(
        self, ctx, days: typing.Optional[int] = 7, *, command: str
    ):
        """Shows command history for a command."""

        query = """SELECT *, t.success + t.failed AS "total"
                   FROM (
                       SELECT guild_id,
                              SUM(CASE WHEN failed THEN 0 ELSE 1 END) AS "success",
                              SUM(CASE WHEN failed THEN 1 ELSE 0 END) AS "failed"
                       FROM commands
                       WHERE name=$1
                       AND invoked_at > (CURRENT_TIMESTAMP - $2::interval)
                       GROUP BY guild_id
                   ) AS t
                   ORDER BY "total" DESC
                   LIMIT 30;
                """

        await self.tabulate_query(ctx, query, command, datetime.timedelta(days=days))

    @command_history.command(name="guild", aliases=["server"])
    @commands.is_owner()
    async def command_history_guild(self, ctx, guild_id: int):
        """Shows command history for a guild."""

        query = """SELECT
                        CASE failed
                            WHEN TRUE THEN name || ' [!]'
                            ELSE name
                        END AS "name",
                        channel_id,
                        author_id,
                        invoked_at
                   FROM commands
                   WHERE guild_id=$1
                   ORDER BY invoked_at DESC
                   LIMIT 15;
                """
        await self.tabulate_query(ctx, query, guild_id)

    @command_history.command(name="user", aliases=["member"])
    @commands.is_owner()
    async def command_history_user(self, ctx, user_id: int):
        """Shows command history for a user."""

        query = """SELECT
                        CASE failed
                            WHEN TRUE THEN name || ' [!]'
                            ELSE name
                        END AS "name",
                        guild_id,
                        invoked_at
                   FROM commands
                   WHERE author_id=$1
                   ORDER BY invoked_at DESC
                   LIMIT 20;
                """
        await self.tabulate_query(ctx, query, user_id)

    @command_history.command(name="log")
    @commands.is_owner()
    async def command_history_log(self, ctx, days=7):
        """Shows the command history log for the last N days."""

        query = """SELECT name, COUNT(*)
                   FROM commands
                   WHERE invoked_at > (CURRENT_TIMESTAMP - $1::interval)
                   GROUP BY name
                   ORDER BY 2 DESC
                """

        all_commands = {c.qualified_name: 0 for c in self.bot.walk_commands()}

        records = await ctx.db.fetch(query, datetime.timedelta(days=days))
        for name, uses in records:
            if name in all_commands:
                all_commands[name] = uses

        as_data = sorted(all_commands.items(), key=lambda t: t[1], reverse=True)
        table = TabularData()
        table.set_columns(["Command", "Uses"])
        table.add_rows(tup for tup in as_data)
        render = table.render()

        embed = discord.Embed(title="Summary", colour=discord.Colour.green())
        embed.set_footer(text="Since").timestamp = datetime.datetime.utcnow() - datetime.timedelta(
            days=days
        )

        top_ten = "\n".join(f"{command}: {uses}" for command, uses in records[:10])
        bottom_ten = "\n".join(f"{command}: {uses}" for command, uses in records[-10:])
        embed.add_field(name="Top 10", value=top_ten)
        embed.add_field(name="Bottom 10", value=bottom_ten)

        unused = ", ".join(name for name, uses in as_data if uses == 0)
        if len(unused) > 1024:
            unused = "Way too many..."

        embed.add_field(name="Unused", value=unused, inline=False)

        await ctx.send(
            embed=embed,
            file=discord.File(io.BytesIO(render.encode()), filename="full_results.txt"),
        )

    @command_history.command(name="cog")
    @commands.is_owner()
    async def command_history_cog(
        self, ctx, days: typing.Optional[int] = 7, *, cog: str = None
    ):
        """Shows command history for a cog or grouped by a cog."""

        interval = datetime.timedelta(days=days)
        if cog is not None:
            cog = self.bot.get_cog(cog)
            if cog is None:
                return await ctx.send(f"Unknown cog: {cog}")

            query = """SELECT *, t.success + t.failed AS "total"
                       FROM (
                           SELECT name,
                                  SUM(CASE WHEN failed THEN 0 ELSE 1 END) AS "success",
                                  SUM(CASE WHEN failed THEN 1 ELSE 0 END) AS "failed"
                           FROM commands
                           WHERE name = any($1::text[])
                           AND invoked_at > (CURRENT_TIMESTAMP - $2::interval)
                           GROUP BY name
                       ) AS t
                       ORDER BY "total" DESC
                       LIMIT 30;
                    """
            return await self.tabulate_query(
                ctx, query, [c.qualified_name for c in cog.walk_commands()], interval
            )

        # A more manual query with a manual grouper.
        query = """SELECT *, t.success + t.failed AS "total"
                   FROM (
                       SELECT name,
                              SUM(CASE WHEN failed THEN 0 ELSE 1 END) AS "success",
                              SUM(CASE WHEN failed THEN 1 ELSE 0 END) AS "failed"
                       FROM commands
                       WHERE invoked_a t> (CURRENT_TIMESTAMP - $1::interval)
                       GROUP BY name
                   ) AS t;
                """

        class Count:
            __slots__ = ("success", "failed", "total")

            def __init__(self):
                self.success = 0
                self.failed = 0
                self.total = 0

            def add(self, record):
                self.success += record["success"]
                self.failed += record["failed"]
                self.total += record["total"]

        data = defaultdict(Count)
        records = await ctx.db.fetch(query, interval)
        for record in records:
            command = self.bot.get_command(record["name"])
            if command is None or command.cog is None:
                data["No Cog"].add(record)
            else:
                data[command.cog.qualified_name].add(record)

        table = TabularData()
        table.set_columns(["Cog", "Success", "Failed", "Total"])
        data = sorted(
            [(cog, e.success, e.failed, e.total) for cog, e in data.items()],
            key=lambda t: t[-1],
            reverse=True,
        )

        table.add_rows(data)
        render = table.render()
        await ctx.send(discord.utils.escape_mentions(f"```\n{render}\n```"))

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        em = discord.Embed(
            title="Joined Guild",
            color=discord.Color.green(),
            timestamp=guild.created_at,
        )
        if guild.icon:
            em.set_thumbnail(url=guild.icon.url)
        em.set_footer(text="Created")

        em.add_field(name="Name", value=guild.name)
        em.add_field(name="ID", value=guild.id)
        em.add_field(name="Owner", value=str(guild.owner))
        em.add_field(name="Member Count", value=guild.member_count)

        await self.bot.console.send(embed=em)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        em = discord.Embed(
            title="Left Guild", color=discord.Color.red(), timestamp=guild.created_at
        )
        if guild.icon:
            em.set_thumbnail(url=guild.icon.url)
        em.set_footer(text="Created")

        em.add_field(name="Name", value=guild.name)
        em.add_field(name="ID", value=guild.id)
        em.add_field(name="Owner", value=str(guild.owner))
        em.add_field(name="Member Count", value=guild.member_count)

        await self.bot.console.send(embed=em)

    # Socket Stats

    @commands.Cog.listener()
    async def on_socket_event_type(self, event_type):
        self.bot.socket_stats[event_type] += 1

    @flags.add_flag("--sort", "-s", default="count")
    @flags.add_flag("--json", action="store_true")
    @commands.command(aliases=["socket", "websocket"], cls=NoUsageFlagCommand)
    async def socketstats(self, ctx, **flags):
        """Shows websocket stats.

        Flags:
          `--sort` `-s`  Sort by 'name' or by 'count'. Defaults to 'name'
          `--json`  Save socketstats to a json file for use programmatically
        """

        if flags["json"]:
            stats = {
                "uptime": datetime.timestamp(self.bot.uptime),
                "total": sum(self.bot.socket_stats.values()),
            }
            stats.update(self.bot.socket_stats)

            output = io.BytesIO()
            output.write(json.dumps(stats, indent=2).encode())
            output.seek(0)

            file = discord.File(output, filename="socketstats.json")
            return await ctx.send("Socket stats attached below", file=file)

        sort = flags["sort"].lower()

        if sort not in ["name", "count"]:
            raise commands.BadArgument("`--sort` flag must be either 'name' or 'count'")

        sorted_stats = {}

        if sort == "name":
            the_stats_sorted = sorted(self.bot.socket_stats.keys())

        else:
            the_stats_sorted = {
                k: v
                for k, v in reversed(
                    sorted(self.bot.socket_stats.items(), key=lambda item: item[1])
                )
            }

        for name in the_stats_sorted:
            sorted_stats[name] = self.bot.socket_stats[name]

        data = [[n or "None", v] for n, v in sorted_stats.items()]
        data.insert(0, ["Total", sum(self.bot.socket_stats.values())])

        delta = datetime.datetime.utcnow() - self.bot.uptime
        minutes = delta.total_seconds() / 60
        total = sum(self.bot.socket_stats.values())
        cpm = total / minutes

        description = f"Total socket events observed: {total} ({cpm:.2f}/minute)"
        pages = ctx.table_pages(data, title="Websocket Stats", description=description)
        await pages.start()

    # https://github.com/Rapptz/RoboDanny/blob/rewrite/cogs/stats.py#L642-L740
    @commands.command(hidden=True)
    @commands.is_owner()
    async def bothealth(self, ctx):
        """Shows the bot's health."""

        # This uses a lot of private methods because there is no
        # clean way of doing this otherwise.

        HEALTHY = discord.Colour(value=0x43B581)
        UNHEALTHY = discord.Colour(value=0xF04947)
        WARNING = discord.Colour(value=0xF09E47)
        total_warnings = 0

        embed = discord.Embed(title="Bot Health Report", colour=HEALTHY)

        # Check the connection pool health.
        pool = self.bot.pool
        total_waiting = len(pool._queue._getters)
        current_generation = pool._generation

        description = [
            f"Total `Pool.acquire` Waiters: {total_waiting}",
            f"Current Pool Generation: {current_generation}",
            f"Connections In Use: {len(pool._holders) - pool._queue.qsize()}",
        ]

        questionable_connections = 0
        connection_value = []
        for index, holder in enumerate(pool._holders, start=1):
            generation = holder._generation
            in_use = holder._in_use is not None
            is_closed = holder._con is None or holder._con.is_closed()
            display = f"gen={holder._generation} in_use={in_use} closed={is_closed}"
            questionable_connections += any((in_use, generation != current_generation))
            connection_value.append(f"<Holder i={index} {display}>")

        joined_value = "\n".join(connection_value)
        embed.add_field(
            name="Connections", value=f"```py\n{joined_value}\n```", inline=False
        )

        spam_control = self.bot._cd 
        being_spammed = [
            str(key) for key, value in spam_control._cache.items() if value._tokens == 0
        ]

        description.append(
            f'Current Spammers: {", ".join(being_spammed) if being_spammed else "None"}'
        )
        description.append(f"Questionable Connections: {questionable_connections}")

        total_warnings += questionable_connections
        if being_spammed:
            embed.colour = WARNING
            total_warnings += 1

        try:
            task_retriever = asyncio.Task.all_tasks
        except AttributeError:
            # future proofing for 3.9 I guess
            task_retriever = asyncio.all_tasks
        else:
            all_tasks = task_retriever(loop=self.bot.loop)

        event_tasks = [
            t for t in all_tasks if "Client._run_event" in repr(t) and not t.done()
        ]

        cogs_directory = os.path.dirname(__file__)
        tasks_directory = os.path.join("discord", "ext", "tasks", "__init__.py")
        inner_tasks = [
            t
            for t in all_tasks
            if cogs_directory in repr(t) or tasks_directory in repr(t)
        ]

        bad_inner_tasks = ", ".join(
            hex(id(t)) for t in inner_tasks if t.done() and t._exception is not None
        )
        total_warnings += bool(bad_inner_tasks)
        embed.add_field(
            name="Inner Tasks",
            value=f'Total: {len(inner_tasks)}\nFailed: {bad_inner_tasks or "None"}',
        )
        embed.add_field(
            name="Events Waiting", value=f"Total: {len(event_tasks)}", inline=False
        )

        command_waiters = len(self._data_batch)
        is_locked = self._batch_lock.locked()
        description.append(
            f"Commands Waiting: {command_waiters}, Batch Locked: {is_locked}"
        )

        proc = psutil.Process()
        memory_usage = proc.memory_full_info().uss / 1024 ** 2
        cpu_usage = psutil.cpu_percent()
        embed.add_field(
            name="Process",
            value=f"{memory_usage:.2f} MiB\n{cpu_usage:.2f}% CPU",
            inline=False,
        )

        global_rate_limit = not self.bot.http._global_over.is_set()
        description.append(f"Global Rate Limit: {global_rate_limit}")

        if command_waiters >= 8:
            total_warnings += 1
            embed.colour = WARNING

        if global_rate_limit or total_warnings >= 9:
            embed.colour = UNHEALTHY

        embed.set_footer(text=f"{total_warnings} warning(s)")
        embed.description = "\n".join(description)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Stats(bot))
