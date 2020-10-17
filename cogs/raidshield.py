"""
This file contains modified source code from Rapptz/RoboDanny
https://github.com/Rapptz/RoboDanny/blob/b16a3f566af27263d4dd92bcd898a7af3b95ac53/cogs/mod.py

This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. You can obtain a copy of the MPL at http://mozilla.org/MPL/2.0/.
"""


from discord.ext import commands, tasks
from .utils import checks, human_time, cache
from collections import defaultdict

import discord
import enum
import datetime
import asyncio
import argparse
import logging
import asyncpg


log = logging.getLogger(__name__)

# Misc utilities


class Arguments(argparse.ArgumentParser):
    def error(self, message):
        raise RuntimeError(message)


class RaidMode(enum.Enum):
    off = 0
    on = 1
    strict = 2

    def __str__(self):
        return self.name


# Configuration


class ModConfig:
    __slots__ = (
        "raid_mode",
        "id",
        "bot",
        "broadcast_channel_id",
        "mention_count",
        "safe_mention_channel_ids",
    )

    @classmethod
    async def from_record(cls, record, bot):
        self = cls()

        # the basic configuration
        self.bot = bot
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


# Converters


def can_execute_action(ctx, user, target):
    return (
        user.id == ctx.bot.owner_id
        or user == ctx.guild.owner
        or user.top_role > target.top_role
    )


class MemberNotFound(Exception):
    pass


async def resolve_member(guild, member_id):
    member = guild.get_member(member_id)
    if member is None:
        if guild.chunked:
            raise MemberNotFound()
        try:
            member = await guild.fetch_member(member_id)
        except discord.NotFound:
            raise MemberNotFound() from None
    return member


class MemberID(commands.Converter):
    async def convert(self, ctx, argument):
        try:
            m = await commands.MemberConverter().convert(ctx, argument)
        except commands.BadArgument:
            try:
                member_id = int(argument, base=10)
                m = await resolve_member(ctx.guild, member_id)
            except ValueError:
                raise commands.BadArgument(
                    f"{argument} is not a valid member or member ID."
                ) from None
            except MemberNotFound:
                # hackban case
                return type(
                    "_Hackban",
                    (),
                    {"id": member_id, "__str__": lambda s: f"Member ID {s.id}"},
                )()

        if not can_execute_action(ctx, ctx.author, m):
            raise commands.BadArgument(
                "You cannot do this action on this user due to role hierarchy."
            )
        return m


class BannedMember(commands.Converter):
    async def convert(self, ctx, argument):
        if argument.isdigit():
            member_id = int(argument, base=10)
            try:
                return await ctx.guild.fetch_ban(discord.Object(id=member_id))
            except discord.NotFound:
                raise commands.BadArgument(
                    "This member has not been banned before."
                ) from None

        ban_list = await ctx.guild.bans()
        entity = discord.utils.find(lambda u: str(u.user) == argument, ban_list)

        if entity is None:
            raise commands.BadArgument("This member has not been banned before.")
        return entity


class ActionReason(commands.Converter):
    async def convert(self, ctx, argument):
        ret = f"{ctx.author} (ID: {ctx.author.id}): {argument}"

        if len(ret) > 512:
            reason_max = 512 - len(ret) + len(argument)
            raise commands.BadArgument(
                f"Reason is too long ({len(argument)}/{reason_max})"
            )
        return ret


def safe_reason_append(base, to_append):
    appended = base + f"({to_append})"
    if len(appended) > 512:
        return base
    return appended


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


# Checks


class NoMuteRole(commands.CommandError):
    def __init__(self):
        super().__init__("This server does not have a mute role set up.")


def can_mute():
    async def predicate(ctx):
        is_owner = await ctx.bot.is_owner(ctx.author)
        if ctx.guild is None:
            return False

        if not ctx.author.guild_permissions.manage_roles and not is_owner:
            return False

        # This will only be used within this cog.
        ctx.guild_config = config = await ctx.cog.get_guild_config(ctx.guild.id)
        role = config and config.mute_role
        if role is None:
            raise NoMuteRole()
        return ctx.author.top_role > role

    return commands.check(predicate)


# The actual cog


class RaidShield(commands.Cog, name="Raid Sheild"):
    """Raid detection and prevention system from RoboDanny"""

    def __init__(self, bot):
        self.bot = bot
        self.emoji = "\N{SHIELD}"

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
        if isinstance(error, commands.BadArgument):
            await ctx.send(error)
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
        elif isinstance(error, NoMuteRole):
            await ctx.send(error)

    async def bulk_insert(self):
        query = """UPDATE guild_settings
                   SET muted_members = x.result_array
                   FROM jsonb_to_recordset($1::jsonb) AS
                   x(guild_id BIGINT, result_array BIGINT[])
                   WHERE guild_settings.id = x.guild_id;
                """

        if not self._data_batch:
            return

        final_data = []
        for guild_id, data in self._data_batch.items():
            # If it's touched this function then chances are that this has hit cache before
            # so it's not actually doing a query, hopefully.
            config = await self.get_guild_config(guild_id)
            as_set = config.muted_members
            for member_id, insertion in data:
                func = as_set.add if insertion else as_set.discard
                func(member_id)

            final_data.append({"guild_id": guild_id, "result_array": list(as_set)})
            self.get_guild_config.invalidate(self, guild_id)

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

    @cache.cache()
    async def get_guild_config(self, guild_id):
        query = """SELECT * FROM guild_settings WHERE guild_id=$1;"""
        async with self.bot.pool.acquire(timeout=300.0) as con:
            record = await con.fetchrow(query, guild_id)
            if record is not None:
                return await ModConfig.from_record(record, self.bot)
            return None

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
        config = await self.get_guild_config(guild_id)
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

            log.info(
                f"Member {author} (ID: {author.id}) has been autobanned from guild ID {guild_id}"
            )

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild_id = member.guild.id
        config = await self.get_guild_config(guild_id)
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
            value=human_time.human_timedelta(member.created_at),
            inline=False,
        )

        if config.broadcast_channel:
            try:
                await config.broadcast_channel.send(embed=e)
            except discord.Forbidden:
                async with self._disable_lock:
                    await self.disable_raid_mode(guild_id)

    @commands.command(aliases=["newmembers"])
    @commands.guild_only()
    async def newusers(self, ctx, *, count=5):
        """Tells you the newest members of the server.

        This is useful to check if any suspicious members have
        joined.

        The count parameter can only be up to 25.
        """
        count = max(min(count, 25), 5)

        if not ctx.guild.chunked:
            await self.bot.request_offline_members(ctx.guild)

        members = sorted(ctx.guild.members, key=lambda m: m.joined_at, reverse=True)[
            :count
        ]

        e = discord.Embed(title="New Members", colour=discord.Colour.green())

        for member in members:
            body = f"Joined {human_time.human_timedelta(member.joined_at)}\nCreated {human_time.human_timedelta(member.created_at)}"
            e.add_field(name=f"{member} (ID: {member.id})", value=body, inline=False)

        await ctx.send(embed=e)

    @commands.group(aliases=["raids"], invoke_without_command=True)
    @checks.has_permissions(manage_guild=True)
    async def raid(self, ctx):
        """Controls raid mode on the server.

        Calling this command with no arguments will show the current raid
        mode information.

        You must have Manage Server permissions to use this command or
        its subcommands.
        """

        query = "SELECT raid_mode, broadcast_channel FROM guild_settings WHERE guild_id=$1;"

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

        query = """INSERT INTO guild_settings (guild_id, raid_mode, broadcast_channel)
                   VALUES ($1, $2, $3) ON CONFLICT (guild_id)
                   DO UPDATE SET
                        raid_mode = EXCLUDED.raid_mode,
                        broadcast_channel = EXCLUDED.broadcast_channel;
                """

        await ctx.db.execute(query, ctx.guild.id, RaidMode.on.value, channel.id)
        self.get_guild_config.invalidate(self, ctx.guild.id)
        await ctx.send(
            f"Raid mode enabled. Broadcasting join messages to {channel.mention}."
        )

    async def disable_raid_mode(self, guild_id):
        query = """INSERT INTO guild_settings (guild_id, raid_mode, broadcast_channel)
                   VALUES ($1, $2, NULL) ON CONFLICT (guild_id)
                   DO UPDATE SET
                        raid_mode = EXCLUDED.raid_mode,
                        broadcast_channel = NULL;
                """

        await self.bot.pool.execute(query, guild_id, RaidMode.off.value)
        self._spam_check.pop(guild_id, None)
        self.get_guild_config.invalidate(self, guild_id)

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

        query = """INSERT INTO guild_settings (guild_id, raid_mode, broadcast_channel)
                   VALUES ($1, $2, $3) ON CONFLICT (guild_id)
                   DO UPDATE SET
                        raid_mode = EXCLUDED.raid_mode,
                        broadcast_channel = EXCLUDED.broadcast_channel;
                """

        await ctx.db.execute(query, ctx.guild.id, RaidMode.strict.value, channel.id)
        self.get_guild_config.invalidate(self, ctx.guild.id)
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
                       WHERE guild_id=$1;
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
            query = """UPDATE guild_settings SET mention_count = NULL WHERE guild_id=$1;"""
            await ctx.db.execute(query, ctx.guild.id)
            self.get_guild_config.invalidate(self, ctx.guild.id)
            return await ctx.send("Auto-banning members has been disabled.")

        if count <= 3:
            await ctx.send(
                "\N{NO ENTRY SIGN} Auto-ban threshold must be greater than three."
            )
            return

        query = """INSERT INTO guild_settings (guild_id, mention_count, safe_mention_channel_ids)
                   VALUES ($1, $2, '{}')
                   ON CONFLICT (guild_id) DO UPDATE SET
                       mention_count = $2;
                """
        await ctx.db.execute(query, ctx.guild.id, count)
        self.get_guild_config.invalidate(self, ctx.guild.id)
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
                   WHERE guild_id = $1;
                """

        if len(channels) == 0:
            return await ctx.send("Missing channels to ignore.")

        channel_ids = [c.id for c in channels]
        await ctx.db.execute(query, ctx.guild.id, channel_ids)
        self.get_guild_config.invalidate(self, ctx.guild.id)
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
                   WHERE guild_id = $1;
                """

        await ctx.db.execute(query, ctx.guild.id, [c.id for c in channels])
        self.get_guild_config.invalidate(self, ctx.guild.id)
        await ctx.send("Updated mentionspam ignore list.")


def setup(bot):
    bot.add_cog(RaidShield(bot))
