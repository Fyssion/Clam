from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import discord
from discord.ext import commands

from clam.utils import cache, db

if TYPE_CHECKING:
    from clam.bot import Clam
    from clam.utils.context import Context, GuildContext


class AutoRolesTable(db.Table, table_name="autoroles"):
    guild_id = db.Column(db.Integer(big=True), primary_key=True)
    role_id = db.Column(db.Integer(big=True), primary_key=True)
    type = db.Column(db.Integer(small=True), default=0)  # TODO 0 both | 1 users | 2 bots
    created_at = db.Column(db.Datetime, default="now() at time zone 'utc'")


class AutoRoles(commands.Cog, name="Auto Roles"):
    """Automatically assign roles to newly joined members."""

    def __init__(self, bot: Clam):
        self.bot = bot
        self.emoji = "🪄"

    async def cog_check(self, ctx: Context) -> bool:
        await commands.has_permissions(manage_roles=True).predicate(ctx)
        await commands.bot_has_permissions(manage_roles=True).predicate(ctx)
        return True

    @cache.cache()
    async def get_autoroles(self, guild_id: int) -> list[discord.Role]:
        query = """SELECT * FROM autoroles
                   WHERE guild_id=$1;
                """

        records = await self.bot.pool.fetch(query, guild_id)
        roles: list[discord.Role] = []
        guild = self.bot.get_guild(guild_id)

        if not guild:
            return []

        for record in records:
            role = guild.get_role(record["role_id"])
            if role:
                roles.append(role)

        return roles

    @commands.group(invoke_without_command=True)
    async def autorole(self, ctx: GuildContext):
        """Commands to manage automatically assigned roles."""

        await ctx.send_help(ctx.command)

    @autorole.command(name="add")
    async def autorole_add(self, ctx: GuildContext, *, role: discord.Role):
        """Adds an auto role to the server."""

        query = "INSERT INTO autoroles (guild_id, role_id) VALUES ($1, $2);"
        await ctx.db.execute(query, ctx.guild.id, role.id)
        self.get_autoroles.invalidate(self, ctx.guild.id)

        await ctx.send(
            ctx.tick(True, f"{role.mention} will be automatically assigned to newly joined members."),
            allowed_mentions=discord.AllowedMentions.none()
        )

    @autorole.command(name="remove")
    async def autorole_remove(self, ctx: GuildContext, *, role: discord.Role):
        """Removes an auto role from the server."""

        query = "DELETE FROM autoroles WHERE guild_id=$1 AND role_id=$2;"
        await ctx.db.execute(query, ctx.guild.id, role.id)
        self.get_autoroles.invalidate(self, ctx.guild.id)

        await ctx.send(
            ctx.tick(True, f"{role.mention} will no longer be automatically assigned."),
            allowed_mentions=discord.AllowedMentions.none()
        )

    @autorole.command(name="list")
    async def autorole_list(self, ctx: GuildContext):
        roles = await self.get_autoroles(ctx.guild.id)

        if not roles:
            return await ctx.send("No auto roles registered.")

        role_list = "\n".join((role.mention for role in roles))
        await ctx.send(f"Registered auto roles:\n{role_list}", allowed_mentions=discord.AllowedMentions.none())

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        roles = await self.get_autoroles(member.guild.id)
        await member.add_roles(*roles, reason="Automatic role assignment")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        query = "DELETE FROM autoroles WHERE guild_id=$1 AND role_id=$2;"
        await self.bot.pool.execute(query, role.guild.id, role.id)
        self.get_autoroles.invalidate(self, role.guild.id)


async def setup(bot: Clam):
    await bot.add_cog(AutoRoles(bot))
