import asyncio
import datetime
import enum
import json
import io
import logging
import os.path
import re
import typing
from collections import Counter, defaultdict
from urllib.parse import urlparse

import asyncpg
import discord
from discord.ext import commands, flags, tasks
from jishaku.models import copy_context_with


from clam.utils import cache, checks, db, humantime
from clam.utils.checks import has_manage_guild
from clam.utils.emojis import GREEN_TICK, LOADING, RED_TICK
from clam.utils.flags import NoUsageFlagGroup
from clam.utils.formats import human_join, plural
from clam.utils.utils import is_int


log = logging.getLogger("clam.mod")


class AutomodMode(enum.Enum):
    off = 0
    low = 1
    medium = 2
    high = 3

    def __str__(self):
        return self.name


class SpamViolations(db.Table, table_name="spam_violations"):
    id = db.PrimaryKeyColumn()

    guild_id = db.Column(db.Integer(big=True))
    channel_id = db.Column(db.Integer(big=True))
    user_id = db.Column(db.Integer(big=True))
    violated_at = db.Column(db.Datetime, default="now() at time zone 'utc'")


class GuildSettingsTable(db.Table, table_name="guild_settings"):
    id = db.Column(db.Integer(big=True), primary_key=True)

    mute_role_id = db.Column(db.Integer(big=True))
    muted_members = db.Column(db.Array(db.Integer(big=True)))
    automod_mode = db.Column(db.Integer(small=True))
    violation_count = db.Column(db.Integer(small=True))
    ignore_roles = db.Column(db.Boolean, default=False)
    ignored_channels = db.Column(db.Array(db.Integer(big=True)))
    ignored_roles = db.Column(db.Array(db.Integer(big=True)))
    ignored_members = db.Column(db.Array(db.Integer(big=True)))
    mention_count = db.Column(db.Integer(small=True))
    forbidden_words = db.Column(db.Array(db.String))


class GuildSettings:
    @classmethod
    def from_record(cls, record, bot):
        self = cls()

        self.bot = bot

        self.id = record["id"]
        self.mute_role_id = record["mute_role_id"]
        self.muted_members = record["muted_members"] or []
        self.automod_mode = record["automod_mode"]
        self.violation_count = record["violation_count"]
        self.ignore_roles = record["ignore_roles"]
        self.ignored_channels = record["ignored_channels"] or []
        self.ignored_roles = record["ignored_roles"] or []
        self.ignored_members = record["ignored_members"] or []
        self.mention_count = record["mention_count"]
        self.forbidden_words = record["forbidden_words"] or []

        return self

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
        now = discord.utils.utcnow()
        seven_days_ago = now - datetime.timedelta(days=7)
        ninety_days_ago = now - datetime.timedelta(days=90)
        return member.created_at > ninety_days_ago and member.joined_at > seven_days_ago

    def is_spamming(self, message):
        if message.guild is None:
            return False

        current = message.created_at.timestamp()

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
        joined = member.joined_at or discord.utils.utcnow()
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
    """Moderation commands that help you moderate your server."""

    def __init__(self, bot):
        self.bot = bot
        self.emoji = "\N{POLICE CAR}"
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

        # AutoMod stuff

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

    def cog_unload(self):
        self.batch_updates.stop()

    async def cog_command_error(self, ctx, error):
        if isinstance(error, NoMuteRole) or isinstance(error, RoleHierarchyFailure):
            await ctx.send(f"{ctx.tick(False)} {error}")
            ctx.handled = True

        elif isinstance(error, commands.CommandInvokeError):
            original = error.original
            if isinstance(original, discord.Forbidden):
                await ctx.send("I do not have permission to execute this action.")
                ctx.handled = True
            elif isinstance(original, discord.NotFound):
                await ctx.send(f"This entity does not exist: {original.text}")
                ctx.handled = True
            elif isinstance(original, discord.HTTPException):
                await ctx.send(
                    "Somehow, an unexpected error occurred. Try again later?"
                )
                ctx.handled = True

    @commands.command(aliases=["su"])
    @checks.has_permissions(administrator=True)
    async def runas(self, ctx, target: discord.Member, *, command):
        """Runs a command as someone else.

        You must have the administrator permission to run this command,
        and you cannot run a command as someone with a higher role than you.
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

    @cache.cache()
    async def get_spam_violations(self, guild_id, user_id):
        query = "SELECT * FROM spam_violations WHERE guild_id=$1 AND user_id=$2;"
        return await self.bot.pool.fetch(query, guild_id, user_id) or []

    async def log_mod_action(self, ctx, action, emoji, moderator, target, reason, duration=None):
        guild_log = await ctx.get_guild_log()
        if guild_log:
            em = discord.Embed(title=f"{emoji} [Mod Action] {action}", color=discord.Color.purple())
            if duration:
                em.add_field(name="Duration", value=duration, inline=False)
            em.add_field(name="User", value=str(target), inline=False)
            if reason:
                em.add_field(name="Reason", value=reason, inline=False)
            em.add_field(
                name="Responsible Moderator",
                value=f"{ctx.author.mention} | {ctx.author} (ID: {ctx.author.id})",
                inline=False
            )
            self.bot.loop.create_task(guild_log.log_mod_action(embed=em))

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
        """Bans a user from the server."""

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
            human_friendly = f"user with an ID of `{user_id}`"

        to_be_banned = discord.Object(id=user_id)
        reason = f"Ban by {ctx.author} (ID: {ctx.author.id}) with reason: {reason}"

        try:
            await ctx.guild.ban(to_be_banned, reason=reason)
        except discord.HTTPException:
            return await ctx.send(f"{ctx.tick(False)} I couldn't ban that user.")

        log_target = str(user) if user else f"User with ID {user_id}"
        await self.log_mod_action(ctx, "Ban", "\N{HAMMER}", ctx.author, log_target, reason)

        await ctx.send(f"{ctx.tick(True)} Banned {human_friendly}")

    @commands.command()
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
        """Temporarily bans a user from the server."""

        timers = self.bot.get_cog("Timers")
        if not timers:
            return await ctx.send(
                "Sorry, that functionality isn't available right now. Try again later."
            )

        if isinstance(user, (discord.User, discord.Member)):
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

        friendly_time = humantime.timedelta(duration.dt, discord_fmt=False)
        audit_reason = f"Tempban by {ctx.author} (ID: {ctx.author.id}) for {friendly_time} with reason: {reason}"

        try:
            await ctx.guild.ban(to_be_banned, reason=audit_reason)
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

        log_target = str(user) if user else f"User with ID {user_id}"
        emoji = "\N{HOURGLASS} \N{HAMMER}"
        await self.log_mod_action(ctx, "Tempban", emoji, ctx.author, log_target, reason, friendly_time)

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
        mod_text = f"{mod} (ID: {mod.id})" if mod else f"mod with ID {mod_id}"

        reason = (
            f"Automatic unban from tempban command. Command orignally invoked by {mod_text}"
        )
        await guild.unban(discord.Object(id=user_id), reason=reason)

        guild_log = await self.bot.get_guild_log(guild_id)
        if guild_log:
            emoji = "\N{HOURGLASS} \N{SPARKLES}"

            user = self.bot.get_user(user_id)
            if user:
                target = str(user)
            else:
                target = f"User with ID {user_id}"

            if mod:
                moderator = f"{mod.mention} | {mod} (ID: {mod.id})"

            else:
                moderator = f"Unknown moderator with ID {mod_id}"

            em = discord.Embed(title=f"{emoji} [Mod Action] Tempban Expiration", color=discord.Color.purple())
            duration = humantime.fulltime(timer.created_at)
            em.add_field(name="Original Tempban", value=duration, inline=False)
            em.add_field(name="User", value=target, inline=False)
            if reason:
                em.add_field(name="Reason", value=reason, inline=False)
            em.add_field(
                name="Responsible Moderator",
                value=moderator,
                inline=False
            )
            await guild_log.log_mod_action(embed=em)

    @commands.command()
    @checks.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx, user: BannedUser, *, reason=None):
        """Unbans a user from the server."""

        to_be_unbanned = discord.Object(id=user.id)
        reason = f"Unban by {ctx.author} (ID: {ctx.author.id}) with reason: {reason}"

        try:
            await ctx.guild.unban(to_be_unbanned, reason=reason)
        except discord.HTTPException:
            return await ctx.send(f"{ctx.tick(False)} I couldn't unban that user.")

        await self.log_mod_action(ctx, "Unban", "\N{SPARKLES}", ctx.author, user, reason)

        await ctx.send(f"{ctx.tick(True)} Unbanned user `{user}`")

    @commands.command()
    @checks.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx, user: discord.Member, *, reason=None):
        """Kicks a user from the server."""

        if not role_hierarchy_check(ctx, ctx.author, user):
            return await ctx.send(
                "You can't preform this action due to role hierarchy."
            )

        audit_reason = f"Kick by {ctx.author} (ID: {ctx.author.id}) with reason: {reason}"

        try:
            await ctx.guild.kick(user, reason=audit_reason)
        except discord.HTTPException:
            return await ctx.send(f"{ctx.tick(False)} I couldn't kick that user.")

        await self.log_mod_action(ctx, "Kick", "\N{BOXING GLOVE}", ctx.author, user, reason)

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
        """Mutes a user."""

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

        audit_reason = f"Mute by {ctx.author} (ID: {ctx.author.id}) with reason: {reason}"
        await settings.mute_member(member, audit_reason)
        self.get_guild_settings.invalidate(self, ctx.guild.id)

        emoji = "\N{SPEAKER WITH CANCELLATION STROKE}"
        await self.log_mod_action(ctx, "Mute", emoji, ctx.author, member, reason)

        await ctx.send(f"{ctx.tick(True)} Muted `{member}`")

    @commands.command()
    @can_mute()
    async def unmute(
        self, ctx, member: typing.Union[discord.Member, int], *, reason=None
    ):
        """Unmutes a member."""

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

        audit_reason = f"Unmute by {ctx.author} (ID: {ctx.author.id}) with reason: {reason}"
        await settings.unmute_member(member, audit_reason)
        self.get_guild_settings.invalidate(self, ctx.guild.id)

        emoji = "\N{SPEAKER WITH THREE SOUND WAVES}"
        await self.log_mod_action(ctx, "Unmute", emoji, ctx.author, member, reason)

        await ctx.send(f"{ctx.tick(True)} Unmuted `{member}`")

    async def tempmute_member(self, settings, member, dt, reason, mod=None):
        timers = self.bot.get_cog("Timers")
        if not timers:
            raise RuntimeError("Timers cog is not loaded. Cannot perform tempmute.")

        role = settings.mute_role

        execute_db = False if member.id in settings.muted_members else True

        try:
            await settings.mute_member(member, reason, execute_db=execute_db)
            await timers.create_timer(
                dt, "tempmute", member.guild.id, role.id, mod.id or None, member.id
            )

        except Exception:
            await settings.unmute_member(
                member, reason="Mute or timer creation failed for previous tempmute."
            )
            raise

        self.get_guild_settings.invalidate(self, member.guild.id)

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
        """Temporarily mutes a member."""

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

        friendly_time = humantime.timedelta(
            duration.dt, source=ctx.message.created_at, discord_fmt=False
        )
        audit_reason = f"Tempmute by {ctx.author} (ID: {ctx.author.id}) for {friendly_time} with reason: {reason}"
        await self.tempmute_member(settings, member, duration.dt, audit_reason, mod=ctx.author)

        emoji = "\N{HOURGLASS} \N{SPEAKER WITH CANCELLATION STROKE}"
        await self.log_mod_action(ctx, "Tempmute", emoji, ctx.author, member, reason, duration=friendly_time)

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

        if not mod_id:
            mod = None
            mod_text = "AutoMod"

        else:
            mod = guild.get_member(mod_id)
            mod_text = f"{mod} (ID: {mod.id})" if mod else f"mod with ID {mod_id}"

        role = guild.get_role(role_id)
        if not role:
            return

        member = guild.get_member(member_id)
        if not member:
            return

        reason = f"Automatic unmute from mute timer. Command orignally invoked by {mod_text}"
        await settings.unmute_member(member, reason=reason, execute_db=False)

        guild_log = await self.bot.get_guild_log(guild_id)
        if guild_log:
            emoji = "\N{HOURGLASS} \N{SPEAKER WITH THREE SOUND WAVES}"

            user = self.bot.get_user(member_id)
            if user:
                target = str(user)
            else:
                target = f"User with ID {member_id}"

            if not mod_id:
                moderator = "AutoMod"

            elif mod:
                moderator = f"{mod.mention} | {mod} (ID: {mod.id})"

            else:
                moderator = f"Unknown moderator with ID {mod_id}"

            em = discord.Embed(title=f"{emoji} [Mod Action] Tempmute Expiration", color=discord.Color.purple())
            duration = humantime.fulltime(timer.created_at)
            em.add_field(name="Original Tempmute", value=duration, inline=False)
            em.add_field(name="User", value=target, inline=False)
            if reason:
                em.add_field(name="Reason", value=reason, inline=False)
            em.add_field(
                name="Responsible Moderator",
                value=moderator,
                inline=False
            )
            await guild_log.log_mod_action(embed=em)

    @commands.command()
    @commands.guild_only()
    async def selfmute(self, ctx, *, duration: humantime.ShortTime):
        """Mutes you for a duration of time.

        The duration can't be less than 5 minutes or more than 24 hours.
        Do not bother a moderator to unmute you.
        """
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

        settings = await self.get_guild_settings(ctx.guild.id)

        if not settings:
            raise NoMuteRole()

        role = settings.mute_role

        if not role:
            raise NoMuteRole()

        if role in ctx.author.roles:
            return await ctx.send("You've already been muted.")

        human_friendly = humantime.timedelta(
            duration.dt, source=ctx.message.created_at, discord_fmt=False
        )
        confirm = await ctx.confirm(
            f"Are you sure you want to mute yourself for {human_friendly}?\n"
            "Do not ask a moderator to unmute you."
        )

        if not confirm:
            return await ctx.send("Aborted selfmute")

        execute_db = False if ctx.author.id in settings.muted_members else True

        reason = f"Selfmute by {ctx.author} (ID: {ctx.author.id}) for {human_friendly}"

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

    @commands.command()
    @checks.has_permissions(manage_roles=True)
    async def muted(self, ctx):
        """Shows members with the muted role."""

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
        await pages.start()

    @mute.group(name="role", invoke_without_command=True)
    async def mute_role(self, ctx):
        """Manages the server's mute role."""

        settings = await self.get_guild_settings(ctx.guild.id)
        if not settings:
            return await ctx.send("No mute role has been set for this server.")

        if not settings.mute_role:
            return await ctx.send("No mute role has been set for this server.")

        return await ctx.send(f"This server's mute role is **`{settings.mute_role}`**")

    @mute_role.command(name="set")
    @commands.bot_has_permissions(manage_roles=True)
    @checks.has_permissions(manage_roles=True)
    async def mute_role_set(self, ctx, *, role: discord.Role):
        """Sets an existing role as the mute role."""

        query = """INSERT INTO guild_settings (id, mute_role_id, muted_members)
                   VALUES ($1, $2, $3) ON CONFLICT (id) DO UPDATE SET
                        mute_role_id=EXCLUDED.mute_role_id,
                        muted_members=EXCLUDED.muted_members;
                """

        await ctx.db.execute(query, ctx.guild.id, role.id, [])
        self.get_guild_settings.invalidate(self, ctx.guild.id)

        await ctx.send(f"{ctx.tick(True)} Set mute role to **`{role}`**")

    async def update_channel_overwrites(self, role: discord.Role, reason: str):
        succeeded = 0
        failed = []

        for channel in role.guild.channels:
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

        return succeeded, failed

    @mute_role.command(name="create")
    @commands.bot_has_permissions(manage_channels=True, manage_roles=True)
    @checks.has_permissions(manage_channels=True, manage_roles=True)
    async def mute_role_create(
        self, ctx, name="Muted", *, color: discord.Color = discord.Color.dark_grey()
    ):
        """Creates a new mute role and updates channel overwrites."""

        settings = await self.get_guild_settings(ctx.guild.id)

        if settings and settings.mute_role:
            result = await ctx.confirm(
                "A mute role is already set for this server. "
                "Are you sure you want to create a new one?"
            )

            if not result:
                return await ctx.send("Aborted.")

        async with ctx.typing():
            guild = ctx.guild
            reason = f"Creation of Muted role by {ctx.author} (ID: {ctx.author.id})"

            role = await guild.create_role(name=name, color=color, reason=reason)
            succeeded, failed = await self.update_channel_overwrites(role, reason)

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

    @mute_role.command(name="update", aliases=["sync"])
    @commands.bot_has_permissions(manage_channels=True, manage_roles=True)
    @checks.has_permissions(manage_channels=True, manage_roles=True)
    async def mute_role_update(self, ctx):
        """Updates the channel overwrites of the mute role."""
        settings = await self.get_guild_settings(ctx.guild.id)

        if not settings or not settings.mute_role_id:
            raise NoMuteRole()

        role = settings.mute_role
        reason = f"Update of Muted role by {ctx.author} (ID: {ctx.author.id})"

        async with ctx.typing():
            succeeded, failed = await self.update_channel_overwrites(role, reason)

        message = (
            "Updated channel overwrites.\n"
            f"Attempted to change {len(ctx.guild.channels)} channels:"
            f"\n  - {succeeded} succeeded\n  - {len(failed)} failed"
        )

        if failed:
            formatted = ", ".join(failed)
            message += f"\n\nChannels failed: {formatted}"

        await ctx.send(message)

    @mute_role.command(name="unbind")
    @commands.bot_has_permissions(manage_channels=True, manage_roles=True)
    @checks.has_permissions(manage_channels=True, manage_roles=True)
    async def mute_role_unbind(self, ctx):
        """Unbinds the server's mute role without deleting it."""

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

    @commands.command()
    @commands.guild_only()
    @has_manage_guild()
    @commands.is_owner()
    async def welcome_message(
        self, ctx, channel: discord.TextChannel, *, content: BinConverter
    ):
        """Sends a welcome or about message to a channel.

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
    @commands.group(usage="[search=100]", aliases=["remove"], invoke_without_command=True, cls=NoUsageFlagGroup)
    @checks.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def purge(self, ctx, search: typing.Optional[int] = None, **flags):
        """Purge messages in a channel using an optional command-line syntax.

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
        """Purges commands from another bot with their prefixes.

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

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @has_manage_guild()
    @commands.is_owner()
    async def verification(self, ctx):
        """Shows info about the verification system."""

        if str(ctx.guild.id) in self.verifications.keys():
            return await ctx.send("**Verification is ON** for this server.")
        else:
            return await ctx.send(
                "**Verification is OFF** for this server. "
                f"Set it up with `{self.bot.guild_prefix(ctx.guild)}verification create`"
            )

    @verification.command(name="create")
    @commands.guild_only()
    @has_manage_guild()
    @commands.is_owner()
    @commands.bot_has_permissions(
        manage_messages=True, manage_roles=True, manage_channels=True
    )
    async def verification_create(self, ctx):
        """Creates a verification system."""

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

    @verification.command(name="remove")
    @commands.guild_only()
    @has_manage_guild()
    @commands.is_owner()
    @commands.bot_has_permissions(
        manage_messages=True, manage_guild=True, manage_roles=True, manage_channels=True
    )
    async def verification_remove(self, ctx):
        """Removes verification."""

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
        if ctx.channel.permissions_for(ctx.me).manage_messages:
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

    async def register_spam_violation(self, member, message):
        query = """INSERT INTO spam_violations (guild_id, user_id, channel_id)
                   VALUES ($1, $2, $3);
                """
        await self.bot.pool.execute(query, member.guild.id, member.id, message.channel.id)
        self.get_spam_violations.invalidate(self, member.guild.id, member.id)

    async def do_automod(self, settings, guild_id, member, message):
        mode = settings.automod_mode

        if mode == AutomodMode.off.value:
            return

        checker = self._spam_check[guild_id]
        if not checker.is_spamming(message):
            return

        violations = await self.get_spam_violations(guild_id, member.id)
        mute_times = [
            600,    # 10 minutes
            3600,   # 1 hour
            21600,  # 6 hours
            86400,  # 1 day
        ]

        medium_and_mute = mode == AutomodMode.medium.value and len(violations) < settings.violation_count
        if mode == AutomodMode.low.value or medium_and_mute:
            # Mute the member
            if len(violations) > 3:
                mute_time = mute_times[-1]
                dt = datetime.datetime.utcnow() + datetime.timedelta(seconds=mute_time)

            else:
                mute_time = mute_times[len(violations)]
                dt = datetime.datetime.utcnow() + datetime.timedelta(seconds=mute_time)

            await self.register_spam_violation(member, message)

            friendly = humantime.timedelta(dt, brief=True, discord_fmt=False)
            reason = (
                f"[AutoMod] Auto-tempmute for {friendly} "
                f"({plural(len(violations)):previous violation|previous violations})"
            )

            try:
                await self.tempmute_member(settings, member, dt, reason)
            except Exception:
                log.info(f"[AutoMod] Failed to tempmute {member} (ID: {member.id}) for {humantime.timedelta(dt, discord_fmt=False)}")
            else:
                log.info(f"[AutoMod] Tempmuted {member} (ID: {member.id}) for {humantime.timedelta(dt, discord_fmt=False)}")

                guild_log = await self.bot.get_guild_log(member.guild.id)
                if guild_log:
                    em = discord.Embed(
                        title="[AutoMod] Member Auto-Tempmuted",
                        description=member.mention,
                        color=discord.Color.orange()
                    )
                    em.set_author(name=str(member), icon_url=member.display_avatar.url)
                    em.add_field(name="Previous Spam Violations", value=len(violations))
                    em.add_field(name="Mute Duration", value=humantime.timedelta(dt, discord_fmt=False))
                    em.add_field(name="Account Created", value=humantime.fulltime(member.created_at))

                    await guild_log.log_automod_action(embed=em)

            return

        if mode == AutomodMode.medium.value:
            member_violations = f" ({plural(len(violations)):previous violation|previous violations})"
        else:
            member_violations = ""

        try:
            await member.ban(reason=f"[AutoMod] Auto-ban from spam{member_violations}")
        except discord.HTTPException:
            log.info(
                f"[AutoMod] Failed to ban {member} (ID: {member.id}) from server {member.guild}{member_violations}."
            )
        else:
            log.info(
                f"[AutoMod] Banned {member} (ID: {member.id}) from server {member.guild}{member_violations}."
            )

            guild_log = await self.bot.get_guild_log(member.guild.id)
            if guild_log:
                em = discord.Embed(
                    title="[AutoMod] Member Auto-Banned",
                    description=member.mention,
                    color=discord.Color.red()
                )
                em.set_author(name=str(member), icon_url=member.display_avatar.url)
                if mode == AutomodMode.medium.value:
                    em.add_field(name="Previous Spam Violations", value=len(violations))

                em.add_field(name="Account Created", value=humantime.fulltime(member.created_at))

                await guild_log.log_automod_action(embed=em)

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

        guild_id = message.guild.id
        settings = await self.get_guild_settings(guild_id)
        if settings is None:
            return

        if settings.ignore_roles and len(author.roles) > 1:
            return

        if author.id in settings.ignored_members:
            return

        if message.channel.id in settings.ignored_channels:
            return

        if any(r.id in settings.ignored_roles for r in author.roles):
            return

        await self.do_automod(settings, guild_id, author, message)

        # auto-ban tracking for mention spams begin here
        if len(message.mentions) <= 3:
            return

        if not settings.mention_count:
            return

        # check if it meets the thresholds required
        mention_count = sum(not m.bot and m.id != author.id for m in message.mentions)
        if mention_count < settings.mention_count:
            return

        try:
            await author.ban(reason=f"[AutoMod] Spamming mentions ({mention_count} mentions)")
        except Exception:
            log.info(
                f"Failed to autoban member {author} (ID: {author.id}) in guild ID {guild_id}"
            )
        else:
            log.info(
                f"[MentionSpam] Banned {author} (ID: {author.id}) from server {author.guild} for spamming {mention_count} mentions.")
            guild_log = await self.bot.get_guild_log(author.guild.id)
            if guild_log:
                em = discord.Embed(
                    title="[AutoMod] Member auto-banned for mention spamming",
                    description=author.mention,
                    color=discord.Color.red()
                )
                em.set_author(name=str(author), icon_url=author.display_avatar.url)
                em.add_field(name="Account Created", value=humantime.fulltime(author.created_at))

                await guild_log.log_automod_action(embed=em)

    @commands.group(aliases=["raid"], invoke_without_command=True)
    @checks.has_permissions(manage_guild=True)
    async def automod(self, ctx):
        """Controls AutoMod on this server.

        Calling this command with no arguments will show the AutoMod information.

        AutoMod modes:
        - low: AutoMod will tempmute spammers, but not ban.
        - medium: AutoMod will tempmute spammers and ban them after 5 mutes.
        - high: AutoMod will ban spammers without muting them first.

        To learn more about a mode, use `{prefix}help automod <mode>`
        To set automod to a mode, use `{prefix}automod <mode>`

        You must have Manage Server permissions to use this command or
        its subcommands.
        """

        query = """SELECT automod_mode, violation_count, ignored_channels, ignored_roles, ignored_members
                   FROM guild_settings
                   WHERE id=$1;"""

        record = await ctx.db.fetchrow(query, ctx.guild.id)
        if not record or record["automod_mode"] == 0:
            fmt = "**AutoMod is off**"
            ignored = ""
        else:
            mode, count, ignored_channels, ignored_roles, ignored_members = record
            mode = AutomodMode(mode)
            fmt = f"**AutoMod mode set to {mode}**"
            if mode == AutomodMode.medium:
                fmt += f" (banning after {count} violations)"

            ignored = []

            if ignored_channels:
                ignored.append(f"{plural(len(ignored_channels)):channel}")

            if ignored_roles:
                ignored.append(f"{plural(len(ignored_roles)):role}")

            if ignored_members:
                ignored.append(f"{plural(len(ignored_members)):member}")

            if ignored:
                ignored = f"Ignoring {human_join(ignored, final='and')}.\n"
            else:
                ignored = ""

        await ctx.send(f"{fmt}.\n{ignored}\nFor more info on AutoMod, use `{ctx.prefix}help automod`.")

    @automod.command(name="low")
    @checks.has_permissions(manage_guild=True)
    async def automod_low(self, ctx):
        """Enables low automod on this server.

        Low automode means the bot will tempmute spammers (no bans).
        The threshold for spamming depends on a per-content basis
        and also on a per-user basis of 15 messages per 17 seconds.
        """

        settings = await self.get_guild_settings(ctx.guild.id)
        if not settings or not settings.mute_role:
            return await ctx.send(ctx.tick(False, f"A mute role has not been set. See `{ctx.prefix}help mute role`."))

        query = """INSERT INTO guild_settings (id, automod_mode)
                   VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET
                        automod_mode = EXCLUDED.automod_mode;
                """

        await ctx.db.execute(query, ctx.guild.id, AutomodMode.low.value)
        self.get_guild_settings.invalidate(self, ctx.guild.id)

        guild_log = await ctx.get_guild_log()
        if not guild_log or not guild_log.log_automod_actions:
            logging_info = f" To enable AutoMod logging, see `{ctx.prefix}help log`."
        else:
            logging_info = ""

        await ctx.send(ctx.tick(True, f"AutoMode low enabled. Now tempmuting spammers. {logging_info}"))

    @automod.command(name="medium")
    @checks.has_permissions(manage_guild=True)
    async def automod_medium(self, ctx, violations: int = 5):
        """Enables medium AutoMod on this server.

        Medium AutoMod is similar to low AutoMod, except users are
        auto-banned after a number of violations. Defaults to 5.
        The threshold for spamming depends on a per-content basis
        and also on a per-user basis of 15 messages per 17 seconds.

        To clear a user's violations, see `{prefix}automod clear`.
        """

        if violations < 3:
            raise commands.BadArgument("Violations must be at least 3.")

        settings = await self.get_guild_settings(ctx.guild.id)
        if not settings or not settings.mute_role:
            return await ctx.send(ctx.tick(False, f"A mute role has not been set. See `{ctx.prefix}help mute role`."))

        perms = ctx.me.guild_permissions
        if not (perms.kick_members and perms.ban_members):
            return await ctx.send(ctx.tick(False, "I do not have permissions to kick and ban members."))

        query = """INSERT INTO guild_settings (id, automod_mode, violation_count)
                   VALUES ($1, $2, $3) ON CONFLICT (id) DO UPDATE SET
                        automod_mode = EXCLUDED.automod_mode,
                        violation_count = EXCLUDED.violation_count;
                """

        await ctx.db.execute(query, ctx.guild.id, AutomodMode.medium.value, violations)
        self.get_guild_settings.invalidate(self, ctx.guild.id)

        guild_log = await ctx.get_guild_log()
        if not guild_log or not guild_log.log_automod_actions:
            logging_info = f" To enable AutoMod logging, see `{ctx.prefix}help log`."
        else:
            logging_info = ""

        await ctx.send(ctx.tick(True, "AutoMod medium enabled. Now tempmuting spammers. "
                                      f"Spammers will be auto-banned after {plural(violations):violation}.{logging_info}"))

    @automod.command(name="high")
    @checks.has_permissions(manage_guild=True)
    async def automod_high(self, ctx):
        """Enables high AutoMod on this server.

        High AutoMod auto-bans spammers without muting them first.
        The threshold for spamming depends on a per-content basis
        and also on a per-user basis of 15 messages per 17 seconds.

        If this is too strict for you, see AutoMod medium or low.
        """

        perms = ctx.me.guild_permissions
        if not (perms.kick_members and perms.ban_members):
            return await ctx.send(ctx.tick(False, "I do not have permissions to kick and ban members."))

        query = """INSERT INTO guild_settings (id, automod_mode)
                   VALUES ($1, $2) ON CONFLICT (id)
                   DO UPDATE SET
                        automod_mode = EXCLUDED.automod_mode;
                """

        await ctx.db.execute(query, ctx.guild.id, AutomodMode.high.value)
        self.get_guild_settings.invalidate(self, ctx.guild.id)

        guild_log = await ctx.get_guild_log()
        if not guild_log or not guild_log.log_automod_actions:
            logging_info = f" To enable AutoMod logging, see `{ctx.prefix}help log`."
        else:
            logging_info = ""

        await ctx.send(ctx.tick(True, f"AutoMod high enabled. Now banning spammers.{logging_info}"))

    @automod.command(name="off", aliases=["disable", "disabled"])
    @checks.has_permissions(manage_guild=True)
    async def automod_off(self, ctx):
        """Disables AutoMod on this server."""

        query = """INSERT INTO guild_settings (id, automod_mode)
                   VALUES ($1, $2) ON CONFLICT (id)
                   DO UPDATE SET
                        automod_mode = EXCLUDED.automod_mode;
                """

        await ctx.db.execute(query, ctx.guild.id, AutomodMode.off.value)
        self._spam_check.pop(ctx.guild.id, None)
        self.get_guild_settings.invalidate(self, ctx.guild.id)

        await ctx.send(ctx.tick(True, "AutoMod disabled."))

    @automod.command(name="clear")
    @checks.has_permissions(manage_guild=True)
    async def automod_clear(self, ctx, *, member: discord.Member):
        """Clears a member's spam violations."""

        query = """DELETE FROM spam_violations
                   WHERE guild_id=$1 AND user_id=$2
                   RETURNING id;
                   """
        records = await ctx.db.fetch(query, ctx.guild.id, member.id)

        if not records:
            return await ctx.send("That member has no spam violations on record.")

        await ctx.send(ctx.tick(True, f"Cleared {plural(len(records)):spam violation|spam violations}."))

    @automod.command(name="violations")
    @checks.has_permissions(manage_guild=True)
    async def automod_violations(self, ctx, *, member: discord.Member):
        """Shows a member's spam violations"""

        records = await self.get_spam_violations(ctx.guild.id, member.id)

        if not records:
            return await ctx.send("That member has no spam violations on record.")

        violations = []

        for v_id, guild_id, channel_id, member_id, when in records:
            channel = ctx.guild.get_channel(channel_id)
            channel = channel.mention if channel else f"Deleted channel with ID {channel_id}"

            violations.append(f"Channel: {channel} | {humantime.timedelta(when, brief=True)}")

        em = discord.Embed(title="Spam Violations", color=discord.Color.blurple())
        em.set_author(name=str(member), icon_url=member.display_avatar.url)
        pages = ctx.embed_pages(violations, em)
        await pages.start()

    @automod.group(name="ignore", invoke_without_command=True)
    @checks.has_permissions(manage_guild=True)
    async def automod_ignore(self, ctx, *, entity: typing.Union[discord.TextChannel, discord.Role, discord.Member, str]):
        """Ignores a channel, role, or member from being affected by AutoMod."""

        if isinstance(entity, str):
            raise commands.BadArgument(f"Couldn't find a text channel, role, or member named '{entity}'")

        if isinstance(entity, discord.TextChannel):
            query = """UPDATE guild_settings
                       SET ignored_channels =
                           ARRAY(SELECT DISTINCT * FROM unnest(COALESCE(ignored_channels, '{}') || $2::bigint))
                       WHERE id = $1;
                    """
            name = "ignored channels"

        elif isinstance(entity, discord.Role):
            query = """UPDATE guild_settings
                       SET ignored_roles =
                           ARRAY(SELECT DISTINCT * FROM unnest(COALESCE(ignored_roles, '{}') || $2::bigint))
                       WHERE id = $1;
                    """
            name = "ignored roles"

        else:
            query = """UPDATE guild_settings
                       SET ignored_members =
                           ARRAY(SELECT DISTINCT * FROM unnest(COALESCE(ignored_members, '{}') || $2::bigint))
                       WHERE id = $1;
                    """
            name = "ignored members"

        await ctx.db.execute(query, ctx.guild.id, entity.id)
        self.get_guild_settings.invalidate(self, ctx.guild.id)
        await ctx.send(ctx.tick(True, f"Added {entity} to AutoMod {name}."))

    @automod_ignore.command(name="roles")
    @checks.has_permissions(manage_guild=True)
    async def automod_ignore_roles(self, ctx):
        """Ignores all members with roles."""

        query = """UPDATE guild_settings
                   SET ignore_roles=TRUE
                   WHERE id=$1
                   RETURNING id;
                """
        result = await ctx.db.fetchval(query, ctx.guild.id)

        if not result:
            return await ctx.send("AutoMod is not enabled.")

        self.get_guild_settings.invalidate(self, ctx.guild.id)
        await ctx.send(ctx.tick(True, "AutoMod is now ignoring members with roles."))

    @automod.group(name="unignore", invoke_without_command=True)
    @checks.has_permissions(manage_guild=True)
    async def automod_unignore(self, ctx, *, entity: typing.Union[discord.TextChannel, discord.Role, discord.Member, str]):
        """Allows a role, channel, or member to be affected by AutoMod."""

        if isinstance(entity, str):
            raise commands.BadArgument(f"Couldn't find a text channel, role, or member named '{entity}'")

        if isinstance(entity, discord.TextChannel):
            query = """UPDATE guild_settings
                       SET ignored_channels =
                           ARRAY(SELECT element FROM unnest(ignored_channels) AS element
                                 WHERE NOT(element = $2::bigint))
                       WHERE id = $1
                       RETURNING id;
                    """
            name = "ignored channels"

        elif isinstance(entity, discord.Role):
            query = """UPDATE guild_settings
                       SET ignored_roles =
                           ARRAY(SELECT element FROM unnest(ignored_roles) AS element
                                 WHERE NOT(element = $2::bigint))
                       WHERE id = $1
                       RETURNING id;
                    """
            name = "ignored roles"

        else:
            query = """UPDATE guild_settings
                       SET ignored_members =
                           ARRAY(SELECT element FROM unnest(ignored_members) AS element
                                 WHERE NOT(element = $2::bigint))
                       WHERE id = $1
                       RETURNING id;
                    """
            name = "ignored members"

        result = await ctx.db.execute(query, ctx.guild.id, entity.id)

        if not result:
            return await ctx.send("AutoMod is not enabled.")

        self.get_guild_settings.invalidate(self, ctx.guild.id)
        await ctx.send(ctx.tick(True, f"Updated AutoMod {name}."))

    @automod_unignore.command(name="roles")
    @checks.has_permissions(manage_guild=True)
    async def automod_unignore_roles(self, ctx):
        """Unignores all members with roles."""

        query = """UPDATE guild_settings
                   SET ignore_roles=FALSE
                   WHERE id=$1
                   RETURNING id;
                """
        result = await ctx.db.fetchval(query, ctx.guild.id)

        if not result:
            return await ctx.send("AutoMod is not enabled.")

        self.get_guild_settings.invalidate(self, ctx.guild.id)
        await ctx.send(ctx.tick(True, "AutoMod is no longer ignoring members with roles."))

    @automod.command(name="ignored")
    @checks.has_permissions(manage_guild=True)
    async def automod_ignored(self, ctx):
        """Shows the AutoMod ignore list."""

        query = """SELECT ignore_roles, ignored_channels, ignored_roles, ignored_members
                   FROM guild_settings
                   WHERE id=$1;
                """
        record = await ctx.db.fetchrow(query, ctx.guild.id)

        if not record:
            return await ctx.send("AutoMod is not enabled.")

        if not any(v for v in record):
            return await ctx.send("No ignored entities.")

        paginator = commands.Paginator(prefix="", suffix="")

        if record["ignore_roles"]:
            paginator.add_line("**AutoMod is set to ignore __any__ members with roles.**")
            paginator.add_line()

        if record["ignored_channels"]:
            paginator.add_line("**Ignored Channels**")

            for channel_id in record["ignored_channels"]:
                channel = ctx.guild.get_channel(channel_id)
                paginator.add_line(channel.mention if channel else f"Deleted channel with ID {channel_id}")

            paginator.add_line()

        if record["ignored_roles"]:
            paginator.add_line("**Ignored Roles**")

            for role_id in record["ignored_roles"]:
                role = ctx.guild.get_role(role_id)
                paginator.add_line(str(role) if role else f"Deleted role with ID {role_id}")

            paginator.add_line()

        if record["ignored_members"]:
            paginator.add_line("**Ignored Members**")

            for user_id in record["ignored_members"]:
                user = self.bot.get_user(user_id)
                paginator.add_line(str(user) if user else f"Unknown user with ID {user_id}")

            paginator.add_line()

        for page in paginator.pages:
            await ctx.send(page)

    @automod.group(invoke_without_command=True)
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
            query = """SELECT mention_count
                       FROM guild_settings
                       WHERE id=$1;
                    """

            count = await ctx.db.fetchval(query, ctx.guild.id)
            if not count:
                return await ctx.send(
                    "This server has not set up mention spam banning."
                )

            return await ctx.send(f"Auto-banning members that mention more than {count} users.")

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

        query = """INSERT INTO guild_settings (id, mention_count)
                   VALUES ($1, $2)
                   ON CONFLICT (id) DO UPDATE SET
                       mention_count = $2;
                """
        await ctx.db.execute(query, ctx.guild.id, count)
        self.get_guild_settings.invalidate(self, ctx.guild.id)
        await ctx.send(
            f"Now auto-banning members that mention more than {count} users."
        )

    # FORBIDDEN WORDS

    @commands.command()
    @commands.has_permissions(manage_guild=True)
    @commands.bot_has_permissions(kick_members=True, create_instant_invite=True)
    async def forbid(self, ctx, *, word):
        """Forbids a word from being said in this server.

        If a member says any of the forbidden words,
        they will be kicked from the server and sent an invite to join back.
        To view the forbidden words, use `{prefix}forbidden`.

        You must have the Manage Server permission to use this command.
        """

        settings = await self.get_guild_settings(ctx.guild.id)

        if settings and word.lower().strip() in settings.forbidden_words:
            return await ctx.send("That word is already forbidden.")

        forbidden_words = settings.forbidden_words if settings else []
        forbidden_words.append(word.lower().strip())

        query = """INSERT INTO guild_settings (id, forbidden_words)
                   VALUES ($1, $2)
                   ON CONFLICT (id) DO UPDATE SET
                       forbidden_words = $2;
                """
        await ctx.db.execute(query, ctx.guild.id, forbidden_words)
        self.get_guild_settings.invalidate(self, ctx.guild.id)
        await ctx.send(ctx.tick(True, f"Members will now be kicked if they say `{word}`."))

    @commands.command()
    @commands.has_permissions(manage_guild=True)
    async def unforbid(self, ctx, *, word):
        """Removes a word from the forbidden words list.

        You must have the Manage Server permission to use this command.
        """

        settings = await self.get_guild_settings(ctx.guild.id)

        if not settings or word.lower().strip() not in settings.forbidden_words:
            return await ctx.send("That word isn't forbidden.")

        forbidden_words = settings.forbidden_words if settings else []
        forbidden_words.pop(forbidden_words.index(word.lower().strip()))

        query = """INSERT INTO guild_settings (id, forbidden_words)
                   VALUES ($1, $2)
                   ON CONFLICT (id) DO UPDATE SET
                       forbidden_words = $2;
                """
        await ctx.db.execute(query, ctx.guild.id, forbidden_words)
        self.get_guild_settings.invalidate(self, ctx.guild.id)
        await ctx.send(ctx.tick(True, f"Members are now free to say `{word}`."))

    @commands.group(invoke_without_command=True)
    async def forbidden(self, ctx):
        """Shows the forbidden words in this server."""

        settings = await self.get_guild_settings(ctx.guild.id)

        if not settings or not settings.forbidden_words:
            has_perms = ctx.author.guild_permissions.manage_guild or await self.bot.is_owner(ctx.author)
            extra = f" You can forbid a word with `{ctx.prefix}forbid [word]`." if has_perms else ""
            return await ctx.send(f"There are no forbidden words in this server.{extra}")

        em = discord.Embed(title="Forbidden Words", color=discord.Color.red())
        menu = ctx.embed_pages(settings.forbidden_words, em)
        await menu.start(ctx)

    @forbidden.command(name="clear")
    async def forbidden_clear(self, ctx):
        """Clears all forbidden words for this server.

        You must have the Manage Server permission to use this command.
        """

        settings = await self.get_guild_settings(ctx.guild.id)

        if not settings or not settings.forbidden_words:
            return await ctx.send("There are no forbidden words in this server.")

        confirm = await ctx.confirm(f"Are you sure you want to clear {plural(len(settings.forbidden_words)):word}?")
        if not confirm:
            return await ctx.send("Aborted.")

        query = """INSERT INTO guild_settings (id, forbidden_words)
                   VALUES ($1, $2)
                   ON CONFLICT (id) DO UPDATE SET
                       forbidden_words = $2;
                """
        await ctx.db.execute(query, ctx.guild.id, [])
        self.get_guild_settings.invalidate(self, ctx.guild.id)
        await ctx.send(ctx.tick(True, "Members are now free to say anything they like."))

    async def revert_member_on_rejoin(self, member):
        """Gives the member their roles back and changes their nickname to what it was before (if applicable)."""

        def check(m):
            return m.guild == member.guild and m == member

        try:
            new_member = await self.bot.wait_for("member_join", check=check, timeout=3600)  # 1 hour
        except asyncio.TimeoutError:
            return

        voice = None

        if member.voice:
            if member.voice.channel:
                voice = member.voice.channel

        await new_member.edit(roles=member.roles, nick=member.nick, voice_channel=voice, reason="Member rejoined from bonk")

        log.info(f"Gave {member} their roles and nickname back in {member.guild} (they were bonked).")

    async def bonk_member(self, message, word):
        """Kicks a member for saying a forbidden word and sends an invite back to the server."""

        # role hierarchy check
        if (
            await self.bot.is_owner(message.author)
            or message.author == message.guild.owner
            or (message.guild.me.top_role < message.author.top_role and message.guild.owner != message.guild.me)
           ):
            await message.channel.send(f"***{message.author.display_name} just said a forbidden word,*** but I was unable to bonk them :(")

        else:
            invite = await message.channel.create_invite(max_uses=1, max_age=86400, reason=f"Bonked for saying a forbidden word: {word}")
            try:
                await message.author.send(f"You were just bonked for saying a forbidden word in {message.guild}: `{word}`"
                                        f"\n\nHere's an invite back: {invite}")
                dmed = ""
            except discord.HTTPException:
                dmed = " I was not able to DM them with an invite back."

            try:
                await message.author.kick(reason=f"Bonked for saying a forbidden word: {word}")
                kicked = True
            except discord.HTTPException:
                kicked = False
                if not dmed:
                    try:
                        message.author.send("Nevermind, I wasn't able to bonk you :(")
                    except discord.HTTPException:
                        pass

            if not kicked:
                await message.channel.send(f"***{message.author.display_name} just said a forbidden word,*** but I was unable to bonk them :(")

            else:
                await message.channel.send(f"***{message.author.display_name} just got bonked for saying a forbidden word.***{dmed}")

                # Create a task that will re-add the member's roles if they rejoin.
                bot_permissions = message.guild.me.guild_permissions
                if bot_permissions.manage_nicknames and bot_permissions.manage_roles:
                    self.bot.loop.create_task(self.revert_member_on_rejoin(message.author))

        log.info(f"[AutoMod] Bonked {message.author} (ID: {message.author.id}) for saying forbidden word: {word}")

        guild_log = await self.bot.get_guild_log(message.guild.id)
        if guild_log:
            em = discord.Embed(
                title="[AutoMod] Member Bonked",
                description=message.author.mention,
                color=discord.Color.purple()
            )
            em.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
            em.add_field(name="Word", value=word)
            em.add_field(name="Message", value=f"[Jump to message!]({message.jump_url})")
            em.add_field(name="Account Created", value=humantime.fulltime(message.author.created_at), inline=False)

            if message.author.roles[1:]:
                roles = ""
                for role in message.author.roles[1:]:
                    if len(roles + f"{role.mention} ") > 1012:
                        roles += "...and too many more to show"
                        break
                    roles += f"{role.mention} "
            else:
                roles = "No roles"

            em.add_field(name="Roles", value=roles, inline=False)

            await guild_log.log_automod_action(embed=em)

    async def detect_forbidden_word(self, message):
        """Detects if a forbidden word is in a message and takes appropriate action."""

        if message.author.bot:
            return

        if not message.guild:
            return

        bot_permissions = message.guild.me.guild_permissions
        if not bot_permissions.kick_members or not bot_permissions.create_instant_invite:
            return

        settings = await self.get_guild_settings(message.guild.id)

        if not settings:
            return

        if not settings.forbidden_words:
            return

        # if await self.bot.is_owner(message.author):
        #     return

        # remove whitespace.
        content = "".join(message.content.split())

        for word in settings.forbidden_words:
            if word in content.lower():
                await self.bonk_member(message, word)
                break

    @commands.Cog.listener("on_message")
    async def on_message_forbidden_detector(self, message):
        await self.detect_forbidden_word(message)

    @commands.Cog.listener("on_message_edit")
    async def on_message_edit_forbidden_detector(self, before, after):
        # ignore other updates to messages (embeds, pins, etc)
        if before.content == after.content:
            return

        await self.detect_forbidden_word(after)


def setup(bot):
    bot.add_cog(Moderation(bot))
