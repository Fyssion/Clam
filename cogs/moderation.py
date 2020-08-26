from discord.ext import commands
from discord.flags import BaseFlags, fill_with_flags, flag_value
import discord

import json
import typing
from datetime import datetime as d
import asyncio
import re
from urllib.parse import urlparse
from async_timeout import timeout

from .utils import db
from .utils.emojis import GREEN_TICK, RED_TICK
from .utils.checks import has_manage_guild
from .utils.utils import is_int


@fill_with_flags()
class LoggingFlags(BaseFlags):
    """Describes what to log"""

    @flag_value
    def message_edit(self):
        return 1 << 0

    @flag_value
    def message_delete(self):
        return 1 << 1

    @flag_value
    def guild_join(self):
        return 1 << 2

    @flag_value
    def guild_leave(self):
        return 1 << 3


class Logs(db.Table):
    id = db.PrimaryKeyColumn()
    guild_id = db.Column(db.Integer(big=True), index=True)


class BannedUser(commands.Converter):
    async def convert(self, ctx, arg):
        bans = await ctx.guild.bans()

        # First, see if the arg matches a banned user's name
        # If not, see if the arg startswith a banned user's name
        # This allows for the use of discriminators
        # Finally, see if the arg is an int and if it's
        # that of a user in the banned list.

        banned_users = [b[0] for b in bans]

        user = discord.utils.get(banned_users, name=arg)

        if user:
            return user

        for banned_user, reason in bans:
            if arg.startswith(banned_user.name):
                user = banned_user
                break

        if user:
            return user

        try:
            arg = int(arg)
        except ValueError:
            raise commands.BadArgument(
                f"{ctx.Tick(False)} Couldn't find a banned user by that name."
            )

        user = discord.utils.get(banned_users, id=arg)

        if user:
            return user

        raise commands.BadArgument(
            f"{ctx.Tick(False)} Couldn't find a banned user by that name or ID."
        )


class Moderation(commands.Cog):
    """
    This cog has not been fully developed.
    It will include many moderation features.

    If you can't see any commands, that means you
    do not have the required permissions to use
    those commands.
    """

    def __init__(self, bot):
        self.bot = bot
        self.emoji = ":police_car:"
        self.log = self.bot.log

        with open("log_channels.json", "r") as f:
            self.log_channels = json.load(f)

        with open("verifications.json", "r") as f:
            self.verifications = json.load(f)

        self.ver_messages = {}

    @commands.command()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx, user: typing.Union[discord.User, int], *, reason=None):
        if isinstance(user, discord.User):
            user_id = user.id
            human_friendly = f"`{str(user)}`"
        else:
            user_id = user
            human_friendly = f"with an ID of `{user_id}`"

        to_be_banned = discord.Object(id=user_id)

        try:
            await ctx.guild.ban(to_be_banned, reason=reason)
        except discord.HTTPException:
            return await ctx.send(f"{ctx.tick(False)} I couldn't ban that user.")

        await ctx.send(f"{ctx.tick(True)} Banned user {human_friendly}")

    @commands.command()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx, user: BannedUser, *, reason=None):
        to_be_unbanned = discord.Object(id=user.id)

        try:
            await ctx.guild.unban(to_be_unbanned, reason=reason)
        except discord.HTTPException:
            return await ctx.send(f"{ctx.tick(False)} I couldn't unban that user.")

        await ctx.send(f"{ctx.tick(True)} Unbanned user `{user}`")

    @commands.command()
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx, user: discord.Member, *, reason=None):
        try:
            await ctx.guild.unban(user, reason=reason)
        except discord.HTTPException:
            return await ctx.send(f"{ctx.tick(False)} I couldn't kick that user.")

        await ctx.send(f"{ctx.tick(True)} Kicked user `{user}`")

    def get_log(self, guild):
        if str(guild) in self.log_channels.keys():
            channel_id = self.log_channels.get(str(guild))
            return self.bot.get_channel(int(channel_id))
        return None

    async def get_bin(self, url="https://hastebin.com"):
        parsed = urlparse(url)
        newpath = "/raw" + parsed.path
        url = parsed.scheme + "://" + parsed.netloc + newpath
        try:
            async with timeout(10):
                async with self.bot.session.get(url) as resp:
                    f = await resp.read()
        except asyncio.TimeoutError:
            raise TimeoutError(
                ":warning: Could not fetch data from hastebin. \
            Is the site down? Try https://www.pastebin.com"
            )
            return None
        async with self.bot.session.get(url) as resp:
            f = await resp.read()
            f = f.decode("utf-8")
            return f

    async def wait_for_message(self, author, channel, timeout=120):
        def check(msg):
            return msg.author == author and msg.channel == channel

        try:
            message = await self.bot.wait_for("message", check=check, timeout=timeout)
            if message.content == f"{self.bot.guild_prefix(channel.guild)}abort":
                await channel.send("Aborted")
                return None
            return message
        except asyncio.TimeoutError:
            await channel.send("You took too long! Please try again.")
            return None

    @commands.command(
        description="Create a server info message for your server.", hidden=True
    )
    @commands.guild_only()
    @has_manage_guild()
    @commands.is_owner()
    async def welcome(self, ctx):
        await ctx.send("Beginning interactive message generator in your DMs.")
        author = ctx.author
        await author.send(
            "Welcome to the interactive message generator!\n"
            "Paste the message you want to send here, or give me a bin link "
            "(hastebin, mystbin, or your other bin preference)."
        )
        message = await self.wait_for_message(author, author.dm_channel)
        content = message.content
        if content.startswith("http"):
            content = await self.get_bin(message.content)
            if len(content) > 2000:
                if "$$BREAK$$" not in content:
                    return await author.send(
                        "That message is too long, and I couldn't find any message breaks in it.\n"
                        "Add message breaks with they keyword `$$BREAK$$`, and I will split the message there."
                    )
            all_contents = content.split("$$BREAK$$")
        else:
            all_contents = [content]
        messages = []
        for message in all_contents:
            kwargs = {"content": message, "embed": None}
            messages.append(kwargs)
        await author.send("Sending message to server...")
        for message in messages:
            await author.send(**message)

    @commands.group(
        description="View the current verification system", invoke_without_command=True
    )
    @commands.guild_only()
    @has_manage_guild()
    async def verification(self, ctx):
        if str(ctx.guild.id) in self.verifications.keys():
            return await ctx.send("**Verification is ON** for this server.")
        else:
            return await ctx.send(
                "**Verification is OFF** for this server. "
                f"Set it up with `{self.bot.guild_prefix(ctx.guild)}verification create`"
            )

    @verification.command(
        name="create", description="Create a verification system for your server"
    )
    @commands.guild_only()
    @has_manage_guild()
    @commands.is_owner()
    @commands.bot_has_permissions(
        manage_messages=True, manage_roles=True, manage_channels=True
    )
    async def ver_create(self, ctx):
        await ctx.send(
            "Welcome to the interactive verification system generator! "
            f"**You can use `{ctx.guild_prefix}abort` to abort.**\n\n"
            "What would you like the verification message to say?"
        )
        message = await self.wait_for_message(ctx.author, ctx.channel)
        if not message:
            return
        if len(message.content) > 2000:
            return await ctx.send("Message must be shorter than 2000 characters.")
        content = message.content

        await ctx.send(
            "What channel should I send the verification message in? Type `none` for me to create a new channel."
        )
        message = await self.wait_for_message(ctx.author, ctx.channel)
        if not message:
            return
        if message.channel_mentions:
            channel = message.channel_mentions[0]
        else:
            if is_int(message.content):
                channel = ctx.guild.get_channel(int(message.content))
            else:
                channel = None
            channel = channel or discord.utils.get(
                ctx.guild.channels, name=message.content
            )
            if not channel:
                return await ctx.send(
                    "I couldn't find that channel. Make sure I can see the channel."
                )

        await ctx.send(
            "What role should I give when verified? Type `none` for me to create a new role."
        )
        message = await self.wait_for_message(ctx.author, ctx.channel)
        if not message:
            return
        if message.role_mentions:
            role = message.role_mentions[0]
        else:
            if is_int(message.content):
                role = ctx.guild.get_role(int(message.content))
            else:
                role = None
            role = role or discord.utils.get(ctx.guild.roles, name=message.content)
            if not channel:
                return await ctx.send("I couldn't find that role.")

        await ctx.send(
            "What role should be allowed to confirm verifications? Type `none` for me to create a new role."
        )
        message = await self.wait_for_message(ctx.author, ctx.channel)
        if not message:
            return
        if message.role_mentions:
            verify_role = message.role_mentions[0]
        else:
            if is_int(message.content):
                verify_role = ctx.guild.get_role(int(message.content))
            else:
                verify_role = None
            verify_role = verify_role or discord.utils.get(
                ctx.guild.roles, name=message.content
            )
            if not channel:
                return await ctx.send("I couldn't find that role.")

        await ctx.send(
            "Alright, generating the verification system... You can move the `verifiers` channel wherever you like."
        )

        overwrites = {
            ctx.me: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                manage_messages=True,
                embed_links=True,
                read_message_history=True,
            ),
            ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            role: discord.PermissionOverwrite(read_messages=False),
            verify_role: discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            ),
        }

        notify_channel = await ctx.guild.create_text_channel(
            "verifiers", overwrites=overwrites
        )

        message = await channel.send(content)
        await message.add_reaction(GREEN_TICK)

        self.verifications[str(ctx.guild.id)] = {
            "message_id": message.id,
            "role_id": role.id,
            "verify_role_id": verify_role.id,
            "channel_id": notify_channel.id,
        }

        with open("verifications.json", "w") as f:
            json.dump(self.verifications, f)

    @verification.command(
        name="disable", description="Disable verification", aliases=["remove", "delete"]
    )
    @commands.guild_only()
    @has_manage_guild()
    @commands.is_owner()
    @commands.bot_has_permissions(
        manage_messages=True, manage_guild=True, manage_roles=True, manage_channels=True
    )
    async def ver_remove(self, ctx):
        if str(ctx.guild.id) not in self.verifications.keys():
            return await ctx.send(
                "**Verification is OFF** for this server. "
                f"Set it up with `{self.bot.guild_prefix(ctx.guild)}verification create`"
            )
        del self.verifications[str(ctx.guild.id)]
        with open("verifications.json", "w") as f:
            json.dump(
                self.verifications, f, sort_keys=True, indent=4, separators=(",", ": ")
            )
        await ctx.send("**Disabled verification on your server.**")

    @commands.Cog.listener("on_raw_reaction_add")
    async def verification_reaction(self, payload):
        if str(payload.guild_id) not in self.verifications.keys():
            return
        if (
            payload.message_id
            != self.verifications[str(payload.guild_id)]["message_id"]
        ):
            return
        if payload.user_id == self.bot.user.id:
            return

        channel = self.bot.get_channel(payload.channel_id)
        guild = self.bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        role = guild.get_role(int(self.verifications[str(guild.id)]["role_id"]))
        verify_role = guild.get_role(
            int(self.verifications[str(guild.id)]["verify_role_id"])
        )
        verify_channel = guild.get_channel(
            int(self.verifications[str(guild.id)]["channel_id"])
        )

        if guild.id in self.ver_messages.keys():
            message = self.ver_messages[guild.id]
        else:
            message = await channel.fetch_message(
                int(self.verifications[str(guild.id)]["message_id"])
            )
            self.ver_messages[guild.id] = message

        def check(reaction, user):
            return (
                reaction.message.id == bot_message.id
                and user != guild.me
                and verify_role in user.roles
                and str(reaction.emoji) in [GREEN_TICK, RED_TICK]
            )

        if (
            not guild
            or not channel
            or not role
            or not verify_channel
            or not verify_role
        ):
            del self.verifications[str(guild.id)]
            with open("verifications.json", "w") as f:
                json.dump(
                    self.verifications,
                    f,
                    sort_keys=True,
                    indent=4,
                    separators=(",", ": "),
                )
            return
        if str(payload.emoji) != GREEN_TICK:
            return
        await channel.send(
            "Your verification request is being processed by the moderators.",
            delete_after=10,
        )
        await message.remove_reaction(GREEN_TICK, member)

        bot_message = await verify_channel.send(
            f"**`{member}` is requesting verification!**\n\n"
            f"React with {GREEN_TICK} to verify them, or {RED_TICK} to ignore.\n"
            "If you don't respond within 24 hours, they will be ignored."
        )
        await bot_message.add_reaction(GREEN_TICK)
        await bot_message.add_reaction(RED_TICK)
        try:
            reaction, user = await self.bot.wait_for(
                "reaction_add", check=check, timeout=86400
            )  # 24h
        except asyncio.TimeoutError:
            pass

        emoji = str(reaction.emoji)
        if emoji == GREEN_TICK:
            await member.add_roles(role, reason="Verification")
            await bot_message.edit(
                content=f"**{GREEN_TICK} `{user}` accepted `{member}` into the server.**"
            )
        elif emoji == RED_TICK:
            await bot_message.edit(content=f"{RED_TICK} `{user}` ignored `{member}`")
        else:
            await bot_message.edit(
                content=f"**{RED_TICK} Timed out! Ignored `{member}`"
            )

        await bot_message.clear_reactions()

    @commands.command(
        name="purge",
        description="Purge messages in a channel",
        aliases=["cleanup"],
    )
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def purge_command(self, ctx, amount: int = 100):
        deleted = await ctx.channel.purge(limit=amount + 1)
        return await ctx.channel.send(
            f"{ctx.tick(True)} Deleted {len(deleted)} message(s)", delete_after=5
        )

    @commands.group(
        name="log",
        description="Keep a log of all user actions.",
        invoke_without_command=True,
    )
    @commands.is_owner()
    @commands.guild_only()
    @has_manage_guild()
    async def _log(self, ctx):
        log = self.get_log(ctx.guild.id)
        if not log:
            await self.enable(ctx)
        return await ctx.send(
            f"Server log at {log.mention}. Use `{self.bot.guild_prefix(ctx.guild.id)}log set` to change log channel."
        )

    @_log.command(description="Enable your server's log.")
    @commands.guild_only()
    @has_manage_guild()
    @commands.is_owner()
    async def enable(self, ctx):
        if self.get_log(ctx.guild.id):
            return await ctx.send("Log is already enabled!")
        await self._set(ctx)

    @_log.command(description="Disable your server's log.")
    @commands.guild_only()
    @has_manage_guild()
    @commands.is_owner()
    async def disable(self, ctx):
        if not self.get_log(str(ctx.guild.id)):
            await ctx.send("This server doesn't have a log.")
        self.log_channels.pop(str(ctx.guild.id))
        with open("log_channels.json", "w") as f:
            json.dump(
                self.log_channels, f, sort_keys=True, indent=4, separators=(",", ": ")
            )
        await ctx.send(f"**Log disabled**")

    @_log.command(
        name="set", description="Set your server's log channel.", aliases=["setup"]
    )
    @commands.guild_only()
    @has_manage_guild()
    @commands.is_owner()
    async def _set(self, ctx, channel: discord.TextChannel = None):
        if not channel:
            channel = ctx.channel
        if channel.guild.id != ctx.guild.id:
            return await ctx.send("You must specify a channel in this server.")
        self.log_channels[str(ctx.guild.id)] = channel.id
        with open("log_channels.json", "w") as f:
            json.dump(
                self.log_channels, f, sort_keys=True, indent=4, separators=(",", ": ")
            )
        await ctx.send(f"Log channel set to {channel.mention}")

    @commands.Cog.listener("on_message_delete")
    async def _deletion_detector(self, message):
        log = self.get_log(message.guild.id)
        if not log:
            return
        em = discord.Embed(
            title="Message Deletion",
            color=discord.Color.red(),
            timestamp=message.created_at,
        )
        em.set_author(name=str(message.author), icon_url=message.author.avatar_url)
        em.set_footer(text=f"Message sent at")
        em.description = f"In {message.channel.mention}:\n{message.content}"
        await log.send(embed=em)

    @commands.Cog.listener("on_message_edit")
    async def _edit_detector(self, before, after):
        if not before.guild:
            return
        log = self.get_log(before.guild.id)
        if not log or log.id == before.id or before.author.id == self.bot.user.id:
            return
        if before.content == after.content:
            return
        em = discord.Embed(
            title="Message Edit",
            color=discord.Color.blue(),
            timestamp=before.created_at,
        )
        em.set_author(name=str(before.author), icon_url=before.author.avatar_url)
        em.set_footer(text=f"Message sent at")
        em.description = (
            f"[Jump](https://www.discordapp.com/channels/{before.guild.id}/{before.channel.id}/{before.id})\n"
            f"In {before.channel.mention}:"
        )
        em.add_field(name="Before", value=before.content)
        em.add_field(name="After", value=after.content)
        await log.send(embed=em)

    @commands.Cog.listener("on_member_join")
    async def _join_message(self, member):
        log = self.get_log(member.guild.id)
        if not log:
            return
        em = discord.Embed(
            title="Member Join",
            description=f"**{member.mention} joined the server!**",
            color=discord.Color.green(),
            timestamp=d.utcnow(),
        )
        em.set_thumbnail(url=member.avatar_url)
        await log.send(embed=em)

    @commands.Cog.listener("on_member_remove")
    async def _remove_message(self, user):
        log = self.get_log(user.guild.id)
        if not log:
            return
        em = discord.Embed(
            title="Member Left",
            description=f"**{user.mention} left the server**",
            color=discord.Color.red(),
            timestamp=d.utcnow(),
        )
        em.set_thumbnail(url=user.avatar_url)
        await log.send(embed=em)


def setup(bot):
    bot.add_cog(Moderation(bot))
