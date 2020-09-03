from discord.ext import commands, menus
import discord

from datetime import datetime as d
from datetime import timedelta
import re
import os
import base64
import binascii
import humanize
import io
import functools
from PIL import Image
import typing
import dateparser
import asyncio

from .utils import colors


def snowstamp(snowflake):
    timestamp = (int(snowflake) >> 22) + 1420070400000
    timestamp /= 1000

    return d.utcfromtimestamp(timestamp).strftime("%b %d, %Y at %#I:%M %p")


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
        print(pages_limit)
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


class Tools(commands.Cog):
    """Useful Discord tools."""

    def __init__(self, bot):
        self.bot = bot
        self.emoji = ":tools:"
        self.log = self.bot.log

        if not hasattr(bot, "sniped_messages"):
            self.bot.sniped_messages = []

        self.sniped_messages = self.bot.sniped_messages

    async def prompt(self, ctx, msg, *, timeout=180.0, check=None):
        def default_check(ms):
            return ms.author == ctx.author and ms.channel == ctx.channel

        check = check or default_check

        await ctx.send(msg)

        try:
            message = await self.bot.wait_for("message", timeout=timeout, check=check)

        except asyncio.TimeoutError:
            raise commands.BadArgument("You timed out. Aborting.")

        return message.content

    @commands.command(description="Create a poll and send it to any channel")
    async def poll(self, ctx):
        content = await self.prompt(ctx, "What channel should the poll be sent to?")

        channel = await commands.TextChannelConverter().convert(ctx, content)

        content = await self.prompt(
            ctx, "Mention everyone when creating the poll? (y/n)"
        )
        lowered = content.lower()

        if lowered.startswith("y"):
            mention = "@everyone "

        elif lowered.startswith("n"):
            mention = ""

        else:
            raise commands.BadArgument("You must respond with y or n. Aborting.")

        title = await self.prompt(ctx, "What is the title of the poll?")

        emojis = [
            "\N{REGIONAL INDICATOR SYMBOL LETTER A}",
            "\N{REGIONAL INDICATOR SYMBOL LETTER B}",
            "\N{REGIONAL INDICATOR SYMBOL LETTER C}",
            "\N{REGIONAL INDICATOR SYMBOL LETTER D}",
            "\N{REGIONAL INDICATOR SYMBOL LETTER E}",
            "\N{REGIONAL INDICATOR SYMBOL LETTER F}",
            "\N{REGIONAL INDICATOR SYMBOL LETTER G}",
            "\N{REGIONAL INDICATOR SYMBOL LETTER H}:",
            "\N{REGIONAL INDICATOR SYMBOL LETTER I}",
            "\N{REGIONAL INDICATOR SYMBOL LETTER J}",
        ]

        options = []

        await ctx.send(
            "Type options for your poll in separate messages.\n"
            f"When you are done, type `{ctx.prefix}create poll` to create the poll."
        )

        def check(ms):
            return ms.author == ctx.author and ms.channel == ctx.channel

        while len(options) <= 10:
            try:
                message = await self.bot.wait_for("message", timeout=180.0, check=check)

            except asyncio.TimeoutError:
                return await ctx.send(f"{ctx.tick(False)} You timed out. Aborting.")

            if message.content.lower() == f"{ctx.prefix}create poll":
                break

            options.append(message.content)

            await message.add_reaction(ctx.tick(True))

        await ctx.send("Creating your poll...")

        description = []

        for i, option in enumerate(options):
            description.append(f"{emojis[i]} | {option}")

        em = discord.Embed(
            title=title, description="\n".join(description), color=colors.PRIMARY
        )

        poll_message = await channel.send(f"{mention}**New Poll!**", embed=em)

        for i in range(len(options)):
            await poll_message.add_reaction(emojis[i])

    async def send_sniped_message(self, ctx, message):
        em = discord.Embed(
            description=message.content,
            color=colors.PRIMARY,
            timestamp=message.created_at,
        )

        em.set_author(name=str(message.author), icon_url=message.author.avatar_url)
        em.set_footer(text=f"ID: {message.id} | Message sent")

        await ctx.send(embed=em)

    @commands.group(
        invoke_without_command=True,
        description="Get the previous deleted message in this channel",
    )
    async def snipe(self, ctx):
        sniped = [m for m in self.sniped_messages if m.channel == ctx.channel]

        if not sniped:
            return await ctx.send("I haven't sniped any messages in this channel.")

        message = sniped[0]

        await self.send_sniped_message(ctx, message)

    @snipe.command(
        name="id", description="Get a sniped message by it's id (found with snipe all)"
    )
    async def snipe_id(self, ctx, id):
        sniped = [m for m in self.sniped_messages if m.channel == ctx.channel]

        if not sniped:
            return await ctx.send("I haven't sniped any messages in this channel.")

        message = discord.utils.get(sniped, id=id)

        if not message:
            return await ctx.send("I don't have a sniped message with that ID.")

        await self.send_sniped_message(ctx, message)

    @commands.command(
        description="Get all sniped messages in this channel",
    )
    async def sniped(self, ctx):
        sniped = [m for m in self.sniped_messages if m.channel == ctx.channel]

        if not sniped:
            return await ctx.send("I haven't sniped any messages in this channel.")

        entries = [f"{m.author} `(ID: {m.id})`" for m in sniped]

        em = discord.Embed(title="Sniped Messages", color=colors.PRIMARY)

        pages = ctx.embed_pages(entries, em)
        await pages.start(ctx)

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        self.sniped_messages.insert(0, message)

        if len(self.sniped_messages) > 1000:
            self.sniped_messages.pop(len(self.sniped_messages) - 1)

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

    @commands.command(
        description="Get the avatar of a member.", aliases=["profilepic"],
    )
    async def avatar(self, ctx, *, member: discord.Member = None):
        if not member:
            member = ctx.author

        icon = member.avatar_url
        color = await self.get_average_color(icon) if icon else None
        color = color or member.color or colors.PRIMARY

        em = discord.Embed(color=color)

        if member.nick:
            name = f"{member.nick} ({str(member)})"
        else:
            name = str(member)

        em.set_author(name=name, icon_url=member.avatar_url)
        em.set_image(url=member.avatar_url)

        await ctx.send(embed=em)

    @commands.command(
        name="userinfo",
        description="Get information about a user",
        aliases=["memberinfo", "ui", "whois"],
    )
    @commands.guild_only()
    async def userinfo_command(self, ctx, *, member: discord.Member = None):
        await ctx.trigger_typing()

        member = member or ctx.author

        if member == ctx.author:
            self.log.info(
                f"{str(ctx.author)} successfully used the "
                "userinfo command on themself"
            )
        else:
            self.log.info(
                f"{str(ctx.author)} successfully used the "
                f"userinfo command on '{member}'"
            )

        # def time_ago(user, dt):
        #     if dt is None:
        #         return ""
        #     return f"{snowstamp(user.id)}\n"
        #            f"({time.human_timedelta(dt, accuracy=3)})"

        desc = ""
        if member.id == self.bot.owner_id:
            created_or_owns = "created" if member.id == 224513210471022592 else "owns"
            desc += f"\n:gear: This user {created_or_owns} this bot."
        if member == self.bot.user:
            desc += "\n:wave:Hey, that's me!"
        if member.bot is True:
            desc += "\n:robot: This user is a bot."
        if member.id == ctx.guild.owner_id:
            desc += "\n<:owner:649355683598303260> This user owns this server."
        if member.premium_since:
            formatted = member.premium_since.strftime("%b %d, %Y at %#I:%M %p")
            desc += (
                "\n<:boost:649644112034922516> "
                "This user has been boosting this server since "
                f"{formatted}."
            )

        author = str(member)
        if member.nick:
            author += f" ({member.nick})"
        author += f" - {str(member.id)}"

        icon = member.avatar_url
        color = await self.get_average_color(icon) if icon else None
        color = color or member.color or colors.PRIMARY

        em = discord.Embed(description=desc, color=color)

        em.set_thumbnail(url=member.avatar_url)
        em.set_author(name=author, icon_url=member.avatar_url)
        humanized = humanize.naturaltime(member.created_at)
        em.add_field(
            name=":clock1: Account Created",
            value=f"{humanize.naturaldate(member.created_at).capitalize()} ({humanized})",
            inline=True,
        )
        humanized = humanize.naturaltime(member.joined_at)
        em.add_field(
            name="<:join:649722959958638643> Joined Server",
            value=f"{humanize.naturaldate(member.joined_at).capitalize()} ({humanized})",
            inline=True,
        )
        members = ctx.guild.members
        members.sort(key=lambda x: x.joined_at)
        position = members.index(member)
        em.add_field(name=":family: Join Position", value=position + 1)
        if member.roles[1:]:
            roles = ""
            for role in member.roles[1:]:
                roles += f"{role.mention} "
            em.add_field(name="Roles", value=roles, inline=False)
        await ctx.send(embed=em)

    @commands.command(
        name="serverinfo",
        description="Get information about the current server",
        aliases=["guildinfo"],
    )
    async def serverinfo_command(self, ctx):
        await ctx.trigger_typing()
        guild = ctx.guild
        if guild.unavailable == True:
            return await ctx.send(
                "This guild is unavailable.\nWhat does this mean? I don't know either.\nMaybe Discord is having an outage..."
            )

        desc = ""
        if guild.description:
            desc += f"\n{guild.description}\n"
        if guild.large == True:
            desc += "\n:information_source: This guild is considered large (over 250 members)."

        icon = guild.icon_url
        color = await self.get_average_color(icon) if icon else None
        color = color or colors.PRIMARY

        em = discord.Embed(description=desc, color=color)

        em.set_thumbnail(url=guild.icon_url)
        if guild.banner_url:
            em.set_image(url=guild.banner_url)
        em.set_author(name=f"{guild.name} ({guild.id})", icon_url=guild.icon_url)
        em.add_field(
            name="<:owner:649355683598303260> Owner",
            value=guild.owner.mention,
            inline=True,
        )
        humanized = humanize.naturaltime(guild.created_at)
        em.add_field(
            name=":clock1: Server Created",
            value=f"{humanize.naturaldate(guild.created_at).capitalize()} ({humanized})",
            inline=True,
        )
        em.add_field(
            name="<:boost:649644112034922516> Nitro Boosts",
            value=f"Tier {guild.premium_tier} with {guild.premium_subscription_count} boosts",
            inline=True,
        )
        em.add_field(
            name=":earth_americas: Region",
            value=str(guild.region).replace("-", " ").upper(),
            inline=True,
        )
        em.add_field(name=":family: Members", value=len(guild.members), inline=True)
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

    @commands.command(
        name="snowstamp",
        description="Get timestamp from a Discord snowflake",
        hidden=True,
    )
    async def snowstamp_command(self, ctx, snowflake=None):
        if snowflake == None:
            return await ctx.send("Please specify a snowflake to convert.")
        await ctx.send(snowstamp(snowflake))

    def time_in_range(self, start, end, x):
        """Return true if x is in the range [start, end]"""
        if start <= end:
            return start <= x <= end
        else:
            return start <= x or x <= end

    @commands.command(description="Parse a Discord token", hidden=True)
    async def parsetoken(self, ctx, token):
        parsed = token.split(".")
        if len(parsed) != 3:
            return await ctx.send("This is not a Discord token :/")

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
        created = d.utcfromtimestamp(timestamp)
        if not self.time_in_range(2015, 2040, created.year):
            created = created - timedelta(days=14975)

        created = created.strftime("%b %d, %Y at %#I:%M %p")
        em = discord.Embed(color=0x36393F)
        try:
            user = await self.bot.fetch_user(user_id)
        except discord.NotFound:
            em.description = f"ID: `{user_id}`\nCreated: `{created}`\nUser not found."
            return await ctx.send(embed=em)

        em.description = f"ID: `{user_id}`\nUsername: `{user}`\nBot: `{user.bot}`\nCreated: `{created}`"
        em.set_thumbnail(url=user.avatar_url)
        await ctx.send(embed=em)

    @commands.command(
        name="embed",
        description="Create a custom embed and send it to a specified channel.",
        aliases=["em"],
        hidden=True,
    )
    @commands.guild_only()
    @commands.is_owner()
    async def embed_command(self, ctx):
        def check(ms):
            # Look for the message sent in the same channel where the command was used
            # As well as by the user who used the command.
            return ms.channel == ctx.author.dm_channel and ms.author == ctx.author

        if (ctx.channel).__class__.__name__ == "DMChannel":
            await ctx.send("Please use this command in a server.")
            return

        await ctx.send("Check your DMs!", delete_after=5)
        await ctx.author.send(
            "**Create an embed:**\nWhat server would you like to send the embed to? Type `here` to send the embed where you called the command."
        )

        msg = await self.bot.wait_for("message", check=check)

        if msg == "here":
            em_guild = ctx.guild
        else:
            await ctx.author.send(
                "Custom servers not supported yet :(\nServer set to where you called the command."
            )
            em_guild = ctx.guild

        # Check to see if bot has permission to view perms

        await ctx.author.send(
            f"Server set to `{em_guild.name}`.\nWhat channel would you like to send to?"
        )

        msg = await self.bot.wait_for("message", check=check)

        # Check for permission here

        # while hasPermissionToSend == False:

    @commands.group(
        description="Search for things in a server.",
        aliases=["find"],
        invoke_without_command=True,
    )
    async def search(self, ctx):
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

    @search.command(
        name="username",
        description="Search server for a specified username",
        aliases=["user", "name"],
    )
    async def search_username(self, ctx, *, username):
        matches = []
        for member in ctx.guild.members:
            if username.lower() in member.name.lower():
                matches.append(member)
        if matches:
            pages = menus.MenuPages(
                source=SearchPages(matches), clear_reactions_after=True
            )
            return await pages.start(ctx)
            # return await ctx.send(self.compile_list(matches))
        await ctx.send("No matches found.")

    @search.command(
        name="nickname",
        description="Search server for a specified nickname",
        aliases=["nick"],
    )
    async def search_nickname(self, ctx, *, nickname):
        matches = []
        for member in ctx.guild.members:
            if member.nick:
                if nickname.lower() in member.nick.lower():
                    matches.append(member)
        if matches:
            pages = menus.MenuPages(
                source=SearchPages(matches), clear_reactions_after=True
            )
            return await pages.start(ctx)
        await ctx.send("No matches found.")

    @search.command(
        name="discriminator",
        description="Search server for a specified descrininator",
        aliases=["number", "discrim", "dis", "num"],
    )
    async def search_discriminator(self, ctx, discriminator: int):
        matches = []
        for member in ctx.guild.members:
            if discriminator == int(member.discriminator):
                matches.append(member)
        if matches:
            pages = menus.MenuPages(
                source=SearchPages(matches), clear_reactions_after=True
            )
            return await pages.start(ctx)
        await ctx.send("No matches found.")


def setup(bot):
    bot.add_cog(Tools(bot))
