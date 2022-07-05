import asyncio
import base64
import binascii
import collections
import datetime
import functools
import io
import json
import pathlib
import re
import os.path
from typing import Any, Dict, List

import discord
from discord import app_commands
from discord.ext import commands, menus
from jishaku.codeblocks import codeblock_converter
from PIL import Image

from clam.utils import checks, colors, emojis, humantime
from clam.utils.context import Context
from clam.utils.formats import human_join, plural
from clam.utils.menus import MenuPages


def snowstamp(snowflake):
    timestamp = (int(snowflake) >> 22) + 1420070400000
    timestamp /= 1000

    return datetime.datetime.utcfromtimestamp(timestamp).strftime("%b %d, %Y at %#I:%M %p")


def can_snipe():
    async def predicate(ctx):
        return ctx.guild and str(ctx.guild.id) not in ctx.cog.snipe_ignored

    return commands.check(predicate)


class GlobalUser(commands.Converter):
    async def convert(self, ctx, arg):
        try:
            if not ctx.guild:
                raise commands.BadArgument()  # blank to skip
            user = await commands.MemberConverter().convert(ctx, arg)

        except commands.BadArgument:
            try:
                user = await commands.UserConverter().convert(ctx, arg)

            except commands.BadArgument:
                try:
                    arg = int(arg)

                except ValueError:
                    arg = discord.utils.escape_mentions(arg)
                    raise commands.BadArgument(
                        f"Could not find a member or user `{arg}` with that name. Try with their ID instead."
                    )
                try:
                    user = await ctx.bot.fetch_user(arg)

                except discord.HTTPException:
                    raise commands.BadArgument(
                        f"Could not find a member or user with the ID of `{arg}`."
                    )

        return user


class SearchPages(menus.ListPageSource):
    def __init__(self, data):
        pages_limit = 10
        current = (
            f"Found **{len(data)}** {'matches' if len(data) > 1 else 'match'}! ```ini\n"
        )
        for i, entry in enumerate(data):
            if entry.nick:
                nick = f"{entry.nick} - "
            else:
                nick = ""
            if (
                len(
                    current
                    + f"\n[{i+1}] {nick}{entry.name}#{entry.discriminator} ({entry.id})"
                )
                <= 2000
            ):
                current += (
                    f"\n[{i+1}] {nick}{entry.name}#{entry.discriminator} ({entry.id})"
                )
            else:
                current = f"Found **{len(data)}** {'matches' if len(data) > 1 else 'match'}! ```ini\n"
                if i + 1 < pages_limit:
                    pages_limit = i + 1
        super().__init__(data, per_page=pages_limit)

    async def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page
        msg = f"Found **{len(self.entries)}** {'matches' if len(self.entries) > 1 else 'match'}! ```ini\n"
        for i, member in enumerate(entries, start=offset):
            if member.nick:
                nick = f"{member.nick} - "
            else:
                nick = ""
            msg += f"\n[{i+1}] {nick}{member.name}#{member.discriminator} ({member.id})"
        # msg += '\n'.join(f'{i+1}. {v}' for i, v in enumerate(entries, start=offset))
        msg += "\n```"
        return msg


DeletedMessage = collections.namedtuple(
    "DeletedMessage", ("message", "id", "channel", "deleted_at")
)
EditedMessage = collections.namedtuple(
    "EditedMessage", ("before", "after", "id", "channel", "edited_at")
)


class Tools(commands.Cog):
    """Useful Discord tools."""

    def __init__(self, bot):
        self.bot = bot
        self.emoji = "\N{HAMMER AND WRENCH}"
        self.log = self.bot.log

        if not hasattr(bot, "sniped_messages"):
            self.bot.sniped_messages = []

        if not os.path.exists("snipe_ignored.json"):
            with open("snipe_ignored.json", "w") as f:
                json.dump([], f)

        with open("snipe_ignored.json", "r") as f:
            self.snipe_ignored = json.load(f)

    @commands.command(aliases=["countreactions"])
    async def reactioncount(self, ctx, *, message: discord.Message):
        """Counts reactions for a message."""

        if not message.reactions:
            return await ctx.send("This message has no reactions.")

        total = sum(r.count for r in message.reactions)

        human_friendly = []
        for reaction in message.reactions:
            percentage = int(reaction.count / total * 100)
            human_friendly.append(
                f"{reaction.emoji} `{percentage}%` ({plural(reaction.count):reaction})"
            )

        formatted = "\n".join(human_friendly)

        await ctx.send(f"**Reactions ({total} total):**\n{formatted}")

    @commands.command(aliases=["inrole"])
    async def hasrole(self, ctx, *, role: discord.Role):
        """Shows members with a specific role."""

        role_members = []

        for member in sorted(role.members, key=lambda m: m.name.lower()):
            role_members.append(f"{member} - ID: {member.id}")

        pages = ctx.pages(role_members, per_page=10, title=f"Members with role '{role}'")
        await pages.start()

    @commands.command(aliases=["newmembers"])
    @commands.guild_only()
    async def newjoins(self, ctx, *, count=5):
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

        em = discord.Embed(title="New Joins", colour=colors.PRIMARY)

        for member in members:
            body = f"Joined {humantime.timedelta(member.joined_at)}\nCreated {humantime.timedelta(member.created_at)}"
            em.add_field(name=f"{member} (ID: {member.id})", value=body, inline=False)

        await ctx.send(embed=em)

    @commands.command(aliases=["oldmembers"])
    @commands.guild_only()
    async def oldjoins(self, ctx, *, count=5):
        """Tells you the oldest members of the server.

        The count parameter can only be up to 25.
        """

        count = max(min(count, 25), 5)

        if not ctx.guild.chunked:
            await self.bot.request_offline_members(ctx.guild)

        members = sorted(ctx.guild.members, key=lambda m: m.joined_at)[:count]

        em = discord.Embed(title="Oldest Joins", colour=colors.PRIMARY)

        for member in members:
            body = f"Joined {humantime.timedelta(member.joined_at)}\nCreated {humantime.timedelta(member.created_at)}"
            em.add_field(name=f"{member} (ID: {member.id})", value=body, inline=False)

        await ctx.send(embed=em)

    @commands.command(aliases=["oldusers"])
    @commands.guild_only()
    async def boomers(self, ctx, *, count=5):
        """Tells you the oldest users in the server.

        The count parameter can only be up to 25.
        """

        count = max(min(count, 25), 5)

        if not ctx.guild.chunked:
            await self.bot.request_offline_members(ctx.guild)

        members = sorted(ctx.guild.members, key=lambda m: m.created_at)[:count]

        em = discord.Embed(title="Boomers (oldest accounts)", colour=colors.PRIMARY)

        for member in members:
            body = f"Created {humantime.timedelta(member.created_at)}\nJoined {humantime.timedelta(member.joined_at)}"
            em.add_field(name=f"{member} (ID: {member.id})", value=body, inline=False)

        await ctx.send(embed=em)

    @commands.command(aliases=["newusers"])
    @commands.guild_only()
    async def babies(self, ctx, *, count=5):
        """Tells you the newest users in the server.

        The count parameter can only be up to 25.
        """

        count = max(min(count, 25), 5)

        if not ctx.guild.chunked:
            await self.bot.request_offline_members(ctx.guild)

        members = sorted(ctx.guild.members, key=lambda m: m.created_at, reverse=True)[
            :count
        ]

        em = discord.Embed(title="Babies (newest accounts)", colour=colors.PRIMARY)

        for member in members:
            body = (
                f"Created {humantime.timedelta(member.created_at)}\n"
                f"Joined {humantime.timedelta(member.joined_at)}"
            )
            em.add_field(name=f"{member} (ID: {member.id})", value=body, inline=False)

        await ctx.send(embed=em)

    async def prompt(self, ctx, msg, *, timeout=180.0, check=None):
        def default_check(ms):
            return ms.author == ctx.author and not ms.guild

        check = check or default_check

        await ctx.author.send(msg)

        try:
            message = await self.bot.wait_for("message", timeout=timeout, check=check)

        except asyncio.TimeoutError:
            await ctx.author.send("You timed out. Aborting.")
            raise commands.BadArgument("Poll creation cancelled.")

        return message.content

    POLL_EMOJIS = [
        "\N{REGIONAL INDICATOR SYMBOL LETTER A}",
        "\N{REGIONAL INDICATOR SYMBOL LETTER B}",
        "\N{REGIONAL INDICATOR SYMBOL LETTER C}",
        "\N{REGIONAL INDICATOR SYMBOL LETTER D}",
        "\N{REGIONAL INDICATOR SYMBOL LETTER E}",
        "\N{REGIONAL INDICATOR SYMBOL LETTER F}",
        "\N{REGIONAL INDICATOR SYMBOL LETTER G}",
        "\N{REGIONAL INDICATOR SYMBOL LETTER H}",
        "\N{REGIONAL INDICATOR SYMBOL LETTER I}",
        "\N{REGIONAL INDICATOR SYMBOL LETTER J}",
    ]

    @commands.command()
    @commands.guild_only()
    async def poll(self, ctx, name=None, *args):
        """Creates a poll through DMs.

        For a quicker version (with less control), use `{prefix}quickpoll`.
        """

        timers = self.bot.get_cog("Timers")
        if not timers:
            return await ctx.send(
                "Sorry, this functionality is unavailable right now. Try again later?"
            )

        try:
            await ctx.author.send("Welcome to the interactive poll maker")
            self.bot.loop.create_task(ctx.message.add_reaction("\N{INCOMING ENVELOPE}"))

        except discord.Forbidden:
            raise commands.BadArgument(
                "You must allow me to send you DMs. Poll creation cancelled. "
                f"Use `{ctx.prefix}quickpoll` as an alternative."
            )

        title = await self.prompt(ctx, "What is the title of the poll?")

        options = []

        await ctx.author.send(
            "Type options for your poll in separate messages.\n"
            "To accociate an emoji with an option, use this format: `emoji option`.\n"
            "Emojis must be default emojis, not custom."
            f"When you are done, type `{ctx.prefix}done` to create the poll."
        )

        Option = collections.namedtuple("Option", "emoji text")

        def check(ms):
            return ms.author == ctx.author and not ms.guild

        while len(options) <= 10:
            try:
                message = await self.bot.wait_for("message", timeout=180.0, check=check)

            except asyncio.TimeoutError:
                return await ctx.send(f"{ctx.tick(False)} You timed out. Aborting.")

            if message.content.lower() == f"{ctx.prefix}done":
                break

            content = message.content

            if len(content) < 3:
                options.append(Option(None, content))
                await message.add_reaction(ctx.tick(True))
                continue

            args = content.split(" ")

            if len(args) < 2:
                options.append(Option(None, content))
                await message.add_reaction(ctx.tick(True))
                continue

            emoji = args[0]
            text = " ".join(args[1:])

            with open("assets/emoji_map.json", "r") as f:
                emoji_map = json.load(f)

            if emoji in emoji_map.values():
                if emoji in [o.emoji for o in options]:
                    await ctx.author.send(
                        ctx.tick(False, "You have already used that emoji."),
                        delete_after=5.0,
                    )
                    await message.add_reaction(ctx.tick(False))
                    continue

                options.append(Option(emoji, text))
                await message.add_reaction(ctx.tick(True))
                continue

            emoji_regex = re.compile(
                r"<(?P<animated>a?):(?P<name>[a-zA-Z0-9_]{2,32}):(?P<id>[0-9]{18,22})>"
            )
            if emoji_regex.match(emoji):
                await message.add_reaction("\N{WARNING SIGN}")
                await ctx.author.send(
                    "\N{WARNING SIGN} You cannot associate a custom emoji with an option. "
                    "Only default emojis are accepted.",
                    delete_after=5.0,
                )
                continue

            if emoji in [o.emoji for o in options]:
                await ctx.author.send(
                    ctx.tick(False, "You have already used that emoji."),
                    delete_after=5.0,
                )
                await message.add_reaction(ctx.tick(False))
                continue

            options.append(Option(None, content))

            await message.add_reaction(ctx.tick(True))

        await ctx.author.send("Sending your poll...")

        description = []

        for i, option in enumerate(options):
            description.append(f"{option.emoji or self.POLL_EMOJIS[i]} | {option.text}")

        human_friendly = "\n".join(description)

        em = discord.Embed(
            title=title,
            description="Vote for an option by clicking the associated reaction.\n"
            f"This poll will close in 24 hours."
            f"\n\n{human_friendly}",
            color=colors.PRIMARY,
        )

        if ctx.author.nick:
            name = f"{ctx.author.nick} ({str(ctx.author)})"
        else:
            name = str(ctx.author)

        em.set_author(name=name, icon_url=ctx.author.display_avatar.url)

        poll_message = await ctx.send("New Poll", embed=em)

        for i, option in enumerate(options):
            self.bot.loop.create_task(
                poll_message.add_reaction(option.emoji or self.POLL_EMOJIS[i])
            )

        await ctx.author.send(ctx.tick(True, "Poll sent!"))

        option_map = {o.emoji: o.text for o in options}

        when = datetime.datetime.utcnow() + datetime.timedelta(days=1)
        await timers.create_timer(
            when,
            "poll",
            poll_message.id,
            ctx.channel.id,
            ctx.guild.id,
            ctx.author.id,
            option_map,
        )

    @commands.command()
    @commands.guild_only()
    async def quickpoll(self, ctx, title=None, *options):
        """A quicker version of `{prefix}poll`.

        If the the title or an option contains spaces, make sure to wrap it in quotes.
        """

        timers = self.bot.get_cog("Timers")
        if not timers:
            return await ctx.send(
                "Sorry, this functionality is unavailable right now. Try again later?"
            )

        if len(options) < 2:
            raise commands.BadArgument("You must provide at least 2 options.")

        description = []

        for i, option in enumerate(options):
            description.append(f"{self.POLL_EMOJIS[i]} | {option}")

        human_friendly = "\n".join(description)

        em = discord.Embed(
            title=title,
            description="Vote for an option by clicking the associated reaction.\n"
            f"This poll will close in 24 hours."
            f"\n\n{human_friendly}",
            color=colors.PRIMARY,
        )

        if ctx.author.nick:
            name = f"{ctx.author.nick} ({str(ctx.author)})"
        else:
            name = str(ctx.author)

        em.set_author(name=name, icon_url=ctx.author.display_avatar.url)

        poll_message = await ctx.send("New Poll", embed=em)

        for i, option in enumerate(options):
            self.bot.loop.create_task(poll_message.add_reaction(self.POLL_EMOJIS[i]))

        option_map = {self.POLL_EMOJIS[i]: o for i, o in enumerate(options)}

        when = datetime.datetime.utcnow() + datetime.timedelta(days=1)
        await timers.create_timer(
            when,
            "poll",
            poll_message.id,
            ctx.channel.id,
            ctx.guild.id,
            ctx.author.id,
            option_map,
        )

    @commands.Cog.listener()
    async def on_poll_timer_complete(self, timer):
        message_id, channel_id, guild_id, author_id, option_map = timer.args

        guild = self.bot.get_guild(guild_id)

        if not guild:
            return

        channel = guild.get_channel(channel_id)

        if not channel:
            return

        try:
            message = await channel.fetch_message(message_id)

        except discord.HTTPException:
            return

        if not message.reactions:
            results = "Reactions have been cleared. No results.\n\n"
            results += "\n".join(f"{e} | {o} `0%` (0 votes)" for e, o in option_map.items())

        else:
            total = sum(r.count for r in message.reactions)
            largest = max(r.count for r in message.reactions)

            human_friendly = []
            for emoji, option in option_map.items():
                reaction = discord.utils.find(lambda r: str(r.emoji) == emoji, message.reactions)

                if not reaction:
                    human_friendly.append(f"{emoji} | {option} `0%` (0 votes)")
                    continue

                bolded = "**" if reaction.count == largest else ""

                percentage = int(reaction.count / total * 100)
                human_friendly.append(
                    f"{bolded}{emoji} | {option} `{percentage}%` ({plural(reaction.count):vote}){bolded}"
                )

            results = "\n".join(human_friendly)

        em = message.embeds[0]

        em.color = discord.Color.orange()

        em.description = f"This poll has been closed.\nResults:\n\n{results}"

        await message.edit(embed=em)

        author = self.bot.get_user(author_id)

        if not author:
            return

        em = discord.Embed(
            title="Your Poll Results Are In!", color=discord.Color.green()
        )
        em.description = (
            f"Your poll that you created 24 hours ago in {message.guild} has been closed.\n"
            f"You can [find the results here!]({message.jump_url})"
        )

        await author.send(embed=em)

    def is_url_spoiler(self, text, url):
        spoilers = re.findall(r"\|\|(.+?)\|\|", text)
        for spoiler in spoilers:
            if url in spoiler:
                return True
        return False

    async def send_deleted_message(self, ctx, message, deleted_at):
        description = message.content

        to_add = []
        if message.embeds:
            if any(e.type == "rich" for e in message.embeds):
                to_add.append("embed")

        if message.attachments:
            to_add.append("deleted attachment")

        human_friendly = " and ".join(to_add)

        if human_friendly:
            if description:
                description += f"\n\n*Message also contained {human_friendly}*"

            else:
                description = f"*[{human_friendly}]*"

        em = discord.Embed(
            description=description,
            color=colors.PRIMARY,
            timestamp=message.created_at,
        )

        if message.embeds:
            data = message.embeds[0]
            if data.type == "image" and not self.is_url_spoiler(
                message.content, data.url
            ):
                em.set_image(url=data.url)

        em.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        formatted = humantime.timedelta(deleted_at, brief=True, accuracy=1, discord_fmt=False)
        em.set_footer(text=f"Deleted {formatted} | Message sent")
        content = f"\N{WASTEBASKET} Deleted Message | ID: {message.id}"

        await ctx.send(content, embed=em)

    def format_edit(self, message):
        content = message.content

        to_add = []
        if message.embeds:
            if any(e.type == "rich" for e in message.embeds):
                to_add.append("embed")

        if message.attachments:
            to_add.append("attachment")

        human_friendly = " and ".join(to_add)

        if human_friendly:
            if content:
                content += f"\n\n*Message also contained {human_friendly}*"

            else:
                content = f"*[{human_friendly}]*"

        return content

    async def send_sniped_message(self, ctx, snipe):
        # 2nd condition is a fix for extension reloads (something something types)
        if isinstance(snipe, DeletedMessage) or hasattr(snipe, "deleted_at"):
            await self.send_deleted_message(ctx, snipe.message, snipe.deleted_at)
            return

        before = snipe.before
        after = snipe.after
        edited_at = snipe.edited_at

        em = discord.Embed(
            color=colors.PRIMARY,
            timestamp=before.created_at,
        )

        em.add_field(
            name="Before",
            value=self.format_edit(before) or "*Nothing to display*",
            inline=False,
        )
        em.add_field(
            name="After", value=self.format_edit(after) or "*Nothing to display*"
        )

        if after.embeds:
            data = after.embeds[0]
            if data.type == "image" and not self.is_url_spoiler(
                after.content, data.url
            ):
                em.set_image(url=data.url)

        em.set_author(name=str(after.author), icon_url=after.author.display_avatar.url)
        formatted = humantime.timedelta(edited_at, brief=True, accuracy=1, discord_fmt=False)
        em.set_footer(text=f"Edited {formatted} | Message sent")
        content = f"\N{MEMO} Edited Message | ID: {after.id}"

        await ctx.send(content, embed=em)

    @commands.group(invoke_without_command=True)
    @can_snipe()
    async def snipe(self, ctx, message_id: int = None):
        """Shows the previous deleted or edited message in the channel."""

        if str(ctx.author.id) in self.snipe_ignored:
            return await ctx.send(
                f"You are opted out of sniped messages. To opt back in, use `{ctx.prefix}snipe optin`"
            )

        sniped = [s for s in self.bot.sniped_messages if s.channel == ctx.channel]

        if not sniped:
            return await ctx.send("I haven't sniped any messages in this channel.")

        if message_id:
            result = None
            for snipe in sniped:
                if snipe.id == message_id:
                    result = snipe
                    break

            if not result:
                raise commands.BadArgument(
                    "I don't have a sniped message with that ID."
                )

        else:
            snipe = sniped[0]

        await self.send_sniped_message(ctx, snipe)

    @snipe.command(name="disable", aliases=["goaway"])
    @checks.has_permissions(manage_guild=True)
    async def sniped_disable(self, ctx):
        """Disables sniped messages in this server."""

        if str(ctx.guild.id) in self.snipe_ignored:
            return await ctx.send(
                f"Snipe is already disabled. To enable, use `{ctx.prefix}snipe enable`"
            )

        self.snipe_ignored.append(str(ctx.guild.id))
        with open("snipe_ignored.json", "w") as f:
            json.dump(self.snipe_ignored, f)

        await ctx.send(ctx.tick(True, "Disabled sniped messages for this server"))

    @snipe.command(name="enable")
    @checks.has_permissions(manage_guild=True)
    async def sniped_enable(self, ctx):
        """Enables sniped messages in this server."""

        if str(ctx.guild.id) not in self.snipe_ignored:
            return await ctx.send(
                f"Snipe is enabled. To disable, use `{ctx.prefix}snipe disable`"
            )

        self.snipe_ignored.pop(self.snipe_ignored.index(str(ctx.guild.id)))
        with open("snipe_ignored.json", "w") as f:
            json.dump(self.snipe_ignored, f)

        await ctx.send(ctx.tick(True, "Enabled sniped messages for this server"))

    @snipe.command(name="optout", aliases=["ignore", "nothanks"],)
    async def snipe_optout(self, ctx):
        """Opts you out of sniped messages tracking globally."""

        if str(ctx.author.id) in self.snipe_ignored:
            return await ctx.send(
                f"You are already opted out of sniped messages. To opt back in, use `{ctx.prefix}snipe optin`"
            )

        self.snipe_ignored.append(str(ctx.author.id))
        with open("snipe_ignored.json", "w") as f:
            json.dump(self.snipe_ignored, f)

        await ctx.send(ctx.tick(True, "Opted out of sniped messages"))

    @snipe.command(name="optin", aliases=["unignore", "yesplease"])
    async def snipe_optin(self, ctx):
        """Opts you in back to sniped messages tracking."""

        if str(ctx.author.id) not in self.snipe_ignored:
            return await ctx.send(
                f"You have not opted out of sniped messages. To optout, use `{ctx.prefix}snipe optout`"
            )

        self.snipe_ignored.pop(self.snipe_ignored.index(str(ctx.author.id)))
        with open("snipe_ignored.json", "w") as f:
            json.dump(self.snipe_ignored, f)

        await ctx.send(ctx.tick(True, "Opted in to sniped messages"))

    @snipe.command(name="ignored")
    @commands.is_owner()
    async def snipe_ignored(self, ctx):
        """Shows all users who have opted out of sniped messages."""

        if not self.snipe_ignored:
            return await ctx.send("No ignored entities")

        entities = []

        for entity_id in self.snipe_ignored:
            entity_id = int(entity_id)
            user = self.bot.get_user(entity_id)

            if not user:
                guild = self.bot.get_guild(entity_id)

                if guild:
                    entities.append(f"Guild: {guild} (ID: {guild.id})")
                entities.append(f"User or guild with an ID of {entity_id}")

            else:
                entities.append(f"User: {user} (ID: {user.id})")

        pages = ctx.pages(
            entities,
            per_page=10,
            title="Snipe Ignored Entities",
            description="Entities that have opted out of sniped messages",
        )
        await pages.start()

    @commands.group(invoke_without_command=True)
    @can_snipe()
    async def sniped(self, ctx):
        """Shows the sniped messaegs in the channel."""

        if str(ctx.author.id) in self.snipe_ignored:
            return await ctx.send(
                f"You are opted out of sniped messages. To opt back in, use `{ctx.prefix}snipe optin`"
            )

        sniped = [s for s in self.bot.sniped_messages if s.channel == ctx.channel]

        if not sniped:
            return await ctx.send("I haven't sniped any messages in this channel.")

        entries = []

        for snipe in sniped:
            if isinstance(snipe, DeletedMessage) or hasattr(snipe, "message"):
                message = snipe.message
                human_friendly = humantime.timedelta(
                    snipe.deleted_at, brief=True, accuracy=1
                )
                entries.append(
                    f"\N{WASTEBASKET} {message.author} - {human_friendly} `(ID: {message.id})`"
                )

            else:
                message = snipe.before
                human_friendly = humantime.timedelta(
                    snipe.edited_at, brief=True, accuracy=1
                )
                entries.append(
                    f"\N{MEMO} {message.author} - {human_friendly} `(ID: {message.id})`"
                )

        # entries = [
        #     f"{m.author} - {humantime.timedelta(d, brief=True, accuracy=1)} `(ID: {m.id})`"
        #     for m, d in sniped
        # ]

        em = discord.Embed(title="Sniped Messages", color=colors.PRIMARY)

        pages = ctx.embed_pages(entries, em)
        await pages.start()

    @sniped.command(name="clear", aliases=["delete"])
    @commands.is_owner()
    async def sniped_clear(self, ctx, *args):
        """Clears sniped messages in the channel.

        Use the `--all` flag to clear sniped messages for all channels.
        """
        if "--all" in args:
            cleared = len(self.bot.sniped_messages)
            self.bot.sniped_messages.clear()

        else:
            before_amount = len(self.bot.sniped_messages)
            self.bot.sniped_messages = [
                s for s in self.bot.sniped_messages if s.channel != ctx.channel
            ]
            cleared = before_amount - len(self.bot.sniped_messages)

        await ctx.send(ctx.tick(True, f"Cleared **`{cleared}`** sniped messages."))

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if not message.guild:
            return

        if (
            str(message.author.id) in self.snipe_ignored
            or str(message.guild.id) in self.snipe_ignored
        ):
            return

        now = datetime.datetime.utcnow()
        self.bot.sniped_messages.insert(
            0, DeletedMessage(message, message.id, message.channel, now)
        )

        if len(self.bot.sniped_messages) > 1000:
            self.bot.sniped_messages.pop(len(self.bot.sniped_messages) - 1)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if not after.guild:
            return

        if (
            str(after.author.id) in self.snipe_ignored
            or str(after.guild.id) in self.snipe_ignored
        ):
            return

        if before.content == after.content:
            return

        now = datetime.datetime.utcnow()
        self.bot.sniped_messages.insert(
            0, EditedMessage(before, after, after.id, after.channel, now)
        )

        if len(self.bot.sniped_messages) > 1000:
            self.bot.sniped_messages.pop(len(self.bot.sniped_messages) - 1)

    async def get_average_color(self, icon):
        bytes = io.BytesIO(await icon.read())
        partial = functools.partial(Image.open, bytes)
        image = await self.bot.loop.run_in_executor(None, partial)
        partial = functools.partial(image.resize, (1, 1))
        resized = await self.bot.loop.run_in_executor(None, partial)
        partial = functools.partial(resized.getpixel, (0, 0))
        color = await self.bot.loop.run_in_executor(None, partial)
        try:
            hex_string = "0x{:02x}{:02x}{:02x}".format(*color)
            return discord.Color(int(hex_string, 16))
        except TypeError:
            return None

    @commands.hybrid_command(aliases=["pfp"])
    @app_commands.describe(user="Who's avatar to show")
    async def avatar(self, ctx, *, user: GlobalUser = None):
        """Shows a user's avatar."""

        if not user:
            user = ctx.author

        avatar = user.display_avatar
        color = await self.get_average_color(avatar) if avatar else None
        color = color or user.color or colors.PRIMARY

        em = discord.Embed(color=color)

        if isinstance(user, discord.Member) and user.nick:
            name = f"{user.nick} ({str(user)})"
        else:
            name = str(user)

        format_names = ["png", "jpeg", "webp"]
        if user.display_avatar.is_animated():
            format_names.append("gif")

        formats = [f"[{f.upper()}]({user.display_avatar.with_format(f).url})" for f in format_names]

        em.description = f"View as {human_join(formats)}"

        em.set_author(name=name, icon_url=user.display_avatar.with_format("png").url)
        em.set_image(url=user.display_avatar.url)

        await ctx.send(embed=em)

    @commands.hybrid_command(aliases=["memberinfo", "ui", "whois"])
    @app_commands.describe(user="Who to show info about")
    async def userinfo(self, ctx, *, user: GlobalUser = None):
        """Shows info about a user."""

        if ctx.interaction is None:
            await ctx.typing()

        await ctx.defer()

        user = user or ctx.author

        is_member = isinstance(user, discord.Member)

        badge_mapping = {
            discord.UserFlags.staff: emojis.DISCORD_DEVELOPER,
            discord.UserFlags.partner: emojis.PARTNER,
            discord.UserFlags.hypesquad: emojis.HYPESQUAD_EVENTS,
            discord.UserFlags.bug_hunter: emojis.BUG_HUNTER,
            discord.UserFlags.bug_hunter_level_2: emojis.BUG_HUNTER_2,
            discord.UserFlags.hypesquad_bravery: emojis.HYPESQUAD_BRAVERY,
            discord.UserFlags.hypesquad_brilliance: emojis.HYPESQUAD_BRILLIANCE,
            discord.UserFlags.hypesquad_balance: emojis.HYPESQUAD_BALANCE,
            discord.UserFlags.early_supporter: emojis.EARLY_SUPPORTER,
            discord.UserFlags.verified_bot_developer: emojis.EARLY_VERIFIED_DEVELOPER,
        }

        badges = []
        for f in user.public_flags.all():
            badge = badge_mapping.get(f)

            if badge:
                badges.append(badge)

        desc = " ".join(badges)
        if user.id == self.bot.owner_id:
            created_or_owns = "created" if user.id == 224513210471022592 else "owns"
            desc += f"\n:gear: This user {created_or_owns} {self.bot.user.name}."
        if user == self.bot.user:
            desc += "\n:wave: Hey, that's me!"
        if user.bot is True:
            verified = "verified " if user.public_flags.verified_bot else ""
            desc += f"\n:robot: This user is a {verified}bot."
        if is_member and user.id == ctx.guild.owner_id:
            desc += "\n<:owner:649355683598303260> This user is the server owner."
        if is_member and user.premium_since:
            formatted = user.premium_since.strftime("%b %d, %Y at %#I:%M %p")
            desc += (
                "\n<:boost:649644112034922516> "
                "This user has been boosting this server since "
                f"{formatted}."
            )

        author = str(user)
        if is_member and user.nick:
            author += f" ({user.nick})"
        author += f" - {str(user.id)}"

        icon = user.display_avatar
        try:
            color = await self.get_average_color(icon) if icon else None
        except discord.HTTPException:
            color = None
        color = color or (user.color if is_member and user.color else colors.PRIMARY)

        em = discord.Embed(description=desc, color=color)

        em.set_thumbnail(url=user.display_avatar.url)
        em.set_author(name=author, icon_url=user.display_avatar.url)

        created_fmt = humantime.fulltime(user.created_at, accuracy=2)
        em.add_field(
            name=":clock1: Account Created",
            value=created_fmt,
            inline=True,
        )

        if is_member:
            joined_fmt = humantime.fulltime(user.joined_at, accuracy=2)
            em.add_field(
                name="<:join:649722959958638643> Joined Server",
                value=joined_fmt,
                inline=True,
            )

            members = ctx.guild.members
            members.sort(key=lambda x: x.joined_at)
            position = members.index(user)

            escape = discord.utils.escape_markdown
            joins = []

            if position > 0:
                joins.append(escape(f"{members[position - 1]} (#{position})"))

            user_pos = f"{user} (#{position + 1})"
            joins.append(f"**{escape(user_pos)}**")

            if position < len(members) - 1:
                joins.append(escape(f"{members[position + 1]} (#{position + 2})"))

            join_order = " \u2192 ".join(joins)
            em.add_field(name=":busts_in_silhouette: Join Position and Order", value=join_order, inline=False)

            if user.roles[1:]:
                roles = ""
                for role in user.roles[1:]:
                    if len(roles + f"{role.mention} ") > 1012:
                        roles += "...and more"
                        break
                    roles += f"{role.mention} "
                em.add_field(name="Roles", value=roles, inline=False)

        shared = [
            g for g in self.bot.guilds if discord.utils.get(g.members, id=user.id)
        ]

        if not shared:
            em.set_footer(text=f"No servers shared with {self.bot.user.name}")

        else:
            em.set_footer(text=f"{plural(len(shared)):server} shared with {self.bot.user.name}")

        await ctx.send(embed=em)

    @commands.hybrid_command(aliases=["guildinfo"])
    async def serverinfo(self, ctx):
        """Shows info about the server."""

        if ctx.interaction is None:
            await ctx.typing()

        await ctx.defer()

        guild = ctx.guild
        if guild.unavailable:
            return await ctx.send(
                "This guild is unavailable.\nWhat does this mean? I don't know either.\nMaybe Discord is having an outage..."
            )

        desc = ""
        if guild.description:
            desc += f"\n{guild.description}\n"
        if guild.large:
            desc += "\n:information_source: This guild is considered large (over 250 members)."

        icon = guild.icon
        color = await self.get_average_color(icon) if icon else None
        color = color or colors.PRIMARY

        em = discord.Embed(description=desc, color=color)

        em.set_thumbnail(url=guild.icon.url if guild.icon else None)
        em.set_image(url=guild.banner.url if guild.banner else None)
        em.set_author(name=f"{guild.name} ({guild.id})", icon_url=guild.icon.url if guild.icon else None)
        em.add_field(
            name="<:owner:649355683598303260> Owner",
            value=guild.owner.mention,
            inline=True,
        )
        em.add_field(
            name=":clock1: Server Created",
            value=f"{humantime.fulltime(guild.created_at)}",
            inline=True,
        )
        em.add_field(
            name="<:boost:649644112034922516> Nitro Boosts",
            value=f"Tier {guild.premium_tier} with {guild.premium_subscription_count} boosts",
            inline=True,
        )
        bot_amount = len([m for m in guild.members if m.bot])
        em.add_field(
            name=":family: Members",
            value=f"{len(guild.members)} ({bot_amount} bots)",
            inline=True,
        )
        em.add_field(
            name=":speech_balloon: Channels",
            value=f"<:text_channel:661798072384225307> {len(guild.text_channels)} • <:voice_channel:665577300552843294> {len(guild.voice_channels)}",
            inline=True,
        )

        # roles = ""
        # for role in member.roles[1:]:
        #     roles += f"{role.mention} "
        # em.add_field(
        #     name = "Roles",
        #     value = roles,
        #     inline = False
        # )
        await ctx.send(embed=em)

    @commands.command(hidden=True)
    async def snowstamp(self, ctx, snowflake=None):
        """Shows the timestamp from a Discord snowflake."""

        if not snowflake:
            return await ctx.send("Please specify a snowflake to convert.")

        await ctx.send(snowstamp(snowflake))

    def time_in_range(self, start, end, x):
        """Return true if x is in the range [start, end]"""
        if start <= end:
            return start <= x <= end
        else:
            return start <= x or x <= end

    @commands.command(hidden=True)
    async def parsetoken(self, ctx, token):
        """Parses a Discord token."""

        parsed = token.split(".")
        if len(parsed) != 3:
            return await ctx.send("This is not a Discord token.")

        try:
            user_id = base64.b64decode(parsed[0])
        except binascii.Error:
            return await ctx.send("Failed to decode user id.")

        user_id = int(user_id)
        try:
            decoded = base64.b64decode(parsed[1] + "==")
        except binascii.Error:
            return await ctx.send("Failed to decode timestamp.")

        epoch = int.from_bytes(decoded, "big")
        timestamp = epoch + 1293840000
        created = datetime.datetime.utcfromtimestamp(timestamp)
        if not self.time_in_range(2015, 2040, created.year):
            created = created - datetime.timedelta(days=14975)

        created = created.strftime("%b %d, %Y at %#I:%M %p")
        em = discord.Embed(color=0x36393F)
        try:
            user = await self.bot.fetch_user(user_id)
        except discord.NotFound:
            em.description = f"ID: `{user_id}`\nCreated: `{created}`\nUser not found."
            return await ctx.send(embed=em)

        em.description = f"ID: `{user_id}`\nUsername: `{user}`\nBot: `{user.bot}`\nCreated: `{created}`"
        em.set_thumbnail(url=user.display_avatar.url)
        await ctx.send(embed=em)

    @commands.group(aliases=["membersearch"], invoke_without_command=True)
    async def usersearch(self, ctx):
        """Searchs for things in a server."""

        await ctx.send_help(ctx.command)

    def compile_list(self, list):
        msg = (
            f"Found **{len(list)}** {'matches' if len(list) > 1 else 'match'}! ```ini\n"
        )
        for i, member in enumerate(list):
            if member.nick:
                nick = f"{member.nick} - "
            else:
                nick = ""
            msg += f"\n[{i+1}] {nick}{member.name}#{member.discriminator} ({member.id})"
        msg += "\n```"
        return msg

    @usersearch.command(name="username", aliases=["user", "name"])
    async def search_username(self, ctx, *, username):
        """Searchs usernames in this server."""

        matches = []
        for member in ctx.guild.members:
            if username.lower() in member.name.lower():
                matches.append(member)
        if matches:
            pages = MenuPages(SearchPages(matches), ctx=ctx)
            return await pages.start()
            # return await ctx.send(self.compile_list(matches))
        await ctx.send("No matches found.")

    @usersearch.command(name="nickname", aliases=["nick"])
    async def search_nickname(self, ctx, *, nickname):
        """Searches nicknames in this server."""

        matches = []
        for member in ctx.guild.members:
            if member.nick:
                if nickname.lower() in member.nick.lower():
                    matches.append(member)
        if matches:
            pages = MenuPages(SearchPages(matches), ctx=ctx)
            return await pages.start()
        await ctx.send("No matches found.")

    @usersearch.command(name="discriminator", aliases=["number", "discrim", "dis", "num"])
    async def search_discriminator(self, ctx, discriminator: int):
        """Searches discriminators in this server."""

        matches = []
        for member in ctx.guild.members:
            if discriminator == int(member.discriminator):
                matches.append(member)
        if matches:
            pages = MenuPages(SearchPages(matches), ctx=ctx)
            return await pages.start()
        await ctx.send("No matches found.")

    @commands.command()
    async def pyright(self, ctx: Context, *, code: codeblock_converter) -> None:
        filename = f"pyright-{ctx.message.id}.py"
        fp = pathlib.Path(f"./{filename}")

        with open(fp, "w") as f:
            f.write(code.content)

        # hopefully pyright is in the PATH somewhere
        # I know I'm hardcoding the version, but I don't care
        proc = await asyncio.create_subprocess_shell(
            f"pyright --outputjson {filename} --pythonversion 3.11",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        fp.unlink()

        # process the output
        output: Dict[str, Any] = json.loads(stdout.decode())

        result: List[str] = []

        version = output["version"]
        result.append(f"Pyright v{version}")
        result.append("")

        severity_mapping = {
            "error": "-",
            "warning": "-",
            "information": " ",
        }

        for diagnostic in output["generalDiagnostics"]:
            severity = diagnostic["severity"]

            if severity == "information":
                severity = "info"

            severity_tick = severity_mapping[diagnostic["severity"]]
            message = diagnostic["message"]
            line = diagnostic["range"]["start"]["line"]
            column = diagnostic["range"]["start"]["character"]

            result.append(f"{severity_tick} {line}:{column} - {severity}: {message}")

        result.append("")

        summary = output["summary"]
        errors = f"{plural(summary['errorCount']):error}"
        warnings = f"{plural(summary['warningCount']):warning}"
        info = f"{summary['informationCount']} info"

        result.append(human_join((errors, warnings, info), final="and"))
        result.append(f"Took {plural(summary['timeInSec']):second}")

        text = "\n".join(result)
        await ctx.send(f"```diff\n{text}\n```")


async def setup(bot):
    await bot.add_cog(Tools(bot))
