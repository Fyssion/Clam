from discord.ext import commands, menus
import discord

import asyncpg
import enum
import asyncio
import json
import logging
from jishaku.models import copy_context_with

from .utils import db, checks, colors


log = logging.getLogger("clam.reactionroles")


class SelfRolesTable(db.Table, table_name="selfroles"):
    id = db.PrimaryKeyColumn()

    guild_id = db.Column(db.Integer(big=True))
    role_id = db.Column(db.Integer(big=True))
    description = db.Column(db.String)
    created_at = db.Column(db.Datetime, default="now() at time zone 'utc'")

    @classmethod
    def create_table(cls, *, exists_ok=True):
        statement = super().create_table(exists_ok=exists_ok)
        sql = "CREATE UNIQUE INDEX IF NOT EXISTS roles_uniq_idx ON selfroles (guild_id, role_id);"
        return statement + "\n" + sql


class ReactionRolesTable(db.Table, table_name="reactionroles"):
    id = db.PrimaryKeyColumn()

    guild_id = db.Column(db.Integer(big=True))
    channel_id = db.Column(db.Integer(big=True))
    message_id = db.Column(db.Integer(big=True))

    # mapping of emojis to roles
    emojis_and_roles = db.Column(db.JSON, default="'{}'::jsonb")


class SelfRole:
    @classmethod
    def from_record(cls, record, bot):
        self = cls()

        self.bot = bot

        self.id = record["id"]
        self.guild_id = record["guild_id"]
        self.role_id = record["role_id"]
        self.description = record["description"]
        self.created_at = record["created_at"]

        return self

    @property
    def guild(self):
        return self.bot.get_guild(self.guild_id)

    @property
    def role(self):
        return self.guild.get_role(self.role_id)

    @classmethod
    async def convert(cls, ctx, arg):
        role = await commands.RoleConverter().convert(ctx, arg)

        query = "SELECT * FROM selfroles WHERE role_id=$1 AND guild_id=$2;"
        record = await ctx.db.fetchrow(query, role.id, ctx.guild.id)

        if not record:
            escaped = discord.utils.escape_mentions(arg)
            raise commands.BadArgument(f"Selfrole '{escaped}' not found.")

        return cls.from_record(record, ctx.bot)


class SelfRoleDescription(commands.Converter):
    async def convert(self, ctx, arg):
        if len(arg) > 64:
            raise commands.BadArgument(
                f"Selfrole description must be 64 characters or less. ({len(arg)}/64)"
            )

        return arg


class ReactionroleEmojiConverter(commands.Converter):
    async def convert(self, ctx, arg):
        if arg == f"{ctx.prefix}done":
            return None

        if len(arg) < 3:
            raise commands.BadArgument("Invalid format. Please use the correct format.")

        args = arg.split(" ")

        if len(args) < 2:
            raise commands.BadArgument("Invalid format. Please use the correct format.")

        emoji = args[0]
        role_name = " ".join(args[1:])

        with open("assets/emoji_map.json", "r") as f:
            emoji_map = json.load(f)

        passed = False

        if emoji not in emoji_map.values():
            for e in ctx.guild.emojis:
                if str(e) == emoji:
                    passed = True
                    break

            if not passed:
                raise commands.BadArgument(
                    "Invalid emoji. Provide a default emoji or an emoji in this guild."
                )

        role = await commands.RoleConverter().convert(ctx, role_name)
        return emoji, role


class ReactionroleMenu(menus.Menu):
    def __init__(self, embed=None, **kwargs):
        self.embed = embed
        super().__init__(**kwargs)

    def reaction_check(self, payload):
        if payload.message_id != self.message.id:
            return False

        return payload.emoji in self.buttons

    async def send_initial_message(self, ctx, channel):
        return await channel.send(embed=self.embed)


class PromptResponse(enum.Enum):
    TIMED_OUT = 0
    CANCELLED = 1


class Selfroles(commands.Cog):
    """Assign roles to yourself through command or reaction.

    Mods must create a selfrole or set an existing role as a selfrole.
    """

    def __init__(self, bot):
        self.bot = bot
        self.emoji = "<:selfroles:784533393538154597>"

        self.active_menus = []
        self.bot.loop.create_task(self.start_reactionrole_menus())

    @commands.Cog.listener()
    async def on_guild_leave(self, guild):
        """Remove all selfroles of a guild when I leave it"""
        query = "DELETE FROM selfroles WHERE guild_id=$1"
        await self.bot.pool.execute(query, guild.id)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        """Remove selfrole reference when a role is deleted"""
        query = "DELETE FROM selfroles WHERE guild_id=$1 AND role_id=$2"
        await self.bot.pool.execute(query, role.guild.id, role.id)

    @commands.group(aliases=["role"], invoke_without_command=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def selfrole(self, ctx):
        """A set of commands to manage selfroles."""
        await ctx.send_help(ctx.command)

    @selfrole.command(name="add", aliases=["sub"])
    @commands.bot_has_permissions(manage_roles=True)
    async def selfrole_add(self, ctx, *, role: SelfRole):
        """Add a selfrole to yourself.

        The role specified must be a selfrole.
        """
        if not role.role:
            await ctx.send("That role doesn't seem to exist anymore. Contact a mod.")

        if role.role in ctx.author.roles:
            return await ctx.send("You already have this role.")

        try:
            await ctx.author.add_roles(role.role, reason="Selfrole addition")
        except discord.HTTPException:
            return await ctx.send("Failed to add role. Try again later?")

        await ctx.send(ctx.tick(True, f"Successfully added role `{role.role.name}`"))

    @selfrole.command(name="remove", aliases=["unsub"])
    @commands.bot_has_permissions(manage_roles=True)
    async def selfrole_remove(self, ctx, *, role: SelfRole):
        """Remove a selfrole from yourself.

        The role specified must be a selfrole.
        """
        if not role.role:
            await ctx.send("That role doesn't seem to exist anymore. Contact a mod.")

        if role.role not in ctx.author.roles:
            return await ctx.send("You don't have this role.")

        try:
            await ctx.author.remove_roles(role.role, reason="Selfrole removal")
        except discord.HTTPException:
            return await ctx.send("Failed to remove role. Try again later?")

        await ctx.send(ctx.tick(True, f"Successfully removed role `{role.role.name}`"))

    async def insert_selfrole(self, ctx, role, description):
        query = """INSERT INTO selfroles (guild_id, role_id, description)
                   VALUES ($1, $2, $3);
                """

        async with ctx.db.acquire() as con:
            async with con.transaction():
                try:
                    await ctx.db.execute(query, ctx.guild.id, role.id, description)
                except asyncpg.UniqueViolationError:
                    raise commands.BadArgument(
                        "There is already selfrole bound to that role."
                    ) from None

    async def delete_selfrole(self, ctx, role):
        query = """DELETE FROM selfroles
                   WHERE guild_id=$1 AND role_id=$2
                   RETURNING selfroles.id;
                """

        selfrole_id = await ctx.db.fetchval(query, ctx.guild.id, role.id)

        if not selfrole_id:
            escaped = discord.utils.escape_mentions(role.name)
            raise commands.BadArgument(f"Selfrole '{escaped}' not found.")

    @selfrole.command(name="create", aliases=["new"])
    @checks.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def selfrole_create(
        self, ctx, name, *, description: SelfRoleDescription = None
    ):
        """Create a new selfrole.

        Wrap the role name in quotes if it contains spaces.

        You must have the manage roles permission to use this command.
        """
        reason = f"Selfrole creation by {ctx.author} (ID: {ctx.author.id})"

        try:
            role = await ctx.guild.create_role(name=name, reason=reason)

        except discord.HTTPException:
            return await ctx.send("Failed to create role. Maybe try again later?")

        await self.insert_selfrole(ctx, role, description)

        await ctx.send(ctx.tick(True, f"Created selfrole `{role.name}`"))

    @selfrole.command(name="delete")
    @checks.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def selfrole_delete(self, ctx, *, role: discord.Role):
        """Delete a selfrole.

        This command **will delete the role.**
        If you want to unbind a selfrole without deleting it,
        use `selfrole unbind` instead.

        You must have the manage roles permission to use this command.
        """
        await self.delete_selfrole(ctx, role)

        try:
            await role.delete()
        except discord.HTTPException:
            return await ctx.send("Failed to delete role. Try deleting it manually.")

        await ctx.send(
            ctx.tick(True, "Successfully deleted selfrole and corresponding role")
        )

    @selfrole.command(name="set")
    @checks.has_permissions(manage_roles=True)
    async def selfrole_set(
        self, ctx, role: discord.Role, *, description: SelfRoleDescription = None
    ):
        """Set an existing role as a selfrole.

        Wrap the role name in quotes if it contains spaces.

        This is to be used when you want to convert a pre-existing role
        to a selfrole.

        You must have the manage roles permission to use this command.
        """
        await self.insert_selfrole(ctx, role, description)
        await ctx.send(ctx.tick(True, f"Bound new selfrole to `{role.name}`"))

    @selfrole.command(name="unbind")
    @checks.has_permissions(manage_roles=True)
    async def selfrole_unbind(self, ctx, *, role: discord.Role):
        """Unbind a selfrole from a role without deleting it.

        This is to be used when you don't want a role to be a selfrole,
        but you still want to keep the original role.

        You must have the manage roles permission to use this command.
        """
        await self.delete_selfrole(ctx, role)
        await ctx.send(ctx.tick(True, "Successfully unbound selfrole from role"))

    @selfrole.command(name="list", aliases=["all"])
    async def selfrole_list(self, ctx):
        """AView available selfroles in this server."""
        query = """SELECT role_id, description
                   FROM selfroles
                   WHERE guild_id=$1;
                """

        records = await ctx.db.fetch(query, ctx.guild.id)

        selfroles = []

        for role_id, description in records:
            role = ctx.guild.get_role(role_id)

            def format_role(name, description):
                if description:
                    return f"{name} - {description}"

                return name

            if not role:
                selfroles.append(format_role("***[unknown role]***", description))

            else:
                selfroles.append(format_role(f"**{role.name}**", description))

        em = discord.Embed(title="Available Selfroles", color=colors.PRIMARY)
        em.description = (
            f"To add a role to yourself, use `{ctx.prefix}selfrole add <role>`"
        )

        pages = ctx.embed_pages(selfroles, em)
        await pages.start(ctx)

    @commands.command(aliases=["roles"])
    async def selfroles(self, ctx):
        """Alias for selfrole list."""
        await ctx.invoke(self.selfrole_list)

    # reaction roles

    async def remove_guild_reactionroles(self, guild_id):
        query = "DELETE FROM reactionroles WHERE guild_id=$1;"
        await self.bot.pool.execute(query, guild_id)

    async def remove_channel_reactionroles(self, channel_id):
        query = "DELETE FROM reactionroles WHERE channel_id=$1;"
        await self.bot.pool.execute(query, channel_id)

    async def remove_reactionroles(self, channel_id, message_id):
        query = "DELETE FROM reactionroles WHERE channel_id=$1 AND message_id=$2;"
        await self.bot.pool.execute(query, channel_id, message_id)

    @commands.Cog.listener("on_guild_leave")
    async def cleanup_guild_reaectionroles(self, guild):
        await self.remove_guild_reactionroles(guild.id)

    @commands.Cog.listener("on_guild_channel_delete")
    async def cleanup_channel_reactionroles(self, channel):
        await self.remove_channel_reactionroles(channel.id)

    @commands.Cog.listener("on_raw_message_delete")
    async def cleanup_reactionroles(self, payload):
        await self.remove_reactionroles(payload.channel_id, payload.message_id)

    def create_button(self, ctx, emoji, role):
        async def action(menu, payload):
            if payload.user_id == self.bot.user.id:
                return

            guild_id = payload.guild_id

            guild = ctx.bot.get_guild(guild_id)
            if not guild:
                return

            new_role = guild.get_role(role.id)
            if not role:
                return

            member = guild.get_member(payload.user_id)
            if not member:
                return

            if member.bot:
                return

            if payload.event_type == "REACTION_REMOVE" and new_role in member.roles:
                await member.remove_roles(new_role, reason="Reactionrole removal")
                log.info(f"{guild}: Removed '{role}' role from {member}")

            elif payload.event_type == "REACTION_ADD":
                await member.add_roles(new_role, reason="Reactionrole addition")
                log.info(f"{guild}: Added '{role}' role to {member}")

        button = menus.Button(emoji=emoji, action=action)

        return button

    async def create_reactionrole_menu(self, ctx, emojis_and_roles):
        menu = ReactionroleMenu(timeout=None)
        description = (
            "Press a reaction to get the associated role!\n"
            "Press the reaction again to remove the role.\n\n"
        )
        options = []

        query = """SELECT role_id, description
                   FROM selfroles
                   WHERE guild_id=$1;
                """
        records = await ctx.db.fetch(query, ctx.guild.id)

        def format_role(name, description):
            if description:
                return f"{name} - {description}"
            return name

        for i, (emoji, role) in enumerate(emojis_and_roles):
            role_desc = None
            for role_id, desc in records:
                if role.id == role_id:
                    role_desc = desc
                    break

            options.append(f"{emoji} | {format_role(role.name, role_desc)}")
            menu.add_button(self.create_button(ctx, emoji, role))

        description += "\n".join(options)
        em = discord.Embed(
            title="Reaction Roles", description=description, color=colors.PRIMARY
        )
        footer_text = (
            "If you cannot see the reactions, try reloading with CTRL+R.\n"
            "If nothing happens when you click a reaction, try clicking it again."
        )
        warning_url = (
            "https://raw.githubusercontent.com/Fyssion/Clam/main/assets/warning.png"
        )
        em.set_footer(text=footer_text, icon_url=warning_url)
        menu.embed = em

        await menu.start(ctx)
        return menu

    async def start_reactionrole_menus(self):
        await self.bot.wait_until_ready()

        query = "SELECT * FROM reactionroles"
        records = await self.bot.pool.fetch(query)

        for record in records:
            guild = self.bot.get_guild(record["guild_id"])
            if not guild:
                self.bot.loop.create_task(
                    self.remove_guild_reactionroles(record["guild_id"])
                )
                continue

            channel = guild.get_channel(record["channel_id"])
            if not channel:
                self.bot.loop.create_task(
                    self.remove_channel_reactionroles(record["channel_id"])
                )
                continue

            try:
                message = await channel.fetch_message(record["message_id"])
            except discord.NotFound:
                self.bot.loop.create_task(
                    self.remove_reactionroles(
                        record["channel_id"], record["message_id"]
                    )
                )
                continue
            except Exception:
                continue

            # hehe this is just to make the menu start
            ctx = await self.bot.get_context(message)

            menu = ReactionroleMenu(timeout=None, message=message)

            for emoji, role_id in record["emojis_and_roles"]:
                role = guild.get_role(role_id)
                if not role:
                    continue
                menu.add_button(self.create_button(ctx, emoji, role))

            await menu.start(ctx, channel=channel)
            self.active_menus.append(menu)

        log.info("Started all reactionrole menus")

    @commands.group(aliases=["reactionrole"], invoke_without_command=True)
    @checks.has_permissions(manage_roles=True)
    async def reactionroles(self, ctx):
        """Commands to create and manage reactionrole messages."""
        await ctx.send_help(ctx.command)

    async def prompt(self, ctx, *, converter=None, delete_after=None):
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        while True:  # scary
            try:
                message = await self.bot.wait_for("message", check=check, timeout=180)
            except asyncio.TimeoutError:
                await ctx.send("You timed out. Aborting.")
                return PromptResponse.TIMED_OUT

            if message.content == f"{ctx.prefix}abort":
                await ctx.send("Aborted.")
                return PromptResponse.CANCELLED

            if not converter:
                result = message.content
                break

            try:
                result = await converter().convert(ctx, message.content)
            except commands.BadArgument as e:
                await ctx.send(f"{e}\nPlease try again.", delete_after=delete_after)
                continue

            else:
                break

        return result

    @reactionroles.command(name="create", aliases=["new"])
    @checks.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def reactionroles_create(self, ctx):
        """Start an interactive reactionrole creation session."""
        await ctx.send(
            "Beginning interactive reactionrole creation session.\n"
            f"Use `{ctx.prefix}abort` to abort."
        )

        await ctx.send("Enter the channel to send the reactionrole message to.")
        channel = await self.prompt(ctx, converter=commands.TextChannelConverter)

        if isinstance(channel, PromptResponse):
            return

        permissions = channel.permissions_for(ctx.me)
        if not permissions.send_messages:
            return await ctx.send("I cannot send messages to that channel. Aborting.")

        if not permissions.embed_links:
            return await ctx.send("I cannot send embeds to that channel. Aborting.")

        if not permissions.add_reactions:
            return await ctx.send(
                "I cannot add reactions to messages in that channel. Aborting."
            )

        await ctx.send(
            "Next, please send messages with the emoji and selfrole in this format: `emoji selfrole`\n"
            "Note that emojis must be default Discord emojis or an emoji in this server. "
            "Other emojis will not be accepted.\n\n"
            "Examples:\n - \N{VIDEO GAME} Video Gamer\n - \N{LOWER LEFT PAINTBRUSH} Artist\n\n"
            f"Use `{ctx.prefix}done` when you are done (or `{ctx.prefix}abort` to abort)."
        )

        emojis_and_roles = []

        while True:
            result = await self.prompt(
                ctx, converter=ReactionroleEmojiConverter, delete_after=5.0
            )

            if not result:
                if not emojis_and_roles:
                    await ctx.send("You didn't provide any emojis or roles. Aborting.")
                    return

                await ctx.send("Alright! Creating reactionrole menu...")
                break

            if isinstance(result, PromptResponse):
                return

            emojis_and_roles.append(result)
            await ctx.send(ctx.tick(True, "Added role"), delete_after=5.0)

        alt_ctx = await copy_context_with(ctx, channel=channel)
        menu = await self.create_reactionrole_menu(alt_ctx, emojis_and_roles)

        self.active_menus.append(menu)

        query = """INSERT INTO reactionroles (guild_id, channel_id, message_id, emojis_and_roles)
                   VALUES ($1, $2, $3, $4::jsonb);
                """

        emojis_and_roles = [(e, r.id) for e, r in emojis_and_roles]
        await ctx.db.execute(
            query, ctx.guild.id, channel.id, menu.message.id, emojis_and_roles
        )

        await ctx.send(
            ctx.tick(
                True,
                "Successfully created your reactionrole menu. To delete it, just delete the message.",
            )
        )


def setup(bot):
    bot.add_cog(Selfroles(bot))
