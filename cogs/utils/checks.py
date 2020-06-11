import discord
from discord.ext import commands


def has_permissions(**perms):
    async def predicate(ctx):
        try:
            return await commands.has_permissions(**perms).predicate(ctx)
        except commands.MissingPermissions:
            if ctx.bot.is_owner(ctx.author):
                return True
            else:
                raise


def has_manage_guild():
    async def predicate(ctx):
        try:
            await commands.has_guild_permissions(manage_guild=True).predicate(ctx)
            permissions = True
        except commands.errors.MissingPermissions:
            permissions = False
        return ctx.author.id == 224513210471022592 or permissions

    return commands.check(predicate)
