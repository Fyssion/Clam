import datetime

import discord
from discord.ext import commands

from .utils import cache, checks, db, humantime


class GuildLogsTable(db.Table, table_name="guild_logs"):
    id = db.Column(db.Integer(big=True), primary_key=True)
    channel_id = db.Column(db.Integer(big=True))

    log_joins = db.Column(db.Boolean, default=True)
    log_leaves = db.Column(db.Boolean, default=False)
    log_member_actions = db.Column(db.Boolean, default=False)
    log_automod_actions = db.Column(db.Boolean, default=True)
    log_mod_actions = db.Column(db.Boolean, default=False)


class GuildLog:
    @classmethod
    def from_record(cls, record, bot):
        self = cls()
        self.bot = bot

        self.id = record["id"]
        self.channel_id = record["channel_id"]
        self.log_joins = record["log_joins"]
        self.log_leaves = record["log_leaves"]
        self.log_member_actions = record["log_member_actions"]
        self.log_automod_actions = record["log_automod_actions"]
        self.log_mod_actions = record["log_mod_actions"]

        self.options = {
            "log_joins": self.log_joins,
            "log_leaves": self.log_leaves,
            "log_member_actions": self.log_member_actions,
            "log_automod_actions": self.log_automod_actions,
            "log_mod_actions": self.log_mod_actions
        }

        return self

    @property
    def guild(self):
        return self.bot.get_guild(self.id)

    @property
    def channel(self):
        guild = self.guild
        if not guild:
            return None
        return self.guild.get_channel(self.channel_id)

    async def log_automod_action(self, **message_kwargs):
        channel = self.channel

        if not channel:
            return

        if not self.log_automod_actions:
            return

        await channel.send(**message_kwargs)

    async def log_mod_action(self, **message_kwargs):
        channel = self.channel

        if not channel:
            return

        if not self.log_mod_actions:
            return

        await channel.send(**message_kwargs)


class Log(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.emoji = "\N{CLIPBOARD}"

    @cache.cache()
    async def get_guild_log(self, guild_id):
        query = "SELECT * FROM guild_logs WHERE id=$1;"
        record = await self.bot.pool.fetchrow(query, guild_id)

        if not record:
            return None

        return GuildLog.from_record(record, self.bot)

    async def send_log_info(self, ctx):
        log = await self.get_guild_log(ctx.guild.id)

        if not log:
            return await ctx.send(
                "**Logging is disabled for this server.**\n"
                f"Enable it with `{ctx.prefix}log <channel>`"
            )

        options = [f"{ctx.tick(v)} {k[4:].replace('_', ' ')}" for k, v in log.options.items()]
        options = "\n".join(options)

        message = f"**Logging is enabled**\nChannel: {log.channel.mention}\nLogging:\n{options}"

        await ctx.send(message)

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(manage_guild=True)
    async def log(self, ctx, *, channel: discord.TextChannel = None):
        """Control logging to a channel

        To set the logging channel, use this command with the log channel.
        Otherwise, use `{prefix}log create`

        Log options are listed below. To learn more about a
        specific option, use `{prefix}help log <option>`.
        """
        if not channel:
            return await self.send_log_info(ctx)

        query = """INSERT INTO guild_logs (id, channel_id)
                   VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET
                        channel_id=excluded.channel_id;
                """
        await ctx.db.execute(query, ctx.guild.id, channel.id)

        await ctx.send(ctx.tick(True, f"Now logging member joins and AutoMod actions to {channel.mention}. "
                                      f"For more info on changing what gets logged, see `{ctx.prefix}help log`"))
        self.get_guild_log.invalidate(self, ctx.guild.id)

    @log.command(name="create")
    @checks.has_permissions(manage_guild=True, manage_channels=True)
    @commands.bot_has_permissions(view_audit_log=True)
    async def log_create(self, ctx):
        """Creates a log channel for you."""

        overwrites = {
            ctx.guild.me: discord.PermissionOverwrite(
                read_messages=True, send_messages=True, embed_links=True, manage_channels=True
            )
        }

        reason = f"Log channel creation by {ctx.author} (ID: {ctx.author.id})"
        try:
            channel = await ctx.guild.create_text_channel(name="log", overwrites=overwrites, reason=reason)
        except discord.Forbidden:
            return await ctx.send(ctx.tick(False, "I don't have permissions to create channels."))
        except discord.HTTPException:
            return await ctx.send("I couldn't create the log channel. Try creating it yourself.")

        query = """INSERT INTO guild_logs (id, channel_id)
                   VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET
                        channel_id=excluded.channel_id;
                """
        await ctx.db.execute(query, ctx.guild.id, channel.id)

        await ctx.send(ctx.tick(True, f"Now logging member joins/leaves and AutoMod actions to {channel.mention}."
                                      f"For more info on changing what gets logged, see `{ctx.prefix}help log`"))
        self.get_guild_log.invalidate(self, ctx.guild.id)

    @log.command(name="disable")
    @checks.has_permissions(manage_guild=True)
    async def log_disable(self, ctx):
        """Disables logging for this server."""
        query = """DELETE FROM guild_logs
                   WHERE id=$1
                   RETURNING guild_logs.id;
                """
        result = await ctx.db.fetchval(query, ctx.guild.id)

        if not result:
            return await ctx.send("Logging is not enabled for this server.")

        await ctx.send(ctx.tick(True, "Disabled logging for this server."))
        self.get_guild_log.invalidate(self, ctx.guild.id)

    async def toggle_logging_option(self, ctx, option, human_friendly_option):
        guild_log = await self.get_guild_log(ctx.guild.id)
        if not guild_log:
            return await ctx.send("Logging is not enabled.")

        query = f"""UPDATE guild_logs
                   SET {option}=(NOT {option})
                   WHERE id=$1
                   RETURNING {option};
                """

        final = await ctx.db.fetchval(query, ctx.guild.id)

        value = "Enabled" if final else "Disabled"
        await ctx.send(ctx.tick(True, f"{value} {human_friendly_option} logging."))
        self.get_guild_log.invalidate(self, ctx.guild.id)

    @log.command(name="joins")
    @checks.has_permissions(manage_guild=True)
    async def log_joins(self, ctx):
        """Enable/disable member join logging"""
        await self.toggle_logging_option(ctx, "log_joins", "join")

    @log.command(name="leaves")
    @checks.has_permissions(manage_guild=True)
    @commands.bot_has_permissions(view_audit_log=True)
    async def log_leaves(self, ctx):
        """Enable/disable member leave logging"""
        await self.toggle_logging_option(ctx, "log_leaves", "leave")

    @log.command(name="member_actions")
    @checks.has_permissions(manage_guild=True)
    async def log_member_actions(self, ctx):
        """Enable/disable member action logging

        Member actions include:
        - message edits
        - message deletions
        """
        await self.toggle_logging_option(ctx, "log_member_actions", "member action")

    @log.command(name="automod_actions")
    @checks.has_permissions(manage_guild=True)
    async def log_automod_actions(self, ctx):
        """Enable/disable AutoMod action logging

        AutoMod actions are automatic actions performed by AutoMod.
        For more info on AutoMod, see `{prefix}help automod`
        """
        await self.toggle_logging_option(ctx, "log_automod_actions", "AutoMod action")

    @log.command(name="mod_actions")
    @checks.has_permissions(manage_guild=True)
    @commands.bot_has_permissions(view_audit_log=True)
    async def log_mod_actions(self, ctx):
        """Enable/disable mod action logging

        Mod actions include:
        - kicks
        - bans
        - tempbans (and tempban expirations)
        - unbans
        - mutes
        - tempmutes (and tempmute expirations)
        - unmutes

        Mute actions are only logged if a mute role is set.
        Use `{prefix}mute role set <role>` to set a mute role.

        This is useful if you want a permanent audit log.
        """
        await self.toggle_logging_option(ctx, "log_mod_actions", "mod action")

    # ====== #
    # EVENTS #
    # ====== #

    # MOD ACTIONS

    async def log_audit_log_entry(self, guild_log, entry, emoji, *, action=None):
        action = action or entry.action.name.replace("_", " ").capitalize()
        em = discord.Embed(title=f"{emoji} [Mod Action] {action}", color=discord.Color.purple())
        em.add_field(name="User", value=entry.target, inline=False)
        if entry.reason:
            em.add_field(name="Reason", value=entry.reason, inline=False)
        em.add_field(
            name="Responsible Moderator",
            value=f"{entry.user.mention} | {entry.user} (ID: {entry.user.id})",
            inline=False
        )

        if guild_log.channel:
            await guild_log.channel.send(embed=em)

    async def log_mod_action(self, guild, emoji, valid_actions, *, action=None, can_be_me=True):
        guild_log = await self.get_guild_log(guild.id)
        if not guild_log or not guild_log.log_mod_actions:
            return

        entries = await guild.audit_logs(limit=1).flatten()

        if not entries:
            return

        entry = entries[0]

        if entry.action not in valid_actions:
            return

        if not can_be_me and entry.user == self.bot.user:
            return

        await self.log_audit_log_entry(guild_log, entry, emoji, action=action)

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        await self.log_mod_action(guild, "\N{HAMMER}", (discord.AuditLogAction.ban,), can_be_me=False)

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user):
        await self.log_mod_action(guild, "\N{SPARKLES}", (discord.AuditLogAction.unban,), can_be_me=False)

    @commands.Cog.listener("on_member_remove")
    async def on_member_kick(self, member):
        await self.log_mod_action(member.guild, "\N{BOXING GLOVE}", (discord.AuditLogAction.kick,), can_be_me=False)

    @commands.Cog.listener("on_member_update")
    async def on_member_update(self, before, after):
        if before.roles != after.roles:
            mod = self.bot.get_cog("Moderation")
            if not mod:
                return

            settings = await mod.get_guild_settings(before.guild.id)
            if not settings or not settings.mute_role:
                return

            # unmute (mute role removed)
            if set(before.roles) - set(after.roles) == {settings.mute_role}:
                emoji = "\N{SPEAKER WITH THREE SOUND WAVES}"
                await self.log_mod_action(
                    before.guild, emoji,
                    (discord.AuditLogAction.member_role_update,),
                    action="Unmute",
                    can_be_me=False
                )

            # mute (mute role added)
            elif set(after.roles) - set(before.roles) == {settings.mute_role}:
                emoji = "\N{SPEAKER WITH CANCELLATION STROKE}"
                await self.log_mod_action(
                    before.guild,
                    emoji,
                    (discord.AuditLogAction.member_role_update,),
                    action="Mute",
                    can_be_me=False
                )

    # MEMBER ACTIONS

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        guild_log = await self.get_guild_log(message.guild.id)
        if not guild_log:
            return

        if not guild_log.log_member_actions:
            return

        if guild_log.channel_id == message.channel.id:
            return

        if message.author == self.bot.user:
            return

        em = discord.Embed(
            title="Message Deleted",
            color=discord.Color.dark_gold(),
            timestamp=message.created_at,
        )

        em.set_author(name=str(message.author), icon_url=message.author.avatar_url)
        em.set_footer(text="Message originally sent")

        em.add_field(name="Channel", value=message.channel.mention, inline=False)
        content = message.content[:1000] + ("..." if len(message.content) > 1000 else "")
        em.add_field(name="Content", value=content, inline=False)

        if guild_log.channel:
            await guild_log.channel.send(embed=em)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if not before.guild:
            return

        guild_log = await self.get_guild_log(before.guild.id)
        if not guild_log:
            return

        if not guild_log.log_member_actions:
            return

        if guild_log.channel_id == before.channel.id:
            return

        if before.author == self.bot.user:
            return

        if before.content == after.content:
            return

        em = discord.Embed(
            title="Message Edited",
            description=f"[Jump to message!]({before.jump_url})",
            color=discord.Color.blue(),
            timestamp=before.created_at,
        )

        em.set_author(name=str(before.author), icon_url=before.author.avatar_url)
        em.set_footer(text="Message originally sent")

        em.add_field(name="Channel", value=before.channel.mention, inline=False)
        em.add_field(name="Before", value=before.content[:1000] + ("..." if len(before.content) > 1000 else ""), inline=False)
        em.add_field(name="After", value=after.content[:1000] + ("..." if len(after.content) > 1000 else ""), inline=False)

        if guild_log.channel:
            await guild_log.channel.send(embed=em)

    # JOINS/LEAVES

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild_id = member.guild.id

        guild_log = await self.get_guild_log(guild_id)
        if not guild_log:
            return

        if not guild_log.log_joins:
            return

        em = discord.Embed(title="Member Joined", description=member.mention, color=0x53DDA4)  # green
        em.set_author(name=str(member), icon_url=member.avatar_url)
        em.add_field(name="ID", value=member.id)
        em.add_field(name="Joined At", value=humantime.date(member.joined_at))
        em.add_field(
            name="Account Created",
            value=humantime.fulltime(member.created_at),
            inline=False,
        )

        moderation = self.bot.get_cog("Moderation")

        if moderation:
            settings = await moderation.get_guild_settings(guild_id)
            if settings:
                now = datetime.datetime.utcnow()

                is_new = member.created_at > (now - datetime.timedelta(days=7))
                checker = moderation._spam_check[guild_id]

                if checker.is_fast_join(member):
                    em.color = 0xDD5F53  # red
                    if is_new:
                        em.title = "Member Joined (Very New Member)"
                else:
                    em.color = 0x53DDA4  # green

                    if is_new:
                        em.color = 0xDDA453  # yellow
                        em.title = "Member Joined (Very New Member)"

        if guild_log.channel:
            await guild_log.channel.send(embed=em)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        guild_id = member.guild.id

        guild_log = await self.get_guild_log(guild_id)
        if not guild_log:
            return

        if not guild_log.log_leaves:
            return

        no_nos = (
            discord.AuditLogAction.member_prune,
            discord.AuditLogAction.kick,
            discord.AuditLogAction.ban
            )

        audit_logs = await member.guild.audit_logs(limit=1).flatten()
        five_seconds_ago = datetime.datetime.utcnow() - datetime.timedelta(seconds=5)
        if audit_logs and audit_logs[0].action in no_nos and audit_logs[0].created_at >= five_seconds_ago:
            return  # don't log kicks/prunes

        em = discord.Embed(title="Member Left", description=member.mention, color=discord.Color.orange())
        em.set_author(name=str(member), icon_url=member.avatar_url)
        em.add_field(name="ID", value=member.id)
        em.add_field(
            name="Account Created",
            value=humantime.fulltime(member.created_at),
            inline=False,
        )

        if guild_log.channel:
            await guild_log.channel.send(embed=em)


def setup(bot):
    bot.add_cog(Log(bot))
