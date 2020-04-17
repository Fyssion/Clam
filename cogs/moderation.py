from discord.ext import commands
import discord

import json
from datetime import datetime as d
import asyncio
import re
from urllib.parse import urlparse
from async_timeout import timeout

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

        with open("verifications.json", "r") as f:
            self.verifications = json.load(f)

    def get_log(self, guild):
        if str(guild) in self.log_channels.keys():
            channel_id = self.log_channels.get(str(guild))
            return self.bot.get_channel(int(channel_id))
        return None

    async def get_bin(self, url="https://hastebin.com"):
        parsed = urlparse(url)
        newpath = "/raw" + parsed.path
        url = (parsed.scheme +
            "://" +
            parsed.netloc +
            newpath)
        try:
            async with timeout(10):
                async with self.bot.session.get(url) as resp:
                    f = await resp.read()
        except asyncio.TimeoutError:
            raise TimeoutError(":warning: Could not fetch data from hastebin. \
            Is the site down? Try https://www.pastebin.com")
            return None
        async with self.bot.session.get(url) as resp:
            f = await resp.read()
            f = f.decode("utf-8")
            return f

    async def wait_for_message(self, author, channel, timeout=120):
        def check(msg):
            return msg.author == author and msg.channel == channel
        try:
            return await self.bot.wait_for("message", check=check, timeout=120)
        except asyncio.TimeoutError:
            return None

    @commands.command(description="Create a server info message for your server.")
    @commands.guild_only()
    @has_manage_guild()
    async def welcome(self, ctx):
        await ctx.send("Beginning interactive message generator in your DMs.")
        author = ctx.author
        await author.send("Welcome to the interactive message generator!\n"
                    "Paste the message you want to send here, or give me a bin link "
                    "(hastebin, mystbin, or your other bin preference).")
        message = await self.wait_for_message(author, author.dm_channel)
        content = message.content
        if content.startswith("http"):
            content = await self.get_bin(message.content)
            if len(content) > 2000:
                if "$$BREAK$$" not in content:
                    return await author.send("That message is too long, and I couldn't find any message breaks in it.\n"
                                             "Add message breaks with they keyword `$$BREAK$$`, and I will split the message there.")
            all_contents = content.split("$$BREAK$$")
        else:
            all_contents = [content]
        messages = []
        for message in all_contents:
            kwargs = {"content" : message,
                      "embed" : None}
            messages.append(kwargs)
        for message in messages:
            await author.send(**message)

    @commands.group(description="View the current verification system", invoke_without_command=True)
    @commands.guild_only()
    @has_manage_guild()
    async def verification(self, ctx):
        if ctx.guild.id in self.verifications.keys():
            return await ctx.send("**Verification is ON** for this server.")
        else:
            return await ctx.send("**Verification is OFF** for this server. "
                                  f"Set it up with `{self.bot.guild_prefix(ctx.guild)}verification create`")

    @verification.command(name="create", description="Create a verification system for your server")
    @commands.guild_only()
    @has_manage_guild()
    @commands.bot_has_permissions(manage_messages=True, manage_guild=True, manage_roles=True, manage_channels=True)
    async def ver_create(self, ctx):
        await ctx.send("Welcome to the interactive verification system generator!\n"
                       "What would you like the verification message to say?")
        message = await self.wait_for_message(ctx.author, ctx.channel)
        if len(message.content) > 2000:
            return await ctx.send("Message must be shorter than 2000 characters.")
        content = message.content

        await ctx.send("What channel should I send the verification message in? Type `none` for me to create a new channel.")
        message = await self.wait_for_message(ctx.author, ctx.channel)
        if message.channel_mentions:
            channel = message.channel_mentions[0]
        else:
            channel = self.bot.get_channel(int(message.content)) or discord.utils.get(ctx.guild.channels, name=message.content)
            if not channel:
                return await ctx.send("I couldn't find that channel. Make sure I can see the channel.")

        await ctx.send("What role should I give when verified? Type `none` for me to create a new role.")
        message = await self.wait_for_message(ctx.author, ctx.channel)
        if message.role_mentions:
            role = message.role_mentions[0]
        else:
            role = ctx.guild.get_role(int(message.content)) or discord.utils.get(ctx.guild.roles, name=message.content)
            if not channel:
                return await ctx.send("I couldn't find that role.")

        await ctx.send("What role should be allowed to confirm verifications? Type `none` for me to create a new role.")
        message = await self.wait_for_message(ctx.author, ctx.channel)
        if message.role_mentions:
            verify_role = message.role_mentions[0]
        else:
            verify_role = ctx.guild.get_role(int(message.content)) or discord.utils.get(ctx.guild.roles, name=message.content)
            if not channel:
                return await ctx.send("I couldn't find that role.")

        await ctx.send("Alright, generating the verification system... You can move the `verifiers` channel wherever you like.")

        overwrites = {
            ctx.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True,
                                                embed_links=True, read_message_history=True),
            ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            role: discord.PermissionOverwrite(read_messages=False),
            verify_role: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        notify_channel = await ctx.guild.create_text_channel("verifiers", overwrites=overwrites)

        message = await channel.send(content)
        await message.add_reaction("✅")

        self.verifications[str(ctx.guild.id)] = {
            "message_id": message.id,
            "role_id": role.id,
            "verify_role_id": verify_role.id,
            "channel_id": notify_channel.id
        }

        with open("verifications.json", "w") as f:
            json.dump(self.verifications, f)

    @commands.Cog.listener("on_raw_reaction_add")
    async def verification_reaction(self, payload):
        if str(payload.guild_id) not in self.verifications.keys():
            return
        if payload.message_id != self.verifications[str(payload.guild_id)]["message_id"]:
            return
        if payload.user_id == self.bot.user.id:
            return

        channel = self.bot.get_channel(payload.channel_id)
        guild = self.bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        role = guild.get_role(int(self.verifications[str(guild.id)]["role_id"]))
        verify_role = guild.get_role(int(self.verifications[str(guild.id)]["verify_role_id"]))
        verify_channel = guild.get_channel(int(self.verifications[str(guild.id)]["channel_id"]))

        def check(reaction, user):
            print(reaction.message.id, bot_message.id)
            print(reaction.message == bot_message)
            print(user != self.bot.user)
            print(verify_role in user.roles)
            print(reaction.emoji in ["✅", "❌"])
            return (
                reaction.message.id == bot_message.id
                and user != self.bot.user
                and verify_role in user.roles
                and reaction.emoji in ["✅", "❌"]
            )

        if not guild or not channel or not role or not verify_channel or not verify_role:
            del self.verifications[str(guild.id)]
            with open("verifications.json", "w") as f:
                json.dump(self.verifications, f, sort_keys=True, indent=4, separators=(',', ': '))
            return
        if payload.emoji.name != "✅":
            return
        await channel.send("Your verification request is being processed by the moderators.", delete_after=10)

        bot_message = await verify_channel.send(f"**`{member}` is requesting verification!**\n\n"
                                  "React with :white_check_mark: to verify them, or :x: to ignore.\n"
                                  "If you don't respond within 24 hours, they will be ignored.")
        await bot_message.add_reaction("✅")
        await bot_message.add_reaction("❌")
        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=86400) # 24h

        emoji = reaction.emoji
        if emoji == "✅":
            await member.add_roles(role, reason="Verification")
            await verify_channel.send(f"**:white_check_mark: `{user}` accepted `{member}` into the server.**")
        elif emoji == "❌":
            await verify_channel.send(f":x: `{user}` ignored `{member}`")

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

    @commands.Cog.listener("on_member_join")
    async def _join_message(self, member):
        log = self.get_log(member.guild.id)
        if not log:
            return
        em = discord.Embed(title="Member Join", description=f"**{member.mention} joined the server!**",
                           color=discord.Color.green(), timestamp=d.utcnow())
        em.set_thumbnail(url=member.avatar_url)
        await log.send(embed=em)

    @commands.Cog.listener("on_member_remove")
    async def _remove_message(self, user):
        log = self.get_log(user.guild.id)
        if not log:
            return
        em = discord.Embed(title="Member Left", description=f"**{user.mention} left the server**",
                           color=discord.Color.red(), timestamp=d.utcnow())
        em.set_thumbnail(url=user.avatar_url)
        await log.send(embed=em)


def setup(bot):
    bot.add_cog(Moderation(bot))
