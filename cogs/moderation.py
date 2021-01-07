from discord.ext import commands, flags, tasks
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
from collections import Counter, defaultdict
import enum
import os.path
from jishaku.models import copy_context_with
import asyncpg
import logging

from .utils import db, humantime, checks, cache
from .utils.emojis import GREEN_TICK, RED_TICK, LOADING
from .utils.checks import has_manage_guild
from .utils.utils import is_int
from .utils.flags import NoUsageFlagGroup


log = logging.getLogger("clam.mod")


class RaidMode(enum.Enum):
    off = 0
    on = 1
    strict = 2

    def __str__(self):
        return self.name


class GuildSettingsTable(db.Table, table_name="guild_settings"):
    id = db.Column(db.Integer(big=True), primary_key=True)

    mute_role_id = db.Column(db.Integer(big=True))
    muted_members = db.Column(db.Array(db.Integer(big=True)))
    raid_mode = db.Column(db.Integer(small=True))
    broadcast_channel = db.Column(db.Integer(big=True))
    mention_count = db.Column(db.Integer(small=True))
    safe_mention_channel_ids = db.Column(db.Array(db.Integer(big=True)))


class GuildSettings:
    @classmethod
    def from_record(cls, record, bot):
        self = cls()

        self.bot = bot

        self.id = record["id"]
        self.mute_role_id = record["mute_role_id"]
        self.muted_members = record["muted_members"] or []
        self.raid_mode = record["raid_mode"]
        self.id = record["id"]
        self.broadcast_channel_id = record["broadcast_channel"]
        self.mention_count = record["mention_count"]
        self.safe_mention_channel_ids = set(record["safe_mention_channel_ids"] or [])

        return self

    @property
    def broadcast_channel(self):
        guild = self.bot.get_guild(self.id)
        return guild and guild.get_channel(self.broadcast_channel_id)

    @property
    def mute_role(self):
        guild = self.bot.get_guild(self.id)
        if not guild:
            return None

        return guild.get_role(self.mute_role_id)

    async def mute_member(self, member, reason, *, execute_db=True):
        if execute_db:
            query = """UPDATE guild_settings
                       SET muted_members=$1
                       WHERE id=$2;
                    """

            self.muted_members.append(member.id)

            await self.bot.pool.execute(query, self.muted_members, member.guild.id)

        role = self.mute_role
        await member.add_roles(role, reason=reason)

    async def unmute_member(self, member, reason, *, execute_db=True):
        if execute_db:
            query = """UPDATE guild_settings
                       SET muted_members=$1
                       WHERE id=$2;
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

        banned_users = [b.user for b in bans]

        user = discord.utils.get(banned_users, name=arg)

        if user:
            return user

        for ban_entry in bans:

            if arg.startswith(ban_entry.user.name):
                user = ban_entry.user
                break

        if user:
            return user

        try:
            arg = int(arg)
        except ValueError:
            raise commands.BadArgument(f'Banned user "{arg}" not found.')

        user = discord.utils.get(banned_users, id=arg)

        if user:
            return user

        raise commands.BadArgument(f'Banned user "{arg}" not found.')


# Spam detector

# TODO: add this to d.py maybe
class CooldownByContent(commands.CooldownMapping):
    def _bucket_key(self, message):
        return (message.channel.id, message.content)


class SpamChecker:
    """This spam checker does a few things.

    1) It checks if a user has spammed more than 10 times in 12 seconds
    2) It checks if the content has been spammed 15 times in 17 seconds.
    3) It checks if new users have spammed 30 times in 35 seconds.
    4) It checks if "fast joiners" have spammed 10 times in 12 seconds.

    The second case is meant to catch alternating spam bots while the first one
    just catches regular singular spam bots.

    From experience these values aren't reached unless someone is actively spamming.
    """

    def __init__(self):
        self.by_content = CooldownByContent.from_cooldown(
            15, 17.0, commands.BucketType.member
        )
        self.by_user = commands.CooldownMapping.from_cooldown(
            10, 12.0, commands.BucketType.user
        )
        self.last_join = None
        self.new_user = commands.CooldownMapping.from_cooldown(
            30, 35.0, commands.BucketType.channel
        )

        # user_id flag mapping (for about 30 minutes)
        self.fast_joiners = cache.ExpiringCache(seconds=1800.0)
        self.hit_and_run = commands.CooldownMapping.from_cooldown(
            10, 12, commands.BucketType.channel
        )

    def is_new(self, member):
        now = datetime.datetime.utcnow()
        seven_days_ago = now - datetime.timedelta(days=7)
        ninety_days_ago = now - datetime.timedelta(days=90)
        return member.created_at > ninety_days_ago and member.joined_at > seven_days_ago

    def is_spamming(self, message):
        if message.guild is None:
            return False

        current = message.created_at.replace(tzinfo=datetime.timezone.utc).timestamp()

        if message.author.id in self.fast_joiners:
            bucket = self.hit_and_run.get_bucket(message)
            if bucket.update_rate_limit(current):
                return True

        if self.is_new(message.author):
            new_bucket = self.new_user.get_bucket(message)
            if new_bucket.update_rate_limit(current):
                return True

        user_bucket = self.by_user.get_bucket(message)
        if user_bucket.update_rate_limit(current):
            return True

        content_bucket = self.by_content.get_bucket(message)
        if content_bucket.update_rate_limit(current):
            return True

        return False

    def is_fast_join(self, member):
        joined = member.joined_at or datetime.datetime.utcnow()
        if self.last_join is None:
            self.last_join = joined
            return False
        is_fast = (joined - self.last_join).total_seconds() <= 2.0
        self.last_join = joined
        if is_fast:
            self.fast_joiners[member.id] = True
        return is_fast


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

        settings = await ctx.cog.get_guild_settings(ctx.guild.id)

        if not settings:
            raise NoMuteRole()

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
    Moderation commands that help you moderate your server.
    If you are looking for raid protection, see the `Raid Shield` category.
    """

    def __init__(self, bot):
        self.bot = bot
        self.emoji = ":police_car:"
        self.log = log

        if not os.path.isfile("log_channels.json"):
            self.log.info("log_channels.json not found, creating...")
            with open("log_channels.json", "w") as f:
                json.dump({}, f)

        with open("log_channels.json", "r") as f:
            self.log_channels = json.load(f)

        if not os.path.isfile("verifications.json"):
            self.log.info("verifications.json not found, creating...")
            with open("verifications.json", "w") as f:
                json.dump({}, f)

        with open("verifications.json", "r") as f:
            self.verifications = json.load(f)

        self.ver_messages = {}

        # Raid shield stuff

        # guild_id: SpamChecker
        self._spam_check = defaultdict(SpamChecker)

        # guild_id: List[(member_id, insertion)]
        # A batch of data for bulk inserting mute role changes
        # True - insert, False - remove
        self._data_batch = defaultdict(list)
        self._batch_lock = asyncio.Lock(loop=bot.loop)
        self._disable_lock = asyncio.Lock(loop=bot.loop)
        self.batch_updates.add_exception_type(asyncpg.PostgresConnectionError)
        self.batch_updates.start()

        # (guild_id, channel_id): List[str]
        # A batch list of message content for message
        self.message_batches = defaultdict(list)
        self._batch_message_lock = asyncio.Lock(loop=bot.loop)
        self.bulk_send_messages.start()

    def cog_unload(self):
        self.batch_updates.stop()
        self.bulk_send_messages.stop()

    async def cog_command_error(self, ctx, error):
        if isinstance(error, NoMuteRole) or isinstance(error, RoleHierarchyFailure):
            await ctx.send(f"{ctx.tick(False)} {error}")
            ctx.handled = True

        elif isinstance(error, commands.CommandInvokeError):
            original = error.original
            if isinstance(original, discord.Forbidden):
                await ctx.send("I do not have permission to execute this action.")
            elif isinstance(original, discord.NotFound):
                await ctx.send(f"This entity does not exist: {original.text}")
            elif isinstance(original, discord.HTTPException):
                await ctx.send(
                    "Somehow, an unexpected error occurred. Try again later?"
                )
            ctx.handled = True

    @commands.command(aliases=["su"])
    @checks.has_permissions(administrator=True)
    async def runas(self, ctx, target: discord.Member, *, command):
        """
        Run a command as someone else.
        You must have the administrator permission, and you cannot run
        a command as someone with a higher role than you.
        """
        if target.id == self.bot.owner_id:
            raise commands.BadArgument(
                "You cannot run commands as the owner of the bot."
            )

        if not role_hierarchy_check(ctx, ctx.author, target):
            raise commands.BadArgument(
                "You can only run commands as members with roles lower than yours."
            )

        alt_ctx = await copy_context_with(
            ctx, author=target, content=ctx.prefix + command
        )

        if alt_ctx.command is None:
            if alt_ctx.invoked_with is None:
                return await ctx.send(
                    "This bot has been hard-configured to ignore this user."
                )
            return await ctx.send(f'Command "{alt_ctx.invoked_with}" is not found')

        if await self.bot.can_run(alt_ctx, call_once=True):
            await alt_ctx.command.invoke(alt_ctx)
        else:
            raise commands.CheckFailure("The global check once functions failed.")

    @cache.cache()
    async def get_guild_settings(self, guild_id):
        query = """SELECT * FROM guild_settings WHERE id=$1;"""
        record = await self.bot.pool.fetchrow(query, guild_id)
        if record is not None:
            return GuildSettings.from_record(record, self.bot)
        return None

    @commands.command()
    @checks.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(
        self,
        ctx,
        user: typing.Union[discord.Member, discord.User, int, str],
        *,
        reason=None,
    ):
        if type(user) == str:
            raise commands.BadArgument(
                f'User "{user}" not found.'
                "\nIf they aren't in the server, try banning by ID instead."
            )

        if isinstance(user, discord.Member) and not role_hierarchy_check(
            ctx, ctx.author, user
        ):
            return await ctx.send(
                "You can't preform this action due to role hierarchy."
            )

        if isinstance(user, discord.User) or isinstance(user, discord.Member):
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
    @checks.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def tempban(
        self,
        ctx,
        user: typing.Union[discord.Member, discord.User, int],
        duration: humantime.FutureTime,
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

        friendly_time = humantime.timedelta(duration.dt)
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

        friendly_time = humantime.timedelta(duration.dt, source=timer.created_at)
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
    @checks.has_permissions(ban_members=True)
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
    @checks.has_permissions(kick_members=True)
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

        if settings and role.id != settings.mute_role_id:
            return

        query = """UPDATE guild_settings
                   SET mute_role_id=$1, muted_members=$2
                   WHERE id=$3;
                """

        await self.bot.pool.execute(query, None, [], role.guild.id)
        self.get_guild_settings.invalidate(self, role.guild.id)

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
                   WHERE id=$2;

                """
        await self.bot.pool.execute(query, settings.muted_members, before.guild.id)
        self.get_guild_settings.invalidate(self, before.guild.id)

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

        if not role_hierarchy_check(ctx, ctx.guild.me, member):
            return await ctx.send(
                "The bot can't preform this action due to role hierarchy."
            )

        settings = await self.get_guild_settings(ctx.guild.id)

        if member.id in settings.muted_members:
            return await ctx.send(f"{ctx.tick(False)} That member is already muted")

        reason = f"Mute by {ctx.author} (ID: {ctx.author.id}) with reason: {reason}"
        await settings.mute_member(member, reason)
        self.get_guild_settings.invalidate(self, ctx.guild.id)

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
                       WHERE id=$2;
                    """
            await ctx.db.execute(query, settings.muted_members, ctx.guild.id)
            self.get_guild_settings.invalidate(self, ctx.guild.id)
            return await ctx.send(f"{ctx.tick(True)} Unmuted user with ID `{member}`")

        if member.id not in settings.muted_members:
            return await ctx.send(f"{ctx.tick(False)} That member isn't muted")

        reason = f"Unmute by {ctx.author} (ID: {ctx.author.id}) with reason: {reason}"
        await settings.unmute_member(member, reason)
        self.get_guild_settings.invalidate(self, ctx.guild.id)

        await ctx.send(f"{ctx.tick(True)} Unmuted `{member}`")

    @commands.command()
    @can_mute()
    async def tempmute(
        self,
        ctx,
        member: discord.Member,
        duration: humantime.FutureTime,
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

        friendly_time = humantime.timedelta(
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

        self.get_guild_settings.invalidate(self, ctx.guild.id)

        await ctx.send(f"{ctx.tick(True)} Muted `{member}` for `{friendly_time}`.")

    @commands.Cog.listener()
    async def on_tempmute_timer_complete(self, timer):
        guild_id, role_id, mod_id, member_id = timer.args

        settings = await self.get_guild_settings(guild_id)

        query = """UPDATE guild_settings
                   SET muted_members=$1
                   WHERE id=$2;
                """
        if member_id in settings.muted_members:
            settings.muted_members.pop(settings.muted_members.index(member_id))
            await self.bot.pool.execute(query, settings.muted_members, guild_id)
            self.get_guild_settings.invalidate(self, guild_id)

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
    async def selfmute(self, ctx, duration: humantime.ShortTime, *, reason=None):
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

        human_friendly = humantime.timedelta(
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

        self.get_guild_settings.invalidate(self, ctx.guild.id)

        await ctx.send(
            f"{ctx.tick(True)} You have been muted for `{human_friendly}`.\n"
            "Don't bug anyone about it!"
        )

    @commands.command(description="View members with the muted role")
    @checks.has_permissions(manage_roles=True)
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
        if not settings:
            return await ctx.send("No mute role has been set for this server.")

        if not settings.mute_role:
            return await ctx.send("No mute role has been set for this server.")

        return await ctx.send(f"This server's mute role is **`{settings.mute_role}`**")

    @mute_role.command(name="set", description="Set an existing mute role")
    @commands.bot_has_permissions(manage_roles=True)
    @checks.has_permissions(manage_roles=True)
    async def mute_role_set(self, ctx, *, role: discord.Role):
        query = """INSERT INTO guild_settings (id, mute_role_id, muted_members)
                   VALUES ($1, $2, $3) ON CONFLICT (id) DO UPDATE SET
                        mute_role_id=EXCLUDED.mute_role_id,
                        muted_members=EXCLUDED.muted_members;
                """

        await ctx.db.execute(query, ctx.guild.id, role.id, [])
        self.get_guild_settings.invalidate(self, ctx.guild.id)

        await ctx.send(f"{ctx.tick(True)} Set mute role to **`{role}`**")

    @mute_role.command(
        name="create",
        description="Create a new mute role and change channel overwrites",
    )
    @commands.bot_has_permissions(manage_channels=True, manage_roles=True)
    @checks.has_permissions(manage_channels=True, manage_roles=True)
    async def mute_role_create(
        self, ctx, name="Muted", *, color: discord.Color = discord.Color.dark_grey()
    ):
        settings = await self.get_guild_settings(ctx.guild.id)

        if settings and settings.mute_role:
            result = await ctx.confirm(
                "A mute role is already set for this server. "
                "Are you sure you want to create a new one?"
            )

            if not result:
                return await ctx.send("Aborted.")

        await ctx.trigger_typing()

        guild = ctx.guild
        reason = f"Creation of Muted role by {ctx.author} (ID: {ctx.author.id})"

        role = await guild.create_role(name=name, color=color, reason=reason)

        succeeded = 0
        failed = []

        for channel in guild.channels:
            overwrites = channel.overwrites
            overwrites[role] = discord.PermissionOverwrite(
                send_messages=False, add_reactions=False, speak=False
            )
            try:
                await channel.edit(overwrites=overwrites, reason=reason)
                succeeded += 1

            except discord.HTTPException:
                if isinstance(channel, discord.TextChannel):
                    formatted = channel.mention

                elif isinstance(channel, discord.VoiceChannel):
                    formatted = f"<:voice_channel:665577300552843294> {channel.name}"

                else:
                    formatted = channel.name

                failed.append(formatted)

        query = """INSERT INTO guild_settings (id, mute_role_id)
                   VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET
                        mute_role_id=EXCLUDED.mute_role_id;
                """

        await ctx.db.execute(query, ctx.guild.id, role.id)
        self.get_guild_settings.invalidate(self, ctx.guild.id)

        message = (
            "Created mute role and changed channel overwrites.\n"
            f"Attempted to change {len(guild.channels)} channels:"
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

        if not settings or not settings.mute_role_id:
            raise NoMuteRole()

        query = """UPDATE guild_settings
                   SET mute_role_id=$1, muted_members=$2
                   WHERE id=$3;
                """

        await ctx.db.execute(query, None, [], ctx.guild.id)
        self.get_guild_settings.invalidate(self, ctx.guild.id)

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
            raise commands.BadArgument(
                f"Attachment URL (`{url}`) must end in `.jpg`, `.png`, or `.gif`."
            )

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
            f"Send messages to {channel.mention}": loading,
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
                    message = message[start + len(full_str) :].strip()
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

    async def do_purge(self, ctx, limit, predicate, *, before=None, after=None):
        if limit > 2000:
            return await ctx.send(f"Too many messages to search given ({limit}/2000)")

        if before is None:
            before = ctx.message
        else:
            before = discord.Object(id=before)

        if after is not None:
            after = discord.Object(id=after)

        try:
            deleted = await ctx.channel.purge(
                limit=limit, before=before, after=after, check=predicate
            )

        except discord.Forbidden:
            return await ctx.send("I do not have permissions to delete messages.")

        except discord.HTTPException as e:
            return await ctx.send(f"Error: {e} (try a smaller search?)")

        spammers = Counter(m.author.display_name for m in deleted)
        deleted = len(deleted)
        messages = [f'{deleted} message{" was" if deleted == 1 else "s were"} removed.']
        if deleted:
            messages.append("")
            spammers = sorted(spammers.items(), key=lambda t: t[1], reverse=True)
            messages.extend(f"**{name}**: {count}" for name, count in spammers)

        to_send = "\n".join(messages)

        if len(to_send) > 2000:
            await ctx.send(f"Successfully removed {deleted} messages.", delete_after=10)
        else:
            await ctx.send(to_send, delete_after=10)

    @flags.add_flag("--user", nargs="+")
    @flags.add_flag("--contains", nargs="+")
    @flags.add_flag("--starts", nargs="+")
    @flags.add_flag("--ends", nargs="+")
    @flags.add_flag("--before", type=int)
    @flags.add_flag("--after", type=int)
    @flags.add_flag("--bot", action="store_true")
    @flags.add_flag("--embeds", action="store_true")
    @flags.add_flag("--files", action="store_true")
    @flags.add_flag("--emoji", action="store_true")
    @flags.add_flag("--reactions", action="store_true")
    @flags.add_flag("--or", action="store_true")
    @flags.add_flag("--not", action="store_true")
    @commands.group(usage="[search=100]", invoke_without_command=True, cls=NoUsageFlagGroup)
    @checks.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def purge(self, ctx, search: typing.Optional[int] = None, **flags):
        """Purge messages in a channel using an optional command-line syntax

        Flags:
          `--user`       The author of the message
          `--contains`   A string to search for in the message
          `--starts`     A string the message starts with
          `--ends`       A string the message ends with
          `--before`     Messages must come before this message
          `--after`      Messages must come after this message

        Boolean Flags:
          `--bot`        The message was sent by a bot
          `--embeds`     The message contains an embed
          `--files`      The message contains file(s)
          `--emoji`      The message contains custom emoji
          `--reactions`  The message has been reacted to
          `--or`         Use logical OR for all flags
          `--not`        Use logical NOT for all flags
        """
        predicates = []
        if flags["bot"]:
            predicates.append(lambda m: m.author.bot)

        if flags["embeds"]:
            predicates.append(lambda m: len(m.embeds))

        if flags["files"]:
            predicates.append(lambda m: len(m.attachments))

        if flags["reactions"]:
            predicates.append(lambda m: len(m.reactions))

        if flags["emoji"]:
            custom_emoji = re.compile(r"<:(\w+):(\d+)>")
            predicates.append(lambda m: custom_emoji.search(m.content))

        if flags["user"]:
            users = []
            converter = commands.MemberConverter()
            for u in flags["user"]:
                try:
                    user = await converter.convert(ctx, u)
                    users.append(user)
                except Exception as e:
                    await ctx.send(str(e))
                    return

            predicates.append(lambda m: m.author in users)

        if flags["contains"]:
            predicates.append(
                lambda m: any(sub in m.content for sub in flags["contains"])
            )

        if flags["starts"]:
            predicates.append(
                lambda m: any(m.content.startswith(s) for s in flags["starts"])
            )

        if flags["ends"]:
            predicates.append(
                lambda m: any(m.content.endswith(s) for s in flags["ends"])
            )

        op = all if not flags["or"] else any

        def predicate(m):
            r = op(p(m) for p in predicates)
            if flags["not"]:
                return not r
            return r

        async def warn_them(search):
            return await ctx.confirm(
                f"This action might delete up to {search} messages. Continue?"
            )

        if flags["after"]:
            if search is None:
                search = 2000
                if not await warn_them(search):
                    return await ctx.send("Aborted")

        if search is None:
            search = 100
            if not await warn_them(search):
                return await ctx.send("Aborted")

        search = max(0, min(2000, search))  # clamp from 0-2000
        await self.do_purge(
            ctx, search, predicate, before=flags["before"], after=flags["after"]
        )

    @purge.command(
        name="bot",
        usage="[search=100] <bot> [prefixes...]",
    )
    async def purge_bot(
        self, ctx, search: typing.Optional[int], bot: discord.User, *prefixes
    ):
        """Purge commands from another bot with their prefixes

        Example usage:
            - purge bot @BotName ! ? "$ "

            This will purge message from @BotName and messages
            that start with "?", "!", or "$ ".
        """

        async def warn_them(search):
            return await ctx.confirm(
                f"This action might delete up to {search} messages. Continue?"
            )

        if not search:
            search = 100
            if not await warn_them(search):
                return await ctx.send("Aborted.")

        predicates = []
        predicates.append(lambda m: m.author == bot)

        if prefixes:
            predicates.append(lambda m: any(m.content.startswith(p) for p in prefixes))

        def predicate(m):
            r = any(p(m) for p in predicates)
            return r

        search = max(0, min(2000, search))  # clamp from 0-2000
        await self.do_purge(ctx, search, predicate)

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
        if str(payload.user_id) in self.bot.blacklist:
            return

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
                content=f"{GREEN_TICK} **`{user}` accepted `{member}`** into the server."
            )
        elif emoji == RED_TICK:
            await bot_message.edit(
                content=f"{RED_TICK} **`{user}` ignored `{member}`**"
            )
        else:
            await bot_message.edit(
                content=f"**{RED_TICK} Timed out!** Ignored `{member}`"
            )

        await bot_message.clear_reactions()

    # sourced from Rapptz/RoboDanny
    # https://github.com/Rapptz/RoboDanny/blob/7cd472ca021e9e166959e91a7ff64036474ea46c/cogs/mod.py#L659-L704
    async def _basic_cleanup_strategy(self, ctx, search):
        count = 0
        async for msg in ctx.history(limit=search, before=ctx.message):
            if msg.author == ctx.me:
                await msg.delete()
                count += 1
        return {"Bot": count}

    async def _complex_cleanup_strategy(self, ctx, search):
        prefixes = tuple(self.bot.get_guild_prefixes(ctx.guild))  # thanks startswith

        def check(m):
            return m.author == ctx.me or m.content.startswith(prefixes)

        deleted = await ctx.channel.purge(limit=search, check=check, before=ctx.message)
        return Counter(m.author.display_name for m in deleted)

    @commands.command()
    @checks.has_permissions(manage_messages=True)
    async def cleanup(self, ctx, search=100):
        """Cleans up the bot's messages from the channel.

        If a search number is specified, it searches that many
        messages to delete. If the bot has Manage Messages
        permissions then it will try to delete messages that
        look like they invoked the bot as well.

        After the cleanup is completed, the bot will send
        you a message with which people got their messages
        deleted and their count. This is useful to see which
        users are spammers.

        You must have Manage Messages permission to use this.
        """

        strategy = self._basic_cleanup_strategy
        if ctx.me.permissions_in(ctx.channel).manage_messages:
            strategy = self._complex_cleanup_strategy

        spammers = await strategy(ctx, search)
        deleted = sum(spammers.values())
        messages = [f'{deleted} message{" was" if deleted == 1 else "s were"} removed.']
        if deleted:
            messages.append("")
            spammers = sorted(spammers.items(), key=lambda t: t[1], reverse=True)
            messages.extend(f"- **{author}**: {count}" for author, count in spammers)

        await ctx.send("\n".join(messages), delete_after=10)

    async def bulk_insert(self):
        query = """UPDATE guild_settings
                   SET muted_members = x.result_array
                   FROM jsonb_to_recordset($1::jsonb) AS
                   x(id BIGINT, result_array BIGINT[])
                   WHERE guild_settings.id = x.guild_id;
                """

        if not self._data_batch:
            return

        final_data = []
        for guild_id, data in self._data_batch.items():
            # If it's touched this function then chances are that this has hit cache before
            # so it's not actually doing a query, hopefully.
            config = await self.get_guild_settings(guild_id)
            as_set = config.muted_members
            for member_id, insertion in data:
                func = as_set.add if insertion else as_set.discard
                func(member_id)

            final_data.append({"guild_id": guild_id, "result_array": list(as_set)})
            self.get_guild_settings.invalidate(self, guild_id)

        await self.bot.pool.execute(query, final_data)
        self._data_batch.clear()

    @tasks.loop(seconds=15.0)
    async def batch_updates(self):
        async with self._batch_lock:
            await self.bulk_insert()

    @tasks.loop(seconds=10.0)
    async def bulk_send_messages(self):
        async with self._batch_message_lock:
            for ((guild_id, channel_id), messages) in self.message_batches.items():
                guild = self.bot.get_guild(guild_id)
                channel = guild and guild.get_channel(channel_id)
                if channel is None:
                    continue

                paginator = commands.Paginator(suffix="", prefix="")
                for message in messages:
                    paginator.add_line(message)

                for page in paginator.pages:
                    try:
                        await channel.send(page)
                    except discord.HTTPException:
                        pass

            self.message_batches.clear()

    async def check_raid(self, config, guild_id, member, message):
        if config.raid_mode != RaidMode.strict.value:
            return

        checker = self._spam_check[guild_id]
        if not checker.is_spamming(message):
            return

        try:
            await member.ban(reason="Auto-ban from spam (strict raid mode ban)")
        except discord.HTTPException:
            log.info(
                f"[Raid Mode] Failed to ban {member} (ID: {member.id}) from server {member.guild} via strict mode."
            )
        else:
            log.info(
                f"[Raid Mode] Banned {member} (ID: {member.id}) from server {member.guild} via strict mode."
            )

    @commands.Cog.listener()
    async def on_message(self, message):
        author = message.author
        if author.id in (self.bot.user.id, self.bot.owner_id):
            return

        if message.guild is None:
            return

        if not isinstance(author, discord.Member):
            return

        if author.bot:
            return

        # we're going to ignore members with roles
        if len(author.roles) > 1:
            return

        guild_id = message.guild.id
        config = await self.get_guild_settings(guild_id)
        if config is None:
            return

        # check for raid mode stuff
        await self.check_raid(config, guild_id, author, message)

        # auto-ban tracking for mention spams begin here
        if len(message.mentions) <= 3:
            return

        if not config.mention_count:
            return

        # check if it meets the thresholds required
        mention_count = sum(not m.bot and m.id != author.id for m in message.mentions)
        if mention_count < config.mention_count:
            return

        if message.channel.id in config.safe_mention_channel_ids:
            return

        try:
            await author.ban(reason=f"Spamming mentions ({mention_count} mentions)")
        except Exception as e:
            log.info(
                f"Failed to autoban member {author} (ID: {author.id}) in guild ID {guild_id}"
            )
        else:
            to_send = f"Banned {author} (ID: {author.id}) for spamming {mention_count} mentions."
            async with self._batch_message_lock:
                self.message_batches[(guild_id, message.channel.id)].append(to_send)

            # log.info(
            #     f"Member {author} (ID: {author.id}) has been autobanned from guild ID {guild_id}"
            # )
            log.info(
                f"[MentionSpam] Banned {author} (ID: {author.id}) from server {author.guild} for spamming {mention_count} mentions.")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild_id = member.guild.id
        config = await self.get_guild_settings(guild_id)
        if config is None:
            return

        if not config.raid_mode:
            return

        now = datetime.datetime.utcnow()

        is_new = member.created_at > (now - datetime.timedelta(days=7))
        checker = self._spam_check[guild_id]

        # Do the broadcasted message to the channel
        title = "Member Joined"
        if checker.is_fast_join(member):
            colour = 0xDD5F53  # red
            if is_new:
                title = "Member Joined (Very New Member)"
        else:
            colour = 0x53DDA4  # green

            if is_new:
                colour = 0xDDA453  # yellow
                title = "Member Joined (Very New Member)"

        e = discord.Embed(title=title, colour=colour)
        e.timestamp = now
        e.set_author(name=str(member), icon_url=member.avatar_url)
        e.add_field(name="ID", value=member.id)
        e.add_field(name="Joined", value=member.joined_at)
        e.add_field(
            name="Created",
            value=humantime.timedelta(member.created_at),
            inline=False,
        )

        if config.broadcast_channel:
            try:
                await config.broadcast_channel.send(embed=e)
            except discord.Forbidden:
                async with self._disable_lock:
                    await self.disable_raid_mode(guild_id)

    @commands.group(aliases=["raids"], invoke_without_command=True)
    @checks.has_permissions(manage_guild=True)
    async def raid(self, ctx):
        """Controls raid mode on the server.

        Calling this command with no arguments will show the current raid
        mode information.

        You must have Manage Server permissions to use this command or
        its subcommands.
        """

        query = "SELECT raid_mode, broadcast_channel FROM guild_settings WHERE id=$1;"

        row = await ctx.db.fetchrow(query, ctx.guild.id)
        if row is None:
            fmt = "Raid Mode: off\nBroadcast Channel: None"
        else:
            ch = f"<#{row[1]}>" if row[1] else None
            mode = RaidMode(row[0]) if row[0] is not None else RaidMode.off
            fmt = f"Raid Mode: {mode}\nBroadcast Channel: {ch}"

        await ctx.send(fmt)

    @raid.command(name="on", aliases=["enable", "enabled"])
    @checks.has_permissions(manage_guild=True)
    async def raid_on(self, ctx, *, channel: discord.TextChannel = None):
        """Enables basic raid mode on the server.

        When enabled, server verification level is set to table flip
        levels and allows the bot to broadcast new members joining
        to a specified channel.

        If no channel is given, then the bot will broadcast join
        messages on the channel this command was used in.
        """

        channel = channel or ctx.channel

        try:
            await ctx.guild.edit(verification_level=discord.VerificationLevel.high)
        except discord.HTTPException:
            await ctx.send("\N{WARNING SIGN} Could not set verification level.")

        query = """INSERT INTO guild_settings (id, raid_mode, broadcast_channel)
                   VALUES ($1, $2, $3) ON CONFLICT (id)
                   DO UPDATE SET
                        raid_mode = EXCLUDED.raid_mode,
                        broadcast_channel = EXCLUDED.broadcast_channel;
                """

        await ctx.db.execute(query, ctx.guild.id, RaidMode.on.value, channel.id)
        self.get_guild_settings.invalidate(self, ctx.guild.id)
        await ctx.send(
            f"Raid mode enabled. Broadcasting join messages to {channel.mention}."
        )

    async def disable_raid_mode(self, guild_id):
        query = """INSERT INTO guild_settings (id, raid_mode, broadcast_channel)
                   VALUES ($1, $2, NULL) ON CONFLICT (id)
                   DO UPDATE SET
                        raid_mode = EXCLUDED.raid_mode,
                        broadcast_channel = NULL;
                """

        await self.bot.pool.execute(query, guild_id, RaidMode.off.value)
        self._spam_check.pop(guild_id, None)
        self.get_guild_settings.invalidate(self, guild_id)

    @raid.command(name="off", aliases=["disable", "disabled"])
    @checks.has_permissions(manage_guild=True)
    async def raid_off(self, ctx):
        """Disables raid mode on the server.

        When disabled, the server verification levels are set
        back to Low levels and the bot will stop broadcasting
        join messages.
        """

        try:
            await ctx.guild.edit(verification_level=discord.VerificationLevel.low)
        except discord.HTTPException:
            await ctx.send("\N{WARNING SIGN} Could not set verification level.")

        await self.disable_raid_mode(ctx.guild.id)
        await ctx.send("Raid mode disabled. No longer broadcasting join messages.")

    @raid.command(name="strict")
    @checks.has_permissions(manage_guild=True)
    async def raid_strict(self, ctx, *, channel: discord.TextChannel = None):
        """Enables strict raid mode on the server.

        Strict mode is similar to regular enabled raid mode, with the added
        benefit of auto-banning members that are spamming. The threshold for
        spamming depends on a per-content basis and also on a per-user basis
        of 15 messages per 17 seconds.

        If this is considered too strict, it is recommended to fall back to regular
        raid mode.
        """
        channel = channel or ctx.channel

        perms = ctx.me.guild_permissions
        if not (perms.kick_members and perms.ban_members):
            return await ctx.send(
                "\N{NO ENTRY SIGN} I do not have permissions to kick and ban members."
            )

        try:
            await ctx.guild.edit(verification_level=discord.VerificationLevel.high)
        except discord.HTTPException:
            await ctx.send("\N{WARNING SIGN} Could not set verification level.")

        query = """INSERT INTO guild_settings (id, raid_mode, broadcast_channel)
                   VALUES ($1, $2, $3) ON CONFLICT (id)
                   DO UPDATE SET
                        raid_mode = EXCLUDED.raid_mode,
                        broadcast_channel = EXCLUDED.broadcast_channel;
                """

        await ctx.db.execute(query, ctx.guild.id, RaidMode.strict.value, channel.id)
        self.get_guild_settings.invalidate(self, ctx.guild.id)
        await ctx.send(
            f"Raid mode enabled strictly. Broadcasting join messages to {channel.mention}."
        )

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @checks.has_permissions(ban_members=True)
    async def mentionspam(self, ctx, count: int = None):
        """Enables auto-banning accounts that spam mentions.

        If a message contains `count` or more mentions then the
        bot will automatically attempt to auto-ban the member.
        The `count` must be greater than 3. If the `count` is 0
        then this is disabled.

        This only applies for user mentions. Everyone or Role
        mentions are not included.

        To use this command you must have the Ban Members permission.
        """

        if count is None:
            query = """SELECT mention_count, COALESCE(safe_mention_channel_ids, '{}') AS channel_ids
                       FROM guild_settings
                       WHERE id=$1;
                    """

            row = await ctx.db.fetchrow(query, ctx.guild.id)
            if row is None or not row["mention_count"]:
                return await ctx.send(
                    "This server has not set up mention spam banning."
                )

            ignores = ", ".join(f"<#{e}>" for e in row["channel_ids"]) or "None"
            return await ctx.send(
                f'- Threshold: {row["mention_count"]} mentions\n- Ignored Channels: {ignores}'
            )

        if count == 0:
            query = """UPDATE guild_settings SET mention_count = NULL WHERE id=$1;"""
            await ctx.db.execute(query, ctx.guild.id)
            self.get_guild_settings.invalidate(self, ctx.guild.id)
            return await ctx.send("Auto-banning members has been disabled.")

        if count <= 3:
            await ctx.send(
                "\N{NO ENTRY SIGN} Auto-ban threshold must be greater than three."
            )
            return

        query = """INSERT INTO guild_settings (id, mention_count, safe_mention_channel_ids)
                   VALUES ($1, $2, '{}')
                   ON CONFLICT (id) DO UPDATE SET
                       mention_count = $2;
                """
        await ctx.db.execute(query, ctx.guild.id, count)
        self.get_guild_settings.invalidate(self, ctx.guild.id)
        await ctx.send(
            f"Now auto-banning members that mention more than {count} users."
        )

    @mentionspam.command(name="ignore", aliases=["bypass"])
    @commands.guild_only()
    @checks.has_permissions(ban_members=True)
    async def mentionspam_ignore(self, ctx, *channels: discord.TextChannel):
        """Specifies what channels ignore mentionspam auto-bans.

        If a channel is given then that channel will no longer be protected
        by auto-banning from mention spammers.

        To use this command you must have the Ban Members permission.
        """

        query = """UPDATE guild_settings
                   SET safe_mention_channel_ids =
                       ARRAY(SELECT DISTINCT * FROM unnest(COALESCE(safe_mention_channel_ids, '{}') || $2::bigint[]))
                   WHERE id = $1;
                """

        if len(channels) == 0:
            return await ctx.send("Missing channels to ignore.")

        channel_ids = [c.id for c in channels]
        await ctx.db.execute(query, ctx.guild.id, channel_ids)
        self.get_guild_settings.invalidate(self, ctx.guild.id)
        await ctx.send(
            f'Mentions are now ignored on {", ".join(c.mention for c in channels)}.'
        )

    @mentionspam.command(name="unignore", aliases=["protect"])
    @commands.guild_only()
    @checks.has_permissions(ban_members=True)
    async def mentionspam_unignore(self, ctx, *channels: discord.TextChannel):
        """Specifies what channels to take off the ignore list.

        To use this command you must have the Ban Members permission.
        """

        if len(channels) == 0:
            return await ctx.send("Missing channels to protect.")

        query = """UPDATE guild_settings
                   SET safe_mention_channel_ids =
                       ARRAY(SELECT element FROM unnest(safe_mention_channel_ids) AS element
                             WHERE NOT(element = ANY($2::bigint[])))
                   WHERE id = $1;
                """

        await ctx.db.execute(query, ctx.guild.id, [c.id for c in channels])
        self.get_guild_settings.invalidate(self, ctx.guild.id)
        await ctx.send("Updated mentionspam ignore list.")

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
