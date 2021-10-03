import asyncio

import discord
from discord.ext import commands

from .utils import db, emojis
from .utils.errors import PrivateCog


class ArchivedChannels(db.Table, table_name="archived_channels"):
    id = db.PrimaryKeyColumn()

    channel_id = db.Column(db.Integer(big=True))
    category_id = db.Column(db.Integer(big=True))
    permissions = db.Column(db.JSON, default="'{}'::jsonb")
    archived_at = db.Column(db.Datetime(), default="now() at time zone 'utc'")


CCS_ID = 454469821376102410
CCS_EMOJI = "<:ccs:728343380773437440>"

GENERAL = 617125050075709440
ARCHIVE_CATEGORY = 454471313998872576

VERIFIED = 454470860577701898
CODER = 623295800088461322
BOT = 454471729776033802
RETIRED = 617364905909026857
RETIRED_EMOJI = "\N{CROSS MARK}"


class RoleNotFound(commands.CommandError):
    pass


class ArchivedChannelNotFound(commands.CommandError):
    pass


class CCS(commands.Cog):
    """Commands for Fyssion's personal server."""

    def __init__(self, bot):
        self.bot = bot
        self.emoji = CCS_EMOJI
        self.log = self.bot.log
        self.private = True

    async def cog_check(self, ctx):
        if not ctx.guild or ctx.guild.id != CCS_ID:
            raise PrivateCog("This is a private cog.")

        return True

    async def toggle_role(self, member, role_id):
        """Toggle a role for a member given a role id.

        Parameters
        -----------
        member:
            The member to assign or remove the role to/from
        role_id:
            The id of the role to assign or remove

        Returns
        --------
        added_role :class:`bool`
            Whether or not the role was added. True = added, False = removed

        Raises
        -------
        RoleNotFound:
            When a role with the given ID is not found in the member's guild
        """
        role = member.guild.get_role(role_id)

        if not role:
            raise RoleNotFound(f"Role with ID {role_id} not found in CCS guild.")

        if role in member.roles:
            await member.remove_roles(role)
            return False

        else:
            await member.add_roles(role)
            return True

    @commands.command(aliases=["unretire"])
    @commands.has_role(CODER)
    @commands.bot_has_permissions(manage_roles=True, manage_nicknames=True)
    async def retire(self, ctx, *, member: discord.Member):
        """Retires or unretires a bot."""

        if not member.bot:
            raise commands.BadArgument("Member must be a bot.")

        added_role = await self.toggle_role(member, RETIRED)

        if added_role:
            await member.edit(nick=f"{RETIRED_EMOJI}{member.display_name}")
            await ctx.send(f"{ctx.tick(True)} Retired `{member}`")

        else:
            await member.edit(nick=member.display_name.replace(RETIRED_EMOJI, ""))
            await ctx.send(f"{ctx.tick(True)} Unretired `{member}`")

    @commands.command()
    @commands.has_role(CODER)
    @commands.bot_has_guild_permissions(move_members=True)
    async def kickvoice(self, ctx, *, member: discord.Member):
        """Kicks a bot from a voice channel.

        This command is for testing music bots.
        """

        if not member.bot:
            raise commands.BadArgument("You can only kick bots.")

        if not member.voice:
            raise commands.BadArgument("That bot isn't in a voice channel.")

        await member.move_to(None, reason=f"kickvoice command by {ctx.author} (ID: {ctx.author.id}) on {member} (ID: {member.id})")

        await ctx.send(ctx.tick(True, "Kicked bot."))

    async def unarchive_channel(self, ctx, channel):
        query = """DELETE FROM archived_channels
                   WHERE channel_id=$1
                   RETURNING category_id, permissions;
                """

        result = await ctx.db.fetchrow(query, channel.id)

        if not result:
            await ctx.send(
                "Channel not found in archive database.\n"
                "Which category would you like to move this channel to?"
            )

            def check(ms):
                return ms.author == ctx.author and ms.channel == ctx.channel

            try:
                message = await self.bot.wait_for("message", check=check, timeout=180)
                category = await commands.CategoryChannelConverter().convert(
                    ctx, message.content
                )

            except asyncio.TimeoutError:
                category = None
                await ctx.send("You timed out. Moving to None.")

            verified = ctx.guild.get_role(VERIFIED)

            if not verified:
                return RoleNotFound("Verified role not found.")

            overwrites = {
                ctx.guild.default_role: discord.PermissionOverwrite(
                    read_messages=False
                ),
                verified: discord.PermissionOverwrite(read_messages=True),
            }

        else:
            category_id, raw_overwrites = result

            category = self.bot.get_channel(category_id)

            overwrites = {}

            for entity_id in raw_overwrites:
                entity = ctx.guild.get_member(int(entity_id)) or ctx.guild.get_role(int(entity_id))

                pair = [discord.Permissions(p) for p in raw_overwrites[entity_id]]

                overwrite = discord.PermissionOverwrite.from_pair(*pair)

                if entity is not None:
                    overwrites[entity] = overwrite

        await channel.edit(category=category, overwrites=overwrites)

        await ctx.send(f"{ctx.tick(True)} Unarchived channel `{channel}`")

    async def archive_channel(self, ctx, channel):
        old_category_id = channel.category.id or None
        old_permissions = {}

        for entity in channel.overwrites:
            pair = [p.value for p in channel.overwrites[entity].pair()]
            old_permissions[entity.id] = pair

        category = self.bot.get_channel(ARCHIVE_CATEGORY)

        await channel.edit(overwrites=category.overwrites, category=category)

        query = """INSERT INTO archived_channels (channel_id, category_id, permissions)
                   VALUES ($1, $2, $3);
                """

        await ctx.db.execute(query, channel.id, old_category_id, old_permissions)

        await ctx.send(f"{ctx.tick(True)} Archived channel `{channel}`")

    @commands.command(aliases=["archive", "unarchive"])
    @commands.is_owner()
    @commands.bot_has_permissions(manage_channels=True)
    async def toggle(self, ctx, *, channel: discord.TextChannel = None):
        """Toggles a channel as archived or unarchived.

        This moves the channel between the archive category
        and the category that it previously occupied.
        """

        channel = channel or ctx.channel

        if channel.category and channel.category.id == ARCHIVE_CATEGORY:
            await self.unarchive_channel(ctx, channel)

        else:
            await self.archive_channel(ctx, channel)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if self.bot.debug:
            return

        if member.guild.id != CCS_ID:
            return

        if not member.bot:
            return

        verified = member.guild.get_role(VERIFIED)
        bot = member.guild.get_role(BOT)

        await member.add_roles(verified, bot)

        general = member.guild.get_channel(GENERAL)

        await general.send(f"{emojis.GREEN_TICK} Added bot `{member}`")


def setup(bot):
    bot.add_cog(CCS(bot))
