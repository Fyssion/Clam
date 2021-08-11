import discord
from discord.ext import commands


def has_permissions(**perms):
    async def predicate(ctx):
        try:
            await commands.has_permissions(**perms).predicate(ctx)
            return True
        except commands.MissingPermissions:
            if ctx.author.id == ctx.bot.owner_id:
                return True
            else:
                raise

    return commands.check(predicate)


def has_manage_guild():
    async def predicate(ctx):
        try:
            await commands.has_permissions(manage_guild=True).predicate(ctx)
            permissions = True
        except commands.MissingPermissions:
            permissions = False
        return ctx.author.id == 224513210471022592 or permissions

    return commands.check(predicate)
