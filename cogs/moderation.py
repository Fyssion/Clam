from discord.ext import commands
import discord

import json
from datetime import datetime as d

from .utils.checks import has_manage_guild


class Moderation(commands.Cog, name = ":police_car: Moderation"):
    """
    This cog has not been fully developed. Will include many moderation features.
    """

    def __init__(self, bot):
        self.bot = bot
        self.log = self.bot.log

        with open("log_channels.json", "r") as f:
            self.log_channels = json.load(f)

    def get_log(self, guild):
        if str(guild) in self.log_channels.keys():
            channel_id = self.log_channels.get(str(guild))
            return self.bot.get_channel(int(channel_id))
        return None

    @commands.command(
        name="purge",
        description=("Purge messages in a channel.\n"
                     "**Note: The user calling the command and the bot must have the manage messages permission.**"),
        aliases=["cleanup"],
        usage="[amount]"
    )
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def purge_command(self, ctx, amount=None):
        def is_not_ctx(msg):
            return msg.id != ctx.message.id

        if not amount:
            deleted = await ctx.channel.purge(limit=None, check=is_not_ctx)
            return await ctx.channel.send(f"Deleted {len(deleted)} message(s)", delete_after=5)

        deleted = await ctx.channel.purge(limit=int(amount), check=is_not_ctx)
        return await ctx.channel.send(f"Deleted {len(deleted)} message(s)", delete_after=5)

    @commands.group(name="log", description="Keep a log of all user actions.", invoke_without_command=True)
    @commands.guild_only()
    @has_manage_guild()
    async def _log(self, ctx):
        log = self.get_log(ctx.guild.id)
        if not log:
            await self.enable(ctx)
        return await ctx.send(f"Server log at {log.mention}. Use `{self.bot.guild_prefix(ctx.guild.id)}log set` to change log channel.")

    @_log.command(description="Enable your server's log.")
    @commands.guild_only()
    @has_manage_guild()
    async def enable(self, ctx):
        if self.get_log(ctx.guild.id):
            return await ctx.send("Log is already enabled!")
        await self._set(ctx)

    @_log.command(description="Disable your server's log.")
    @commands.guild_only()
    @has_manage_guild()
    async def disable(self, ctx):
        if not self.get_log(str(ctx.guild.id)):
            await ctx.send("This server doesn't have a log.")
        self.log_channels.pop(str(ctx.guild.id))
        with open("log_channels.json", "w") as f:
            json.dump(self.log_channels, f, sort_keys=True, indent=4, separators=(',', ': '))
        await ctx.send(f"**Log disabled**")

    @_log.command(name="set", description="Set your server's log channel.",
                  aliases=["setup"])
    @commands.guild_only()
    @has_manage_guild()
    async def _set(self, ctx, channel: discord.TextChannel = None):
        if not channel:
            channel = ctx.channel
        if channel.guild.id != ctx.guild.id:
            return await ctx.send("You must specify a channel in this server.")
        self.log_channels[str(ctx.guild.id)] = channel.id
        with open("log_channels.json", "w") as f:
            json.dump(self.log_channels, f, sort_keys=True, indent=4, separators=(',', ': '))
        await ctx.send(f"Log channel set to {channel.mention}")

    @commands.Cog.listener("on_message_delete")
    async def _deletion_detector(self, message):
        log = self.get_log(message.guild.id)
        if not log:
            return
        em = discord.Embed(title="Message Deletion", color=discord.Color.red(),
                           timestamp=message.created_at)
        em.set_author(name=str(message.author), icon_url=message.author.avatar_url)
        em.set_footer(text=f"Message sent at")
        em.description = f"In {message.channel.mention}:\n>>> {message.content}"
        await log.send(embed=em)

    @commands.Cog.listener("on_message_edit")
    async def _edit_detector(self, before, after):
        log = self.get_log(before.guild.id)
        if not log or log.id == before.id or before.author.id == self.bot.user.id:
            return
        if before.content == after.content:
            return
        em = discord.Embed(title="Message Edit", color=discord.Color.blue(),
                           timestamp=before.created_at)
        em.set_author(name=str(before.author), icon_url=before.author.avatar_url)
        em.set_footer(text=f"Message sent at")
        em.description = (f"[Jump](https://www.discordapp.com/channels/{before.guild.id}/{before.channel.id}/{before.id})\n"
                          f"In {before.channel.mention}:")
        em.add_field(name="Before", value=f">>> {before.content}")
        em.add_field(name="After", value=f">>> {after.content}")
        await log.send(embed=em)


def setup(bot):
    bot.add_cog(Moderation(bot))
