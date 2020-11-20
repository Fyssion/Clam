import discord
from discord.ext import commands

from collections import defaultdict
from typing import Optional
import asyncpg

from .utils import db, cache, checks, colors


class CommandPermissionsTable(db.Table, table_name="command_permissions"):
    id = db.PrimaryKeyColumn()

    guild_id = db.Column(db.Integer(big=True))
    channel_id = db.Column(db.Integer(big=True))
    command = db.Column(db.String)
    allowed = db.Column(db.Boolean)

    @classmethod
    def create_table(cls, *, exists_ok=True):
        statement = super().create_table(exists_ok=exists_ok)
        sql = "CREATE UNIQUE INDEX IF NOT EXISTS perms_uniq_idx ON command_permissions (command, guild_id, channel_id, allowed);\n"
        return statement + "\n" + sql


class IgnoredEntities(db.Table, table_name="ignored_entities"):
    id = db.PrimaryKeyColumn()

    guild_id = db.Column(db.Integer(big=True))
    entity_id = db.Column(db.Integer(big=True))


class CogPermissionsTable(db.Table, table_name="cog_permissions"):
    id = db.PrimaryKeyColumn()

    guild_id = db.Column(db.Integer(big=True))
    channel_id = db.Column(db.Integer(big=True))
    cog = db.Column(db.String)
    allowed = db.Column(db.Boolean)

    @classmethod
    def create_table(cls, *, exists_ok=True):
        statement = super().create_table(exists_ok=exists_ok)
        sql = "CREATE UNIQUE INDEX IF NOT EXISTS cog_perms_uniq_idx ON cog_permissions (cog, guild_id, channel_id, allowed);\n"
        return statement + "\n" + sql


class CommandName(commands.Converter):
    async def convert(self, ctx, arg):
        arg = arg.lower()

        valid_commands = {
            c.qualified_name
            for c in ctx.bot.walk_commands()
            if c.cog_name not in ("Settings", "Admin")
        }

        if arg not in valid_commands:
            raise commands.BadArgument(f"Command `{arg}` is not a valid command.")

        return arg


class CogName(commands.Converter):
    async def convert(self, ctx, arg):
        lowered = arg.lower()

        valid_cogs = {
            c.lower() for c in ctx.bot.cogs.keys() if c not in ("Settings", "Admin")
        }

        if lowered not in valid_cogs:
            raise commands.BadArgument(f"Cog `{arg}` is not a valid cog.")

        return arg


class ChannelOrMember(commands.Converter):
    async def convert(self, ctx, arg):
        try:
            return await commands.TextChannelConverter().convert(ctx, arg)
        except commands.BadArgument:
            return await commands.MemberConverter().convert(ctx, arg)


class CommandPermissions:
    """Command permissions resolved for a guild"""

    class Entry:
        def __init__(self):
            self.allowed = set()
            self.denied = set()

    def __init__(self, guild_id, records):
        self.guild_id = guild_id
        self.records = records

        self.permissions = defaultdict(self.Entry)

        for command, channel_id, allowed in records:
            entry = self.permissions[channel_id]

            if allowed:
                entry.allowed.add(command)

            else:
                entry.denied.add(command)

    def _split(self, obj):
        # "hello there world" -> ["hello", "hello there", "hello there world"]
        from itertools import accumulate

        return list(accumulate(obj.split(), lambda x, y: f"{x} {y}"))

    def get_blocked_commands(self, channel_id):
        if len(self.permissions) == 0:
            return set()

        guild = self.permissions[None]
        channel = self.permissions[channel_id]

        # first, apply the guild-level denies
        ret = guild.denied - guild.allowed

        # then apply the channel-level denies
        return (ret | (channel.denied - channel.allowed)) if channel_id else ret

    def _is_blocked(self, command, channel_id):
        command_names = self._split(command)

        guild = self.permissions[None]
        channel = self.permissions[channel_id]

        blocked = None

        for command in command_names:
            if command in guild.denied:
                blocked = True

            if command in guild.allowed:
                blocked = False

        for command in command_names:
            if command in channel.denied:
                blocked = True

            if command in channel.allowed:
                blocked = False

        return blocked

    def is_command_blocked(self, command, channel_id):
        if not len(self.permissions):
            return False

        return self._is_blocked(command, channel_id)

    def is_blocked(self, ctx):
        if not len(self.permissions):
            return False

        if ctx.author.id == ctx.bot.owner_id:
            return False

        if (
            isinstance(ctx.author, discord.Member)
            and ctx.author.guild_permissions.manage_guild
        ):
            return False

        return self._is_blocked(ctx.command.qualified_name, ctx.channel.id)


class CogPermissions(CommandPermissions):
    """Basically CommandPermissions"""

    def _is_blocked(self, cog, channel_id):
        guild = self.permissions[None]
        channel = self.permissions[channel_id]

        blocked = None

        if cog in guild.denied:
            blocked = True

        if cog in guild.allowed:
            blocked = False

        if cog in channel.denied:
            blocked = True

        if cog in channel.allowed:
            blocked = False

        return blocked

    get_blocked_cogs = CommandPermissions.get_blocked_commands
    is_cog_blocked = CommandPermissions.is_command_blocked

    def is_blocked(self, ctx):
        if not len(self.permissions):
            return False

        if ctx.author.id == ctx.bot.owner_id:
            return False

        if (
            isinstance(ctx.author, discord.Member)
            and ctx.author.guild_permissions.manage_guild
        ):
            return False

        return self._is_blocked(ctx.cog.qualified_name, ctx.channel.id)


class Settings(commands.Cog):
    """Commands to configure the bot"""

    def __init__(self, bot):
        self.bot = bot
        self.emoji = "\N{GEAR}"
        self.log = self.bot.log

    @cache.cache()
    async def get_command_permissions(self, guild_id):
        query = """SELECT command, channel_id, allowed
                   FROM command_permissions
                   WHERE guild_id=$1;
                """

        records = await self.bot.pool.fetch(query, guild_id)
        return CommandPermissions(guild_id, records or [])

    @cache.cache()
    async def get_cog_permissions(self, guild_id):
        query = """SELECT cog, channel_id, allowed
                   FROM cog_permissions
                   WHERE guild_id=$1;
                """

        records = await self.bot.pool.fetch(query, guild_id)
        return CogPermissions(guild_id, records or [])

    async def bot_check(self, ctx):
        cog_perms = await self.get_cog_permissions(ctx.guild.id)
        if cog_perms.is_blocked(ctx):
            return False

        cmd_perms = await self.get_command_permissions(ctx.guild.id)
        return not cmd_perms.is_blocked(ctx)

    @cache.cache()
    async def is_ignored(
        self, guild_id, member_id, channel_id=None, *, check_bypass=True
    ):
        """Returns whether a member or channel is ignored in a guild"""
        if member_id in self.bot.blacklist or guild_id in self.bot.blacklist:
            return True

        if check_bypass:
            guild = self.bot.get_guild(guild_id)
            if guild is not None:
                member = guild.get_member(member_id)
                if member is not None and member.guild_permissions.manage_guild:
                    return False

        if channel_id is None:
            query = "SELECT 1 FROM ignored_entities WHERE guild_id=$1 AND entity_id=$2;"
            row = await self.bot.pool.fetchrow(query, guild_id, member_id)
        else:
            query = "SELECT 1 FROM ignored_entities WHERE guild_id=$1 AND entity_id IN ($2, $3);"
            row = await self.bot.pool.fetchrow(query, guild_id, member_id, channel_id)

        return row is not None

    async def bot_check_once(self, ctx):
        if not ctx.guild:
            return True

        return not await self.is_ignored(ctx.guild.id, ctx.author.id, ctx.channel.id)

    @commands.group(aliases=["config"], invoke_without_command=True)
    @checks.has_permissions(manage_guild=True)
    async def settings(self, ctx):
        """Settings to configure the bot"""
        await ctx.send_help(ctx.command)

    @settings.command(name="ignore")
    @checks.has_permissions(manage_guild=True)
    async def settings_ignore(self, ctx, *, entity: ChannelOrMember = None):
        """Ignore commands from a member or channel in this server"""
        entity = entity or ctx.channel

        if isinstance(entity, discord.TextChannel):
            human_friendly = entity.mention

        else:
            human_friendly = f"`{entity}`"

        if await self.is_ignored(ctx.guild.id, entity.id):
            raise commands.BadArgument(
                f"{human_friendly} is already on the server ignore list."
            )

        query = """INSERT INTO ignored_entities (guild_id, entity_id)
                   VALUES ($1, $2);
                """

        await ctx.db.execute(query, ctx.guild.id, entity.id)
        self.is_ignored.invalidate_containing(f"{ctx.guild.id!r}:")

        await ctx.send(
            ctx.tick(True, f"Added {human_friendly} to the server ignore list.")
        )

    @settings.group(name="unignore", invoke_without_command=True)
    @checks.has_permissions(manage_guild=True)
    async def settings_unignore(self, ctx, *, entity: ChannelOrMember = None):
        """Remove a member or channel from the server ignore list"""
        entity = entity or ctx.channel

        if isinstance(entity, discord.TextChannel):
            human_friendly = entity.mention

        else:
            human_friendly = f"`{entity}`"

        query = """DELETE FROM ignored_entities
                   WHERE guild_id=$1 AND entity_id=$2
                   RETURNING ignored_entities.id;
                """

        record = await ctx.db.fetchrow(query, ctx.guild.id, entity.id)

        if not record:
            return await ctx.send(f"{human_friendly} is not on the server ignore list.")

        self.is_ignored.invalidate_containing(f"{ctx.guild.id!r}:")

        await ctx.send(
            ctx.tick(True, f"Removed {human_friendly} from the server ignore list.")
        )

    @settings_unignore.command(name="all")
    @checks.has_permissions(manage_guild=True)
    async def settings_unignore_all(self, ctx):
        """Alias for settings ignored clear"""
        await ctx.invoke(self.settings_ignored_clear)

    @settings.group(name="ignored", invoke_without_command=True)
    @checks.has_permissions(manage_guild=True)
    @checks.has_permissions(manage_guild=True)
    async def settings_ignored(self, ctx):
        """View the server ignore list"""
        query = "SELECT entity_id FROM ignored_entities WHERE guild_id=$1;"
        records = await ctx.db.fetch(query, ctx.guild.id)

        entities = []

        for record in records:
            entity_id = record[0]
            entity = ctx.guild.get_member(entity_id)

            if not entity:
                channel = ctx.guild.get_channel(entity_id)

                if channel:
                    entity = channel.mention

                else:
                    entity = f"User or channel with an id of {entity_id}"

            entities.append(entity)

        if not entities:
            return await ctx.send("No entities in the server ignored list.")

        em = discord.Embed(title="Server Ignore List", color=colors.PRIMARY)

        pages = ctx.embed_pages(entities, em)
        await pages.start(ctx)

    @settings_ignored.command(name="clear")
    @checks.has_permissions(manage_guild=True)
    async def settings_ignored_clear(self, ctx):
        """Clear all entities from the server ignore list"""
        query = "DELETE FROM ignored_users WHERE guild_id=$1;"
        await ctx.db.execute(query, ctx.guild.id)

        self.is_ignored.invalidate_containing(f"{ctx.guild.id!r}:")
        await ctx.send(
            ctx.tick(True, "Cleared all entities from the server ignore list.")
        )

    async def command_toggle(
        self, connection, guild_id, channel_id, command, *, allowed=True
    ):
        # clear the cache
        self.get_command_permissions.invalidate(self, guild_id)

        if channel_id is None:
            subcheck = "channel_id IS NULL"
            args = (guild_id, command)
        else:
            subcheck = "channel_id=$3"
            args = (guild_id, command, channel_id)

        async with connection.transaction():
            # delete the previous entry regardless of what it was
            query = f"DELETE FROM command_permissions WHERE guild_id=$1 AND command=$2 AND {subcheck};"

            # DELETE <num>
            await connection.execute(query, *args)

            query = "INSERT INTO command_permissions (guild_id, channel_id, command, allowed) VALUES ($1, $2, $3, $4);"

            try:
                await connection.execute(query, guild_id, channel_id, command, allowed)
            except asyncpg.UniqueViolationError:
                msg = (
                    "This command is already disabled."
                    if not allowed
                    else "This command is already explicitly enabled."
                )
                raise RuntimeError(msg)

    @settings.command(name="disable")
    @checks.has_permissions(manage_guild=True)
    async def settings_disable(
        self, ctx, channel: Optional[discord.TextChannel], command: CommandName
    ):
        """Disable a command in the server or a channel"""
        channel_id = channel.id if channel else None

        try:
            async with ctx.db.acquire() as conn:
                await self.command_toggle(
                    conn, ctx.guild.id, channel_id, command, allowed=False
                )

        except RuntimeError as e:
            await ctx.send(e)

        else:
            human_friendly = channel.mention if channel else "this server"
            await ctx.send(
                ctx.tick(True, f"Disabled command `{command}` in {human_friendly}")
            )

    @settings.command(name="enable")
    @checks.has_permissions(manage_guild=True)
    async def settings_enable(
        self, ctx, channel: Optional[discord.TextChannel], command: CommandName
    ):
        """Enable a command in the server or a channel"""
        channel_id = channel.id if channel else None

        try:
            async with ctx.db.acquire() as conn:
                await self.command_toggle(conn, ctx.guild.id, channel_id, command)

        except RuntimeError as e:
            await ctx.send(e)

        else:
            human_friendly = channel.mention if channel else "this server"
            await ctx.send(
                ctx.tick(True, f"Enabled command `{command}` in {human_friendly}")
            )

    @settings.command(name="disabled")
    @checks.has_permissions(manage_guild=True)
    async def settings_disabled(self, ctx, channel: discord.TextChannel = None):
        """View disabled commands in a channel or the server"""
        perms = await self.get_command_permissions(ctx.guild.id)
        commands = list(perms.get_blocked_commands(channel.id if channel else None))

        human_friendly = channel.mention if channel else "this server"

        if not commands:
            return await ctx.send(f"No commands disabled in {human_friendly}")

        em = discord.Embed(
            title=f"Disabled commands in {human_friendly}", color=colors.PRIMARY
        )

        pages = ctx.embed_pages(commands, em)
        await pages.start(ctx)

    async def cog_toggle(
        self, connection, guild_id, channel_id, command, *, allowed=True
    ):
        # clear the cache
        self.get_cog_permissions.invalidate(self, guild_id)

        if channel_id is None:
            subcheck = "channel_id IS NULL"
            args = (guild_id, command)
        else:
            subcheck = "channel_id=$3"
            args = (guild_id, command, channel_id)

        async with connection.transaction():
            # delete the previous entry regardless of what it was
            query = f"DELETE FROM cog_permissions WHERE guild_id=$1 AND cog=$2 AND {subcheck};"

            # DELETE <num>
            await connection.execute(query, *args)

            query = "INSERT INTO cog_permissions (guild_id, channel_id, cog, allowed) VALUES ($1, $2, $3, $4);"

            try:
                await connection.execute(query, guild_id, channel_id, command, allowed)
            except asyncpg.UniqueViolationError:
                msg = (
                    "This category is already disabled."
                    if not allowed
                    else "This category is already explicitly enabled."
                )
                raise RuntimeError(msg)

    @settings.group(name="category", aliases=["cog"], invoke_without_command=True)
    @checks.has_permissions(manage_guild=True)
    async def settings_category(self, ctx):
        """Enable and disable categories in the server or a channel"""
        await ctx.send_help(ctx.command)

    @settings_category.command(name="disable")
    @checks.has_permissions(manage_guild=True)
    async def settings_category_disable(
        self, ctx, channel: Optional[discord.TextChannel], command: CogName
    ):
        """Disable a category in the server or a channel"""
        channel_id = channel.id if channel else None

        try:
            async with ctx.db.acquire() as conn:
                await self.cog_toggle(
                    conn, ctx.guild.id, channel_id, command, allowed=False
                )

        except RuntimeError as e:
            await ctx.send(e)

        else:
            human_friendly = channel.mention if channel else "this server"
            await ctx.send(
                ctx.tick(True, f"Disabled category `{command}` in {human_friendly}")
            )

    @settings_category.command(name="enable")
    @checks.has_permissions(manage_guild=True)
    async def settings_cog_enable(
        self, ctx, channel: Optional[discord.TextChannel], command: CogName
    ):
        """Enable a category in the server or a channel"""
        channel_id = channel.id if channel else None

        try:
            async with ctx.db.acquire() as conn:
                await self.cog_toggle(conn, ctx.guild.id, channel_id, command)

        except RuntimeError as e:
            await ctx.send(e)

        else:
            human_friendly = channel.mention if channel else "this server"
            await ctx.send(
                ctx.tick(True, f"Enabled category `{command}` in {human_friendly}")
            )

    @settings_category.command(name="disabled")
    @checks.has_permissions(manage_guild=True)
    async def settings_cog_disabled(self, ctx, channel: discord.TextChannel = None):
        """View disabled categories in a channel or the server"""
        perms = await self.get_cog_permissions(ctx.guild.id)
        commands = list(perms.get_blocked_cogs(channel.id if channel else None))

        human_friendly = channel.mention if channel else "this server"

        if not commands:
            return await ctx.send(f"No categories disabled in {human_friendly}")

        em = discord.Embed(
            title=f"Disabled categories in {human_friendly}", color=colors.PRIMARY
        )

        pages = ctx.embed_pages(commands, em)
        await pages.start(ctx)


def setup(bot):
    bot.add_cog(Settings(bot))
