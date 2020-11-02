from datetime import datetime, timezone, timedelta
from collections import Counter
import asyncio
import functools
import io
import typing
from collections import defaultdict, Counter

import discord
from discord.ext import commands, tasks
import asyncpg
import humanize
import git
import psutil
import itertools

from .utils.utils import get_lines_of_code, TabularData
from .utils import db, colors, human_time
from .utils.emojis import TEXT_CHANNEL, VOICE_CHANNEL
from .utils.menus import MenuPages


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
        self.emoji = ":bar_chart:"
        self._batch_lock = asyncio.Lock(loop=bot.loop)
        self._data_batch = []
        self.bulk_insert_loop.add_exception_type(asyncpg.PostgresConnectionError)
        self.bulk_insert_loop.start()

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
        aliases=["statistics"],
        invoke_without_command=True,
    )
    @commands.guild_only()
    @commands.cooldown(1, 30.0, type=commands.BucketType.member)
    async def stats(self, ctx, *, member: discord.Member = None):
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
                value.append(f"{places[index]} **{command}** ({uses} uses)")

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
            value.append(f"{places[index]} **{command}** ({uses} uses)")

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

    def format_commit(self, commit):
        short = commit.summary
        short_sha2 = commit.name_rev[0:6]

        # [`hash`](url) message (offset)
        offset = human_time.human_timedelta(
            commit.committed_datetime.astimezone(timezone.utc).replace(tzinfo=None),
            accuracy=1,
        )
        commit_hex = commit.name_rev.split()[0]
        return f"[`{short_sha2}`](https://github.com/Fyssion/Clam/commit/{commit_hex}) {short} ({offset})"

    def get_latest_commits(self, count=3):
        repo = git.Repo(".")
        commits = list(list(repo.iter_commits("main", max_count=count)))
        return "\n".join(self.format_commit(c) for c in commits)

    @commands.command(
        name="about", description="Display info about the bot", aliases=["info"],
    )
    async def about(self, ctx):
        revisions = self.get_latest_commits()
        em = discord.Embed(
            title="About",
            description=f"Latest changes:\n{revisions}",
            color=colors.PRIMARY,
        )

        em.set_footer(
            text=f"Made with \N{HEAVY BLACK HEART} using discord.py v{discord.__version__}"
        )

        em.set_thumbnail(url=self.bot.user.avatar_url)

        dev = self.bot.get_user(224513210471022592)
        up = datetime.now() - self.bot.startup_time
        em.add_field(name=":gear: Creator", value=str(dev))
        em.add_field(name=":adult: User Count", value=len(self.bot.users))
        em.add_field(name=":family: Server Count", value=len(self.bot.guilds))

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
            value=humanize.naturaldelta(up).capitalize(),
        )
        cpu = psutil.cpu_percent()

        proc = psutil.Process()
        mem = proc.memory_full_info()
        used = humanize.naturalsize(mem.uss)
        em.add_field(name="Process", value=f"{cpu}% CPU\n{used} memory")

        partial = functools.partial(get_lines_of_code)
        lines = await self.bot.loop.run_in_executor(None, partial)
        em.add_field(name=":page_facing_up: Code", value=lines, inline=False)

        await ctx.send(embed=em)

    @commands.command(
        description="Get the latest changes for the bot", aliases=["changes", "latest", "news"]
    )
    async def changelog(self, ctx):
        async with ctx.typing():
            revisions = self.get_latest_commits(10)

        em = discord.Embed(
            title="Latest changes",
            description=f"{revisions}\n[...view all changes](https://github.com/Fyssion/Clam/commits/main)",
            color=colors.PRIMARY,
        )
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
        uptime = human_time.human_timedelta(
            self.bot.startup_time, source=datetime.now()
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
        """Command history."""
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
        """Command history for a command."""

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

        await self.tabulate_query(ctx, query, command, timedelta(days=days))

    @command_history.command(name="guild", aliases=["server"])
    @commands.is_owner()
    async def command_history_guild(self, ctx, guild_id: int):
        """Command history for a guild."""

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
        """Command history for a user."""

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
        """Command history log for the last N days."""

        query = """SELECT name, COUNT(*)
                   FROM commands
                   WHERE invoked_at > (CURRENT_TIMESTAMP - $1::interval)
                   GROUP BY name
                   ORDER BY 2 DESC
                """

        all_commands = {c.qualified_name: 0 for c in self.bot.walk_commands()}

        records = await ctx.db.fetch(query, timedelta(days=days))
        for name, uses in records:
            if name in all_commands:
                all_commands[name] = uses

        as_data = sorted(all_commands.items(), key=lambda t: t[1], reverse=True)
        table = TabularData()
        table.set_columns(["Command", "Uses"])
        table.add_rows(tup for tup in as_data)
        render = table.render()

        embed = discord.Embed(title="Summary", colour=discord.Colour.green())
        embed.set_footer(
            text="Since"
        ).timestamp = datetime.utcnow() - timedelta(days=days)

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
        """Command history for a cog or grouped by a cog."""

        interval = timedelta(days=days)
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
        em = discord.Embed(title="Joined Guild", color=discord.Color.green(), timestamp=guild.created_at)
        em.set_thumbnail(url=guild.icon_url)
        em.set_footer(text="Created")

        em.add_field(name="Name", value=guild.name)
        em.add_field(name="ID", value=guild.id)
        em.add_field(name="Owner", value=str(guild.owner))
        em.add_field(name="Member Count", value=guild.member_count)

        await self.bot.console.send(embed=em)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        em = discord.Embed(title="Left Guild", color=discord.Color.red(), timestamp=guild.created_at)
        em.set_thumbnail(url=guild.icon_url)
        em.set_footer(text="Created")

        em.add_field(name="Name", value=guild.name)
        em.add_field(name="ID", value=guild.id)
        em.add_field(name="Owner", value=str(guild.owner))
        em.add_field(name="Member Count", value=guild.member_count)

        await self.bot.console.send(embed=em)

    # Socket Stats

    @commands.Cog.listener()
    async def on_socket_response(self, msg):
        self.bot.socket_stats[msg.get("t") or "None"] += 1

    @commands.command(
        description="View websocket stats",
        aliases=["ws", "ss", "socket", "websocket", "websocketstats"],
    )
    async def socketstats(self, ctx):
        sorted_stats = {}

        for name in sorted(self.bot.socket_stats.keys()):
            sorted_stats[name] = self.bot.socket_stats[name]

        data = [[n or "None", v] for n, v in sorted_stats.items()]
        data.insert(0, ["Total", sum(self.bot.socket_stats.values())])

        delta = datetime.utcnow() - self.bot.startup_time
        minutes = delta.total_seconds() / 60
        total = sum(self.bot.socket_stats.values())
        cpm = total / minutes

        description = f"Total socket events observed: {total} ({cpm:.2f}/minute)"
        pages = ctx.table_pages(data, title="Websocket Stats", description=description)
        await pages.start(ctx)


def setup(bot):
    if not hasattr(bot, "command_stats"):
        bot.command_stats = Counter()

    bot.add_cog(Stats(bot))
