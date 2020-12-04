from discord.ext import commands, menus
import discord

import asyncpg

from .utils import db, checks, colors


class SelfRolesTable(db.Table, table_name="selfroles"):
    id = db.PrimaryKeyColumn()

    guild_id = db.Column(db.Integer(big=True))
    role_id = db.Column(db.Integer(big=True))
    description = db.Column(db.String)
    created_at = db.Column(db.Datetime, default="now() at time zone 'utc'")

    @classmethod
    def create_table(cls, *, exists_ok=True):
        statement = super().create_table(exists_ok=exists_ok)
        sql = "CREATE UNIQUE INDEX IF NOT EXISTS roles_uniq_idx ON selfroles (guild_id, role_id);"
        return statement + "\n" + sql


class SelfRole:
    @classmethod
    def from_record(cls, record, bot):
        self = cls()

        self.bot = bot

        self.id = record["id"]
        self.guild_id = record["guild_id"]
        self.role_id = record["role_id"]
        self.description = record["description"]
        self.created_at = record["created_at"]

        return self

    @property
    def guild(self):
        return self.bot.get_guild(self.guild_id)

    @property
    def role(self):
        return self.guild.get_role(self.role_id)

    @classmethod
    async def convert(cls, ctx, arg):
        role = await commands.RoleConverter().convert(ctx, arg)

        query = "SELECT * FROM selfroles WHERE role_id=$1 AND guild_id=$2;"
        record = await ctx.db.fetchrow(query, role.id, ctx.guild.id)

        if not record:
            escaped = discord.utils.escape_mentions(arg)
            raise commands.BadArgument(f"Selfrole '{escaped}' not found.")

        return cls.from_record(record, ctx.bot)


class SelfRoleDescription(commands.Converter):
    async def convert(self, ctx, arg):
        if len(arg) > 64:
            raise commands.BadArgument(f"Selfrole description must be 64 characters or less. ({len(arg)}/64)")

        return arg


class Selfroles(commands.Cog):
    """Assign roles to yourself through command or reaction.

    Mods must create a selfrole or set an existing role as a selfrole.
    """

    def __init__(self, bot):
        self.bot = bot
        self.emoji = "<:selfroles:784533393538154597>"

    @commands.Cog.listener()
    async def on_guild_leave(self, guild):
        """Remove all selfroles of a guild when I leave it"""
        query = "DELETE FROM selfroles WHERE guild_id=$1"
        await self.bot.pool.execute(query, guild.id)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        """Remove selfrole reference when a role is deleted"""
        query = "DELETE FROM selfroles WHERE guild_id=$1 AND role_id=$2"
        await self.bot.pool.execute(query, role.guild.id, role.id)

    @commands.group(aliases=["role"], invoke_without_command=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def selfrole(self, ctx):
        """A set of commands to manage selfroles."""
        await ctx.send_help(ctx.command)

    @selfrole.command(name="add", aliases=["sub"])
    @commands.bot_has_permissions(manage_roles=True)
    async def selfrole_add(self, ctx, *, role: SelfRole):
        """Add a selfrole to yourself.

        The role specified must be a selfrole.
        """
        if not role.role:
            await ctx.send("That role doesn't seem to exist anymore. Contact a mod.")

        if role.role in ctx.author.roles:
            return await ctx.send("You already have this role.")

        try:
            await ctx.author.add_roles(role.role, reason="Selfrole addition")
        except discord.HTTPException:
            return await ctx.send("Failed to add role. Try again later?")

        await ctx.send(ctx.tick(True, f"Successfully added role `{role.role.name}`"))

    @selfrole.command(name="remove", aliases=["unsub"])
    @commands.bot_has_permissions(manage_roles=True)
    async def selfrole_remove(self, ctx, *, role: SelfRole):
        """Remove a selfrole from yourself.

        The role specified must be a selfrole.
        """
        if not role.role:
            await ctx.send("That role doesn't seem to exist anymore. Contact a mod.")

        if role.role not in ctx.author.roles:
            return await ctx.send("You don't have this role.")

        try:
            await ctx.author.remove_roles(role.role, reason="Selfrole removal")
        except discord.HTTPException:
            return await ctx.send("Failed to remove role. Try again later?")

        await ctx.send(ctx.tick(True, f"Successfully removed role `{role.role.name}`"))

    async def insert_selfrole(self, ctx, role, description):
        query = """INSERT INTO selfroles (guild_id, role_id, description)
                   VALUES ($1, $2, $3);
                """

        async with ctx.db.acquire() as con:
            async with con.transaction():
                try:
                    await ctx.db.execute(query, ctx.guild.id, role.id, description)
                except asyncpg.UniqueViolationError:
                    raise commands.BadArgument("There is already selfrole bound to that role.") from None

    async def delete_selfrole(self, ctx, role):
        query = """DELETE FROM selfroles
                   WHERE guild_id=$1 AND role_id=$2
                   RETURNING selfroles.id;
                """

        selfrole_id = await ctx.db.fetchval(query, ctx.guild.id, role.id)

        if not selfrole_id:
            escaped = discord.utils.escape_mentions(role.name)
            raise commands.BadArgument(f"Selfrole '{escaped}' not found.")

    @selfrole.command(name="create", aliases=["new"])
    @checks.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def selfrole_create(self, ctx, name, *, description: SelfRoleDescription = None):
        """Create a new selfrole.

        Wrap the role name in quotes if it contains spaces.

        You must have the manage roles permission to use this command.
        """
        reason = f"Selfrole creation by {ctx.author} (ID: {ctx.author.id})"

        try:
            role = await ctx.guild.create_role(name=name, reason=reason)

        except discord.HTTPException:
            return await ctx.send("Failed to create role. Maybe try again later?")

        await self.insert_selfrole(ctx, role, description)

        await ctx.send(ctx.tick(True, f"Created selfrole `{role.name}`"))

    @selfrole.command(name="delete")
    @checks.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def selfrole_delete(self, ctx, *, role: discord.Role):
        """Delete a selfrole.

        This command **will delete the role.**
        If you want to unbind a selfrole without deleting it,
        use `selfrole unbind` instead.

        You must have the manage roles permission to use this command.
        """
        await self.delete_selfrole(ctx, role)

        try:
            await role.delete()
        except discord.HTTPException:
            return await ctx.send("Failed to delete role. Try deleting it manually.")

        await ctx.send(ctx.tick(True, "Successfully deleted selfrole and corresponding role"))

    @selfrole.command(name="set")
    @checks.has_permissions(manage_roles=True)
    async def selfrole_set(self, ctx, role: discord.Role, *, description: SelfRoleDescription = None):
        """Set an existing role as a selfrole.

        Wrap the role name in quotes if it contains spaces.

        This is to be used when you want to convert a pre-existing role
        to a selfrole.

        You must have the manage roles permission to use this command.
        """
        await self.insert_selfrole(ctx, role, description)
        await ctx.send(ctx.tick(True, f"Bound new selfrole to `{role.name}`"))

    @selfrole.command(name="unbind")
    @checks.has_permissions(manage_roles=True)
    async def selfrole_unbind(self, ctx, *, role: discord.Role):
        """Unbind a selfrole from a role without deleting it.

        This is to be used when you don't want a role to be a selfrole,
        but you still want to keep the original role.

        You must have the manage roles permission to use this command.
        """
        await self.delete_selfrole(ctx, role)
        await ctx.send(ctx.tick(True, "Successfully unbound selfrole from role"))

    @selfrole.command(name="list", aliases=["all"])
    async def selfrole_list(self, ctx):
        """AView available selfroles in this server."""
        query = """SELECT role_id, description
                   FROM selfroles
                   WHERE guild_id=$1;
                """

        records = await ctx.db.fetch(query, ctx.guild.id)

        selfroles = []

        for role_id, description in records:
            role = ctx.guild.get_role(role_id)

            def format_role(name, description):
                if description:
                    return f"{name} - {description}"

                return name

            if not role:
                selfroles.append(format_role("***[unknown role]***", description))

            else:
                selfroles.append(format_role(f"**{role.name}**", description))

        em = discord.Embed(title="Available Selfroles", color=colors.PRIMARY)
        em.description = f"To add a role to yourself, use `{ctx.prefix}selfrole add <role>`"

        pages = ctx.embed_pages(selfroles, em)
        await pages.start(ctx)

    @commands.command(aliases=["roles"])
    async def selfroles(self, ctx):
        """Alias for selfrole list."""
        await ctx.invoke(self.selfrole_list)

def setup(bot):
    bot.add_cog(Selfroles(bot))
