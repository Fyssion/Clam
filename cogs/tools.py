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

from .utils import fuzzy, aiopypi
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

    @commands.command(
        name="userinfo",
        description="Get information about a user",
        aliases=["memberinfo", "ui", "whois"],
        usage="[user]",
    )
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
        if icon:
            bytes = io.BytesIO(await icon.read())
            partial = functools.partial(Image.open, bytes)
            image = await self.bot.loop.run_in_executor(None, partial)
            partial = functools.partial(image.resize, (1, 1))
            resized = await self.bot.loop.run_in_executor(None, partial)
            partial = functools.partial(resized.getpixel, (0, 0))
            color = await self.bot.loop.run_in_executor(None, partial)
            try:
                hex_string = "0x{:02x}{:02x}{:02x}".format(*color)
                color = discord.Color(int(hex_string, 16))
            except TypeError:
                color = member.color or discord.Color.blurple()
        else:
            if member.color:
                color = member.color
            else:
                color = discord.Color.blurple()

        em = discord.Embed(description=desc, color=color, timestamp=d.utcnow(),)

        em.set_thumbnail(url=member.avatar_url)
        em.set_author(name=author, icon_url=member.avatar_url)
        em.set_footer(
            text=f"Requested by {str(ctx.author)}", icon_url=self.bot.user.avatar_url
        )
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
        if icon:
            bytes = io.BytesIO(await icon.read())
            partial = functools.partial(Image.open, bytes)
            image = await self.bot.loop.run_in_executor(None, partial)
            partial = functools.partial(image.resize, (1, 1))
            resized = await self.bot.loop.run_in_executor(None, partial)
            partial = functools.partial(resized.getpixel, (0, 0))
            color = await self.bot.loop.run_in_executor(None, partial)
            try:
                hex_string = "0x{:02x}{:02x}{:02x}".format(*color)
                color = discord.Color(int(hex_string, 16))
            except TypeError:
                color = discord.Color.blurple()
        else:
            color = discord.Color.blurple()

        em = discord.Embed(description=desc, color=color, timestamp=d.utcnow(),)

        em.set_thumbnail(url=guild.icon_url)
        if guild.banner_url:
            em.set_image(url=guild.banner_url)
        em.set_author(name=f"{guild.name} ({guild.id})", icon_url=guild.icon_url)
        em.set_footer(
            text=f"Requested by {ctx.author.name}#{ctx.author.discriminator}",
            icon_url=self.bot.user.avatar_url,
        )
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

    @commands.group(
        description="Fetch a PyPI package.",
        usage="[package] <version>",
        aliases=["package"],
        invoke_without_command=True,
    )
    async def pypi(self, ctx, package, release=None):
        if not release:
            try:
                package = await aiopypi.fetch_package(package)
            except aiopypi.PackageNotFoundError:
                return await ctx.send(
                    f"Package `{package}` not found. Sorry about that."
                )
        else:
            try:
                package = await aiopypi.fetch_package_release(package, release)
            except aiopypi.PackageNotFoundError:
                return await ctx.send(
                    f"Package `{package}` with release `{release}` not found. Sorry about that."
                )
        title = f"{package} {package.version}"
        em = discord.Embed(
            title=title,
            url=package.package_url,
            description=package.summary,
            color=0x0073B7,
            timestamp=d.utcnow(),
        )

        em.set_thumbnail(url="https://i.imgur.com/fGCuXc2.png")

        author = package.author
        if package.author_email:
            author += f" ({package.author_email})"

        em.set_author(name=author)

        installation = f"**`pip install {package.name}"
        installation += f"=={release}`**\n" if release else "`**\n"

        em.description = installation + em.description

        useful_info = []
        if package.home_page:
            useful_info.append(f"[Homepage]({package.home_page})")
        if package.bugtrack_url:
            useful_info.append(f"[Bugtracker]({package.bugtrack_url})")
        if package.license:
            useful_info.append(f"License: {package.license}")
        if package.requires_python:
            useful_info.append(f"Requires python {package.requires_python}")

        if useful_info:
            em.add_field(name="Useful Info", value="\n".join(useful_info))

        releases_text = []
        release_url = "(https://pypi.org/project/{0.name}/{1})"
        for i, release_ in enumerate(reversed(package.releases)):
            if i > 4:
                releases_text.append(f"...and {len(package.releases) - i} more.")
                break
            text = f"[{release_.version}"
            if str(release_) == package.version and not release:
                text += " (latest)"
            text += "]"
            releases_text.append(text + release_url.format(package, release_))

        em.add_field(
            name=f"Releases ({len(package.releases)} total)",
            value="\n".join(releases_text),
        )

        urls_text = []
        for i, url in enumerate(package.project_urls):
            if i > 4:
                urls_text.append(f"...and {len(package.project_urls) - i} more.")
                break
            urls_text.append(f"[{url}]({package.project_urls[url]})")

        if urls_text:
            em.add_field(
                name=f"Project Links ({len(package.project_urls)} total)",
                value="\n".join(urls_text),
            )

        requires_text = []
        for i, requirement in enumerate(package.requires_dist):
            if i > 4:
                requires_text.append(f"...and {len(package.requires_dist) - i} more.")
                break
            # words = requirement.split(" ")
            # requirement = words[0]
            # if len(requirement) < 18:
            #     requires_text.append(requirement)
            # else:
            #     requires_text.append("".join(requirement[:15]) + "...")
            requires_text.append(requirement)

        if requires_text:
            em.add_field(
                name=f"Requirements ({len(package.requires_dist)} total)",
                value="\n".join(requires_text),
                inline=False,
            )

        await ctx.send(embed=em)

    async def send_user_info(self, ctx, data):
        if data["name"]:
            name = f"{data['name']} ({data['login']})"
        else:
            name = data["login"]

        created_at = dateparser.parse(data["created_at"])

        em = discord.Embed(
            title=name,
            description=data["bio"],
            color=0x4078C0,
            url=data["url"],
            timestamp=created_at,
        )

        em.set_footer(text="Joined")

        em.set_thumbnail(url=data["avatar_url"])

        em.add_field(
            name="Public Repos",
            value=data["public_repos"] or "No public repos",
            inline=True,
        )

        if data["public_gists"]:
            em.add_field(name="Public Gists", value=data["public_gists"], inline=True)

        value = [
            "Followers: " + str(data["followers"])
            if data["followers"]
            else "Followers: no followers"
        ]
        value.append(
            "Following: " + str(data["following"])
            if data["following"]
            else "Following: not following anyone"
        )

        em.add_field(name="Followers/Following", value="\n".join(value), inline=True)

        if data["location"]:
            em.add_field(name="Location", value=data["location"], inline=True)
        if data["company"]:
            em.add_field(name="Company", value=data["company"], inline=True)
        if data["blog"]:
            blog = data["blog"]
            if blog.startswith("https://") or blog.startswith("http://"):
                pass
            else:
                blog = "https://" + blog
            em.add_field(name="Website", value=blog, inline=True)

        await ctx.send(embed=em)

    async def send_repo_info(self, ctx, data):
        created_at = dateparser.parse(data["created_at"])
        em = discord.Embed(
            title=data["full_name"],
            color=0x4078C0,
            url=data["url"],
            timestamp=created_at,
        )

        # 2008-01-14T04:33:35Z

        em.set_footer(text="Created")

        owner = data["owner"]

        em.set_author(
            name=owner["login"], url=owner["url"], icon_url=owner["avatar_url"],
        )
        em.set_thumbnail(url=owner["avatar_url"])

        description = data["description"]
        if data["fork"]:
            parent = data["parent"]
            description = (
                f"Forked from [{parent['full_name']}]({parent['url']})\n\n"
                + description
            )

        if data["homepage"]:
            description += "\n" + data["homepage"]

        em.description = description

        em.add_field(name="Language", value=data["language"] or "No language")
        em.add_field(
            name="Stars", value=data["stargazers_count"] or "No Stars", inline=True
        )
        em.add_field(
            name="Watchers", value=data["watchers_count"] or "No watchers", inline=True
        )
        em.add_field(name="Forks", value=data["forks_count"] or "No forks", inline=True)

        await ctx.send(embed=em)

    async def fetch_from_github(self, url):
        GITHUB_API = "https://api.github.com/"

        async with self.bot.session.get(GITHUB_API + url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data
            return None

    @commands.command(
        description="Fetch a repo or user from GitHub and display info about it.",
        usage="[repo or user]",
        aliases=["gh"],
    )
    @commands.cooldown(2, 5.0, commands.BucketType.member)
    async def github(self, ctx, item):

        # Basically, this command checks if a / was found in
        # the requested item. If there was a /, it searches for
        # a repo.
        # If not, it searches for a user.
        # This is because I want this command to be dynamic.
        # To prevent API abuse, I have this command on a cooldown.

        if "/" in item:
            data = await self.fetch_from_github("repos/" + item)
            if data:
                return await self.send_repo_info(ctx, data)
            else:
                return await ctx.send(f"Could not find a repo called `{item}`. Sorry.")

        data = await self.fetch_from_github("users/" + item)
        if data:
            return await self.send_user_info(ctx, data)

        await ctx.send(f"Could not find a user called `{item}`. Sorry.")

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
        em.set_footer(
            text=f"Requested by {ctx.author.name}#{ctx.author.discriminator}",
            icon_url=self.bot.user.avatar_url,
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
