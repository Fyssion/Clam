from discord.ext import commands
from discord.flags import BaseFlags, fill_with_flags, flag_value
import discord

import datetime
import io
import json
import typing
from datetime import datetime as d
import asyncio
import re
from urllib.parse import urlparse
from async_timeout import timeout

from .utils import db, human_time
from .utils.emojis import GREEN_TICK, RED_TICK, LOADING
from .utils.checks import has_manage_guild
from .utils.utils import is_int


class GuildSettingsTable(db.Table, table_name="guild_settings"):
    id = db.PrimaryKeyColumn()
    guild_id = db.Column(db.Integer(big=True))
    mute_role_id = db.Column(db.Integer(big=True))
    muted_members = db.Column(db.Array(db.Integer(big=True)))


class GuildSettings:
    @classmethod
    def from_record(cls, record, bot):
        self = cls()

        self.bot = bot

        self.id = record["id"]
        self.guild_id = record["guild_id"]
        self.mute_role_id = record["mute_role_id"]
        self.muted_members = record["muted_members"]

        return self

    @property
    def mute_role(self):
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return None

        return guild.get_role(self.mute_role_id)

    async def mute_member(self, member, reason, *, execute_db=True):
        if execute_db:
            query = """UPDATE guild_settings
                       SET muted_members=$1
                       WHERE guild_id=$2;
                    """

            self.muted_members.append(member.id)

            await self.bot.pool.execute(query, self.muted_members, member.guild.id)

        role = self.mute_role
        await member.add_roles(role, reason=reason)

    async def unmute_member(self, member, reason, *, execute_db=True):
        if execute_db:
            query = """UPDATE guild_settings
                       SET muted_members=$1
                       WHERE guild_id=$2;
                    """

            self.muted_members.pop(self.muted_members.index(member.id))

            await self.bot.pool.execute(query, self.muted_members, member.guild.id)

        role = self.mute_role
        await member.remove_roles(role, reason=reason)


# @fill_with_flags()
# class LoggingFlags(BaseFlags):
#     """Describes what to log"""

#     @flag_value
#     def message_edit(self):
#         return 1 << 0

#     @flag_value
#     def message_delete(self):
#         return 1 << 1

#     @flag_value
#     def guild_join(self):
#         return 1 << 2

#     @flag_value
#     def guild_leave(self):
#         return 1 << 3


# class Logs(db.Table):
#     id = db.PrimaryKeyColumn()
#     guild_id = db.Column(db.Integer(big=True), index=True)


class WelcomeContent:
    def __init__(self, original, messages):
        self.original = original
        self.messages = messages


class BinConverter(commands.Converter):
    async def get_bin(self, ctx, url):
        parsed = urlparse(url)
        newpath = "/raw" + parsed.path
        url = parsed.scheme + "://" + parsed.netloc + newpath

        try:
            async with ctx.bot.session.get(url, timeout=10) as resp:
                f = await resp.read()
                f = f.decode("utf-8")
                return f

        except asyncio.TimeoutError:
            raise commands.BadArgument("Could not fetch data from specified url.")

    async def convert(self, ctx, arg):
        # Attempt to parse the argument by optionally
        # fetching from a bin and then parsing the content

        if arg.startswith("http"):
            return await self.get_bin(ctx, arg)

        else:
            return arg


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


class NoMuteRole(commands.CommandError):
    def __init__(self):
        super().__init__("A mute role for this server has not been set up.")


class RoleHierarchyFailure(commands.CommandError):
    pass


def role_hierarchy_check(ctx, user, target):
    return (
        user.id == ctx.bot.owner_id
        or user == ctx.guild.owner
        or (user.top_role > target.top_role and ctx.guild.owner != target)
    )


def can_mute():
    async def predicate(ctx):
        if ctx.author.id != ctx.bot.owner_id:
            await commands.has_permissions(manage_roles=True).predicate(ctx)

        await commands.bot_has_permissions(manage_roles=True).predicate(ctx)

        settings = await ctx.cog.get_guild_settings(
            ctx.guild.id, create_if_not_found=True
        )

        role = settings.mute_role
        if not role:
            raise NoMuteRole()

        if ctx.guild.me.top_role < role:
            raise RoleHierarchyFailure("The bot's role is lower than the mute role.")

        if ctx.author.id != ctx.bot.owner_id and ctx.author.top_role < role:
            raise RoleHierarchyFailure("Your role is lower than the mute role.")

        return True

    return commands.check(predicate)


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

    async def cog_command_error(self, ctx, error):
        if isinstance(error, NoMuteRole) or isinstance(error, RoleHierarchyFailure):
            await ctx.send(f"{ctx.tick(False)} {error}")
            ctx.handled = True

    async def create_guild_settings(self, guild_id):
        query = """INSERT INTO guild_settings (guild_id, mute_role_id, muted_members)
                   VALUES ($1, $2, $3)
                   RETURNING id;
                """

        record = await self.bot.pool.fetchrow(query, guild_id, None, [])
        record = {
            "id": record[0],
            "guild_id": guild_id,
            "mute_role_id": None,
            "muted_members": [],
        }
        return GuildSettings.from_record(record, self.bot)

    async def get_guild_settings(self, guild_id, *, create_if_not_found=False):
        query = """SELECT *
                   FROM guild_settings
                   WHERE guild_id=$1;
                """

        record = await self.bot.pool.fetchrow(query, guild_id)

        if not record:
            if create_if_not_found:
                return await self.create_guild_settings(guild_id)
            return None

        return GuildSettings.from_record(record, self.bot)

    @commands.command()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(
        self, ctx, user: typing.Union[discord.Member, discord.User, int], *, reason=None
    ):
        if isinstance(user, discord.Member) and not role_hierarchy_check(
            ctx, ctx.author, user
        ):
            return await ctx.send(
                "You can't preform this action due to role hierarchy."
            )

        if isinstance(user, discord.User):
            user_id = user.id
            human_friendly = f"`{str(user)}`"
        else:
            user_id = user
            human_friendly = f"with an ID of `{user_id}`"

        to_be_banned = discord.Object(id=user_id)
        reason = f"Ban by {ctx.author} (ID: {ctx.author.id}) with reason: {reason}"

        try:
            await ctx.guild.ban(to_be_banned, reason=reason)
        except discord.HTTPException:
            return await ctx.send(f"{ctx.tick(False)} I couldn't ban that user.")

        await ctx.send(f"{ctx.tick(True)} Banned user {human_friendly}")

    @commands.command(description="Temporarily ban a user")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def tempban(
        self,
        ctx,
        user: typing.Union[discord.Member, discord.User, int],
        duration: human_time.FutureTime,
        *,
        reason=None,
    ):
        timers = self.bot.get_cog("Timers")
        if not timers:
            return await ctx.send(
                "Sorry, that functionality isn't available right now. Try again later."
            )

        if isinstance(user, discord.Member) and not role_hierarchy_check(
            ctx, ctx.author, user
        ):
            return await ctx.send(
                "You can't preform this action due to role hierarchy."
            )

        if isinstance(user, discord.User):
            if not role_hierarchy_check(ctx, ctx.author, user):
                return await ctx.send(
                    "You can't preform this action due to role hierarchy."
                )
            user_id = user.id
            human_friendly = f"`{str(user)}`"
        else:
            user_id = user
            human_friendly = f"with an ID of `{user_id}`"

        to_be_banned = discord.Object(id=user_id)

        friendly_time = human_time.human_timedelta(duration.dt)
        reason = f"Tempban by {ctx.author} (ID: {ctx.author.id}) for {friendly_time} with reason: {reason}"

        try:
            await ctx.guild.ban(to_be_banned, reason=reason)
            timer = await timers.create_timer(
                duration.dt, "tempban", ctx.guild.id, ctx.author.id, user_id
            )

        except discord.HTTPException:
            return await ctx.send(f"{ctx.tick(False)} I couldn't ban that user.")

        except Exception:
            await ctx.guild.unban(
                to_be_banned, reason="Timer creation failed for previous tempban."
            )
            raise

        friendly_time = human_time.human_timedelta(duration.dt, source=timer.created_at)
        await ctx.send(
            f"{ctx.tick(True)} Banned user {human_friendly} for `{friendly_time}`."
        )

    @commands.Cog.listener()
    async def on_tempban_timer_complete(self, timer):
        guild_id, mod_id, user_id = timer.args

        guild = self.bot.get_guild(guild_id)

        if not guild:
            return

        mod = guild.get_member(mod_id)
        mod = f"{mod} (ID: {mod.id})" if mod else f"mod with ID {mod_id}"

        reason = (
            f"Automatic unban from tempban command. Command orignally invoked by {mod}"
        )
        await guild.unban(discord.Object(id=user_id), reason=reason)

    @commands.command()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx, user: BannedUser, *, reason=None):
        to_be_unbanned = discord.Object(id=user.id)
        reason = f"Unban by {ctx.author} (ID: {ctx.author.id}) with reason: {reason}"

        try:
            await ctx.guild.unban(to_be_unbanned, reason=reason)
        except discord.HTTPException:
            return await ctx.send(f"{ctx.tick(False)} I couldn't unban that user.")

        await ctx.send(f"{ctx.tick(True)} Unbanned user `{user}`")

    @commands.command()
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx, user: discord.Member, *, reason=None):
        if not role_hierarchy_check(ctx, ctx.author, user):
            return await ctx.send(
                "You can't preform this action due to role hierarchy."
            )

        reason = f"Kick by {ctx.author} (ID: {ctx.author.id}) with reason: {reason}"

        try:
            await ctx.guild.kick(user, reason=reason)
        except discord.HTTPException:
            return await ctx.send(f"{ctx.tick(False)} I couldn't kick that user.")

        await ctx.send(f"{ctx.tick(True)} Kicked user `{user}`")

    def get_log(self, guild):
        if str(guild) in self.log_channels.keys():
            channel_id = self.log_channels.get(str(guild))
            return self.bot.get_channel(int(channel_id))
        return None

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        settings = await self.get_guild_settings(role.guild.id)

        if role.id != settings.muted_role_id:
            return

        query = """UPDATE guild_settings
                   SET muted_role_id=$1, muted_members=$2
                   WHERE guild_id=$3;
                """

        await self.bot.pool.execute(query, None, [], role.guild.id)

    @commands.Cog.listener("on_member_update")
    async def mute_role_check(self, before, after):
        if before.roles == after.roles:
            return

        settings = await self.get_guild_settings(before.guild.id)

        if not settings:
            return

        role = settings.mute_role

        if not role:
            return

        if (
            role in before.roles
            and role not in after.roles
            and before.id in settings.muted_members
        ):
            settings.muted_members.pop(settings.muted_members.index(before.id))

        elif (
            role not in before.roles
            and role in after.roles
            and before.id not in settings.muted_members
        ):
            settings.muted_members.append(before.id)

        query = """UPDATE guild_settings
                   SET muted_members=$1
                   WHERE guild_id=$2;

                """
        await self.bot.pool.execute(query, settings.muted_members, before.guild.id)

    @commands.Cog.listener("on_member_join")
    async def mute_role_retain(self, member):
        settings = await self.get_guild_settings(member.guild.id)

        if not settings:
            return

        if not settings.mute_role:
            return

        if member.id in settings.muted_members:
            await member.add_roles(
                settings.mute_role,
                reason="Member who rejoined server was muted before they left",
            )

    @commands.group(invoke_without_command=True)
    @can_mute()
    async def mute(self, ctx, member: discord.Member, *, reason=None):
        if not role_hierarchy_check(ctx, ctx.author, member):
            return await ctx.send(
                "You can't preform this action due to role hierarchy."
            )

        settings = await self.get_guild_settings(ctx.guild.id)

        if member.id in settings.muted_members:
            return await ctx.send(f"{ctx.tick(False)} That member is already muted")

        reason = f"Mute by {ctx.author} (ID: {ctx.author.id}) with reason: {reason}"
        await settings.mute_member(member, reason)

        await ctx.send(f"{ctx.tick(True)} Muted `{member}`")

    @commands.command()
    @can_mute()
    async def unmute(
        self, ctx, member: typing.Union[discord.Member, int], *, reason=None
    ):
        if isinstance(member, discord.Member) and not role_hierarchy_check(
            ctx, ctx.author, member
        ):
            return await ctx.send(
                "You can't preform this action due to role hierarchy."
            )

        settings = await self.get_guild_settings(ctx.guild.id)

        if isinstance(member, int):
            if member not in settings.muted_members:
                return await ctx.send(f"{ctx.tick(False)} That user isn't muted")

            settings.muted_members.pop(settings.muted_members.index(member))
            query = """UPDATE guild_settings
                       SET muted_members=$1
                       WHERE guild_id=$2;
                    """
            await ctx.db.execute(query, settings.muted_members, ctx.guild.id)
            return await ctx.send(f"{ctx.tick(True)} Unmuted user with ID `{member}`")

        if member.id not in settings.muted_members:
            return await ctx.send(f"{ctx.tick(False)} That member isn't muted")

        reason = f"Unmute by {ctx.author} (ID: {ctx.author.id}) with reason: {reason}"
        await settings.unmute_member(member, reason)

        await ctx.send(f"{ctx.tick(True)} Unmuted `{member}`")

    @commands.command()
    @can_mute()
    async def tempmute(
        self,
        ctx,
        member: discord.Member,
        duration: human_time.FutureTime,
        *,
        reason=None,
    ):
        timers = self.bot.get_cog("Timers")
        if not timers:
            return await ctx.send(
                "Sorry, that functionality isn't available right now. Try again later."
            )

        if not role_hierarchy_check(ctx, ctx.author, member):
            return await ctx.send(
                "You can't preform this action due to role hierarchy."
            )

        settings = await self.get_guild_settings(ctx.guild.id)
        role = settings.mute_role

        execute_db = False if member.id in settings.muted_members else True

        friendly_time = human_time.human_timedelta(
            duration.dt, source=ctx.message.created_at
        )
        reason = f"Tempmute by {ctx.author} (ID: {ctx.author.id}) for {friendly_time} with reason: {reason}"

        try:
            await settings.mute_member(member, reason, execute_db=execute_db)
            timer = await timers.create_timer(
                duration.dt, "tempmute", ctx.guild.id, role.id, ctx.author.id, member.id
            )

        except Exception:
            await settings.unmute_member(
                member, reason="Timer creation failed for previous tempmute."
            )
            raise

        await ctx.send(f"{ctx.tick(True)} Muted `{member}` for `{friendly_time}`.")

    @commands.Cog.listener()
    async def on_tempmute_timer_complete(self, timer):
        guild_id, role_id, mod_id, member_id = timer.args

        settings = await self.get_guild_settings(guild_id)

        query = """UPDATE guild_settings
                   SET muted_members=$1
                   WHERE guild_id=$2;
                """
        if member_id in settings.muted_members:
            settings.muted_members.pop(settings.muted_members.index(member_id))
            await self.bot.pool.execute(query, settings.muted_members, guild_id)

        guild = self.bot.get_guild(guild_id)

        if not guild:
            return

        mod = guild.get_member(mod_id)
        mod = f"{mod} (ID: {mod.id})" if mod else f"mod with ID {mod_id}"
        role = guild.get_role(role_id)
        if not role:
            return

        member = guild.get_member(member_id)
        if not member:
            return

        reason = f"Automatic unmute from mute timer. Command orignally invoked by {mod}"
        await settings.unmute_member(member, reason=reason, execute_db=False)

    @commands.command()
    @commands.guild_only()
    async def selfmute(self, ctx, duration: human_time.ShortTime, *, reason=None):
        timers = self.bot.get_cog("Timers")
        if not timers:
            return await ctx.send(
                "Sorry, that functionality isn't available right now. Try again later."
            )

        created_at = ctx.message.created_at
        if duration.dt > (created_at + datetime.timedelta(days=1)):
            raise commands.BadArgument("Duration cannot be more than 24 hours.")

        if duration.dt < (created_at + datetime.timedelta(minutes=5)):
            raise commands.BadArgument("Duration cannot be less than 5 minutes.")

        human_friendly = human_time.human_timedelta(
            duration.dt, source=ctx.message.created_at
        )
        confirm = await ctx.confirm(
            f"Are you sure you want to mute yourself for {human_friendly}?\n"
            "You won't be able to unmute yourself unless you ask a mod."
        )

        if not confirm:
            return await ctx.send("Aborted selfmute")

        settings = await self.get_guild_settings(ctx.guild.id)

        if not settings:
            raise NoMuteRole()

        role = settings.mute_role

        if not role:
            raise NoMuteRole()

        if role in ctx.author.roles:
            return await ctx.send("You've already been muted.")

        execute_db = False if ctx.author.id in settings.muted_members else True

        reason = f"Selfmute by {ctx.author} (ID: {ctx.author.id}) for {human_friendly} with reason: {reason}"

        try:
            await settings.mute_member(ctx.author, reason, execute_db=execute_db)
            timer = await timers.create_timer(
                duration.dt,
                "tempmute",
                ctx.guild.id,
                role.id,
                ctx.author.id,
                ctx.author.id,
            )

        except Exception:
            await settings.unmute_member(
                ctx.author, reason="Timer creation failed for previous selfmute."
            )
            raise

        await ctx.send(
            f"{ctx.tick(True)} You have been muted for `{human_friendly}`.\n"
            "Don't bug anyone about it!"
        )

    @commands.command(description="View members with the muted role")
    @commands.has_permissions(manage_roles=True)
    async def muted(self, ctx):
        settings = await self.get_guild_settings(ctx.guild.id)

        if not settings:
            raise NoMuteRole()

        role = settings.mute_role

        if not role:
            raise NoMuteRole()

        if not settings.muted_members:
            return await ctx.send("No muted members.")

        members = []
        for member_id in settings.muted_members:
            member = ctx.guild.get_member(member_id)

            if not member:
                members.append(f"User with ID {member_id}")
            else:
                members.append(f"{member} (ID: {member.id})")

        pages = ctx.pages(members, title="Muted Members")
        await pages.start(ctx)

    @mute.group(name="role", invoke_without_command=True)
    async def mute_role(self, ctx):
        settings = await self.get_guild_settings(ctx.guild.id)
        if not settings.mute_role:
            return await ctx.send("No mute role has been set for this server.")

        return await ctx.send(f"This server's mute role is **`{settings.mute_role}`**")

    @mute_role.command(name="set", description="Set an existing mute role")
    @commands.bot_has_permissions(manage_roles=True)
    @commands.has_permissions(manage_roles=True)
    async def mute_role_set(self, ctx, *, role: discord.Role):
        query = """UPDATE guild_settings
                   SET mute_role_id=$1, muted_members=$2
                   WHERE guild_id=$3;
                """

        await ctx.db.execute(query, role.id, [], ctx.guild.id)

        await ctx.send(f"{ctx.tick(True)} Set mute role to **`{role}`**")

    @mute_role.command(
        name="create",
        description="Create a new mute role and change channel overwrites",
    )
    @commands.bot_has_permissions(manage_channels=True, manage_roles=True)
    @commands.has_permissions(manage_channels=True, manage_roles=True)
    async def mute_role_create(
        self, ctx, name="Muted", *, color: discord.Color = discord.Color.dark_grey()
    ):
        settings = await self.get_guild_settings(ctx.guild.id, create_if_not_found=True)

        guild = ctx.guild
        reason = f"Creation of Muted role by {ctx.author} (ID: {ctx.author.id})"

        role = await guild.create_role(name=name, color=color, reason=reason)

        channels_to_update = [c for c in guild.text_channels]
        channels_to_update.extend(c for c in guild.categories)

        succeeded = 0
        failed = []

        for channel in channels_to_update:
            overwrites = channel.overwrites
            overwrites[role] = discord.PermissionOverwrite(
                send_messages=False, add_reactions=False
            )
            try:
                await channel.edit(overwrites=overwrites, reason=reason)
                succeeded += 1

            except discord.HTTPException:
                failed.append(
                    channel.mention
                    if isinstance(channel, discord.TextChannel)
                    else channel.name
                )

        query = """UPDATE guild_settings
                   SET mute_role_id=$1
                   WHERE guild_id=$2;
                """

        await ctx.db.execute(query, role.id, ctx.guild.id)

        message = (
            "Created mute role and changed channel overwrites.\n"
            f"Attempted to change {len(channels_to_update)} channels:"
            f"\n  - {succeeded} succeeded\n  - {len(failed)} failed"
        )

        if failed:
            formatted = ", ".join(failed)
            message += f"\n\nChannels failed: {formatted}"

        await ctx.send(message)

    @mute_role.command(
        name="unbind", description="Unbind the current mute role without deleting it"
    )
    @can_mute()
    async def mute_role_unbind(self, ctx):
        settings = await self.get_guild_settings(ctx.guild.id)

        query = """UPDATE guild_settings
                   SET mute_role_id=$1, muted_members=$2
                   WHERE guild_id=$3;
                """

        await ctx.db.execute(query, None, [], ctx.guild.id)

        await ctx.send(f"{ctx.tick(True)} Unbound mute role")

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

    async def get_attachment(self, ctx, url, index):
        if not url.endswith((".jpg", ".gif", ".png")):
            raise commands.BadArgument(f"Attachment URL (`{url}`) must end in `.jpg`, `.png`, or `.gif`.")

        ending = url[-3:]

        async with ctx.bot.session.get(url) as resp:
            buffer = io.BytesIO(await resp.read())
            file = discord.File(buffer, f"attachment{index}.{ending}")
            return file

    @commands.command(
        name="welcome-message",
        description="Create a server info message for your server.",
        hidden=True,
    )
    @commands.guild_only()
    @has_manage_guild()
    @commands.is_owner()
    async def welcome_message(
        self, ctx, channel: discord.TextChannel, *, content: BinConverter
    ):
        """Send a welcome or about message to a channel

        Please note that this will purge the specified channel of all it's messages.

        Formatting guide:
        - $$BREAK$$ | Split the content before and after this point into two messages
        - $$ATTACHMENT=attachment_url$$ | Add an image or attachment to the message at this point
        """
        done = ctx.tick(True)
        loading = LOADING

        tasks = {
            "Prepare content": done,
            "Split content into messages": loading,
            "Find attachments": loading,
            f"Send messages to {channel.mention}": loading
        }

        def format_tasks():
            return "\n\n".join(f"{v} {k}" for k, v in tasks.items())

        progress_message = await ctx.send(format_tasks())

        if len(content) > 2000:
            if "$$BREAK$$" not in content:
                raise commands.BadArgument(
                    "That message is too long, and I couldn't find any message breaks in it.\n"
                    "Add message breaks with they keyword `$$BREAK$$`, and I will split the message there."
                    )
            all_contents = content.split("$$BREAK$$")
            all_contents = [c.strip() for c in all_contents]
        else:
            all_contents = content.split("$$BREAK$$")
            all_contents = [c.strip() for c in all_contents]

        tasks["Split content into messages"] = done
        await progress_message.edit(content=format_tasks())

        attachment_regex = re.compile(r"\$\$ATTACHMENT=(.*)\$\$")

        contents = []
        for i, message in enumerate(all_contents):
            urls = attachment_regex.findall(message)

            if not urls:
                contents.append(message)

            else:
                for i, url in enumerate(urls):
                    attachment = await self.get_attachment(ctx, url, i)

                    full_str = f"$$ATTACHMENT={url}$$"
                    start = message.find(full_str)
                    before = message[:start].strip()
                    contents.append(before)
                    message = message[start+len(full_str):].strip()
                    contents.append(attachment)

                contents.append(message)

        tasks["Find attachments"] = done
        await progress_message.edit(content=format_tasks())

        messages = []
        for entity in contents:

            if isinstance(entity, discord.File):
                messages.append({"file": entity})
            elif entity:
                messages.append({"content": entity.strip()})

        content = WelcomeContent(content, messages)

        await channel.purge()
        for message in content.messages:
            await channel.send(**message)

        tasks[f"Send messages to {channel.mention}"] = done
        await progress_message.edit(content=format_tasks())

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
        name="purge", description="Purge messages in a channel", aliases=["cleanup"],
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
