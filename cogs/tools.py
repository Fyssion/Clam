from discord.ext import commands, menus
import discord

from datetime import datetime as d
from datetime import timedelta
import re
import os
import ast
import base64
import binascii
import humanize
import io
import functools
from PIL import Image
import typing
import dateparser

from .utils import fuzzy, colors
from .utils.utils import SphinxObjectFileReader


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
        description="Get the avatar of a member.",
        usage="<member>",
        aliases=["profilepic"],
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
        usage="<member>",
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
        if member == self.bot.user:
            desc += "\n:wave:Hey, that's me!"
        if member.bot is True:
            desc += "\n:robot: This user is a bot."
        if member.id == ctx.guild.owner_id:
            desc += "\n<:owner:649355683598303260> " "This user is the server owner."
        if member.id == self.bot.owner_id:
            desc += "\n:gear: This user owns this bot."
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

        em = discord.Embed(description=desc, color=color, timestamp=d.utcnow(),)

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

        em = discord.Embed(description=desc, color=color, timestamp=d.utcnow(),)

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
        usage="[snowflake]",
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

    @commands.command(description="Parse a Discord token", usage="[token]", hidden=True)
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
        usage="[username]",
        aliases=["user", "name"],
    )
    async def search_username(self, ctx, username: str):
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
        usage="[nickname]",
        aliases=["nick"],
    )
    async def search_nickname(self, ctx, nickname: str):
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
        usage="[discriminator]",
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

    def parse_object_inv(self, stream, url):
        # key: URL
        # n.b.: key doesn't have `discord` or `discord.ext.commands` namespaces
        result = {}

        # first line is version info
        inv_version = stream.readline().rstrip()

        if inv_version != "# Sphinx inventory version 2":
            raise RuntimeError("Invalid objects.inv file version.")

        # next line is "# Project: <name>"
        # then after that is "# Version: <version>"
        projname = stream.readline().rstrip()[11:]
        version = stream.readline().rstrip()[11:]

        # next line says if it's a zlib header
        line = stream.readline()
        if "zlib" not in line:
            raise RuntimeError("Invalid objects.inv file, not z-lib compatible.")

        # This code mostly comes from the Sphinx repository.
        entry_regex = re.compile(r"(?x)(.+?)\s+(\S*:\S*)\s+(-?\d+)\s+(\S+)\s+(.*)")
        for line in stream.read_compressed_lines():
            match = entry_regex.match(line.rstrip())
            if not match:
                continue

            name, directive, prio, location, dispname = match.groups()
            domain, _, subdirective = directive.partition(":")
            if directive == "py:module" and name in result:
                # From the Sphinx Repository:
                # due to a bug in 1.1 and below,
                # two inventory entries are created
                # for Python modules, and the first
                # one is correct
                continue

            # Most documentation pages have a label
            if directive == "std:doc":
                subdirective = "label"

            if location.endswith("$"):
                location = location[:-1] + name

            key = name if dispname == "-" else dispname
            prefix = f"{subdirective}:" if domain == "std" else ""

            if projname == "discord.py":
                key = key.replace("discord.ext.commands.", "").replace("discord.", "")

            result[f"{prefix}{key}"] = os.path.join(url, location)

        return result

    async def build_rtfm_lookup_table(self, page_types):
        cache = {}
        for key, page in page_types.items():
            sub = cache[key] = {}
            async with self.bot.session.get(page + "/objects.inv") as resp:
                if resp.status != 200:
                    raise RuntimeError(
                        "Cannot build rtfm lookup table, try again later."
                    )

                stream = SphinxObjectFileReader(await resp.read())
                cache[key] = self.parse_object_inv(stream, page)

        self._rtfm_cache = cache

    async def do_rtfm(self, ctx, key, obj):
        page_types = {
            "latest": "https://discordpy.readthedocs.io/en/latest",
            "latest-jp": "https://discordpy.readthedocs.io/ja/latest",
            "python": "https://docs.python.org/3",
            "python-jp": "https://docs.python.org/ja/3",
        }

        if obj is None:
            await ctx.send(page_types[key])
            return

        if not hasattr(self, "_rtfm_cache"):
            await ctx.trigger_typing()
            # em = discord.Embed(colour = discord.Colour.blurple())
            # em.add_field(name = "\u200b", value = ":mag: `Searching the docs...`")
            # bot_msg = await ctx.send(embed = em)
            await self.build_rtfm_lookup_table(page_types)

        obj = re.sub(r"^(?:discord\.(?:ext\.)?)?(?:commands\.)?(.+)", r"\1", obj)

        if key.startswith("latest"):
            # point the abc.Messageable types properly:
            q = obj.lower()
            for name in dir(discord.abc.Messageable):
                if name[0] == "_":
                    continue
                if q == name:
                    obj = f"abc.Messageable.{name}"
                    break

        cache = list(self._rtfm_cache[key].items())

        def transform(tup):
            return tup[0]

        matches = fuzzy.finder(obj, cache, key=lambda t: t[0], lazy=False)[:7]

        em = discord.Embed(colour=discord.Colour.blurple())
        if len(matches) == 0:
            return await ctx.send("Could not find anything. Sorry.")
        em.add_field(
            name=f"`Results for '{obj}'`",
            value="\n".join(f"[`{key}`]({url})" for key, url in matches),
        )
        # em.description = '\n'.join(f'[`{key}`]({url})' for key, url in matches)
        # await bot_msg.edit(embed = em)
        await ctx.send(embed=em)

    def transform_rtfm_language_key(self, ctx, prefix):
        if ctx.guild is not None:
            #                             日本語 category
            if ctx.channel.category_id == 490287576670928914:
                return prefix + "-jp"
            #                    d.py unofficial JP
            elif ctx.guild.id == 463986890190749698:
                return prefix + "-jp"
        return prefix

    @commands.group(
        aliases=["rtfm", "rtfd"],
        invoke_without_command=True,
        description="Gives you a documentation link for a discord.py entity.",
    )
    async def docs(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a discord.py entity.
        Events, objects, and functions are all supported through a
        a cruddy fuzzy algorithm.
        """
        key = self.transform_rtfm_language_key(ctx, "latest")
        await self.do_rtfm(ctx, key, obj)

    @docs.command(name="python", aliases=["py"])
    async def docs_python(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a Python entity."""
        key = self.transform_rtfm_language_key(ctx, "python")
        await self.do_rtfm(ctx, key, obj)

    def insert_returns(self, body):
        # insert return stmt if the last expression is a expression statement
        if isinstance(body[-1], ast.Expr):
            body[-1] = ast.Return(body[-1].value)
            ast.fix_missing_locations(body[-1])

        # for if statements, we insert returns into the body and the orelse
        if isinstance(body[-1], ast.If):
            insert_returns(body[-1].body)
            insert_returns(body[-1].orelse)

        # for with blocks, again we insert returns into the body
        if isinstance(body[-1], ast.With):
            insert_returns(body[-1].body)


def setup(bot):
    bot.add_cog(Tools(bot))
