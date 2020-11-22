import discord
from discord.ext import commands, menus

from datetime import datetime
from urllib.parse import urlparse
import dateparser
import aiohttp
import importlib
import re
import os
import ast
import base64
import json
import functools
import io
import async_cse
import mediawiki
from PIL import Image
from bs4 import BeautifulSoup

from .utils import aiopypi, aioxkcd, fuzzy, colors
from .utils.menus import MenuPages
from .utils.utils import SphinxObjectFileReader
from .utils.human_time import plural


class DocsSource(menus.ListPageSource):
    def __init__(self, entries, obj):
        super().__init__(entries, per_page=6)
        self.object = obj

    def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page

        em = discord.Embed(
            title=f"`Results for '{self.object}'`", colour=colors.PRIMARY
        )
        em.set_footer(
            text=f"{len(self.entries)} results | Page {menu.current_page + 1}/{self.get_max_pages()}"
        )

        matches = []

        for i, (key, url) in enumerate(entries, start=offset):
            matches.append(f"[`{key}`]({url})")

        em.description = "\n".join(matches)

        return em


class GoogleResultPages(menus.ListPageSource):
    def __init__(self, entries, query):
        super().__init__(entries, per_page=1)
        self.query = query

    def format_page(self, menu, entry):
        em = discord.Embed(title=entry.title, url=entry.url, color=0x4285F4)

        parsed = urlparse(entry.url)
        simplified_url = parsed.netloc
        if parsed.path not in ["/", "\\"]:
            simplified_url += parsed.path

        em.description = f"[`{simplified_url}`]({entry.url})\n\n{entry.description}"
        em.set_author(name=f"Google results for '{self.query}'")

        if entry.image_url:
            em.set_thumbnail(url=entry.image_url)

        em.set_footer(
            text=f"{plural(len(self.entries)):result} | Result {menu.current_page + 1}/{self.get_max_pages()}"
        )

        return em


class WolframResultSource(menus.ListPageSource):
    def __init__(self, result, query):
        super().__init__(list(result["pod"][1:]), per_page=1)
        self.result = result
        self.query = query

    def resolve_subpod_key(self, variable, key):
        if isinstance(variable, list):
            return variable[0][key]
        else:
            return variable[key]

    def format_page(self, menu, pod):
        em = discord.Embed(color=0xDD1100)
        em.set_author(name=f"Wolfram Alpha result for '{self.query}'")

        input_pod = self.result["pod"][0]
        em.add_field(
            name=input_pod["@title"],
            value=self.resolve_subpod_key(input_pod["subpod"], "plaintext"),
            inline=False,
        )
        em.add_field(
            name=pod["@title"],
            value=self.resolve_subpod_key(pod["subpod"], "plaintext") or "\u200b",
            inline=False,
        )

        img = self.resolve_subpod_key(pod["subpod"], "img")
        if img:
            em.set_image(url=img["@src"])

        em.set_footer(
            text=f"{plural(len(self.entries)):pod} | Pod {menu.current_page + 1}/{self.get_max_pages()}"
        )

        return em


class Internet(commands.Cog):
    """Various commands that use the internet.

    Okay yes, I know. Technically all the commands use the
    internet because I have to communicate with Discord.

    However, these are commands that use external APIs.
    """

    def __init__(self, bot):
        self.bot = bot
        self.emoji = ":globe_with_meridians:"

        self.wikipedia = mediawiki.MediaWiki()

    async def search_wikipedia(self, ctx, query, *, color=0x6B6B6B, sentences=2):
        partial = functools.partial(self.wikipedia.search, query)

        async with ctx.typing():
            search_results = await self.bot.loop.run_in_executor(None, partial)

        if not search_results:
            return await ctx.send(
                "Question could not be resolved. Try wording it differently?"
            )

        async with ctx.typing():
            try:
                partial = functools.partial(self.wikipedia.page, search_results[0])
                page = await self.bot.loop.run_in_executor(None, partial)

            except mediawiki.DisambiguationError as e:
                partial = functools.partial(self.wikipedia.page, e.options[0])
                page = await self.bot.loop.run_in_executor(None, partial)

        title = page.title
        summary = page.content.split("\n\n")[0]

        content_sentences = summary.split(". ")

        if len(content_sentences) > sentences:
            summary = ". ".join(content_sentences[:sentences])

        if len(summary) > 1048:
            summary = summary[:1048] + "..."

        description = f"{summary}\n\n[Read more]({page.url})"

        em = discord.Embed(title=title, description=description, color=color, url=page.url)
        em.set_author(name=f"Wikipedia result for '{query}'")

        await ctx.send(embed=em)

    @commands.command(aliases=["q"])
    @commands.cooldown(5, 30, commands.BucketType.user)
    async def question(self, ctx, *, question):
        """Ask the bot for information

        The bot will query wolfram alpha and then wikipedia
        if wolfram alpha returns no results.
        """
        partial = functools.partial(self.bot.wolfram.query, question)

        async with ctx.typing():
            result = await self.bot.loop.run_in_executor(None, partial)

        if result["@success"] != "true":
            return await self.search_wikipedia(ctx, question, color=0xDD1100)

        menu = MenuPages(WolframResultSource(result, question), clear_reactions_after=True)
        await menu.start(ctx)

    @commands.command(aliases=["wiki"])
    @commands.cooldown(5, 30, commands.BucketType.user)
    async def wikipedia(self, ctx, *, query):
        """Search Wikipedia for an article"""
        await self.search_wikipedia(ctx, query)

    @commands.command(aliases=["wolframalpha"])
    @commands.cooldown(5, 30, commands.BucketType.user)
    async def wolfram(self, ctx, *, query):
        """Make a query to Wolfram Alpha and return the result"""
        partial = functools.partial(self.bot.wolfram.query, query)

        async with ctx.typing():
            result = await self.bot.loop.run_in_executor(None, partial)

        if result["@success"] != "true":
            return await ctx.send(
                "Query could not be resolved. Try wording it differently?"
            )

        menu = MenuPages(WolframResultSource(result, query), clear_reactions_after=True)
        await menu.start(ctx)

    @commands.command(
        description="Preform a google search and display the results", aliases=["g"]
    )
    @commands.cooldown(5, 30, commands.BucketType.user)
    async def google(self, ctx, *, query):
        google_client = self.bot.google_client

        try:
            results = await google_client.search(query, safesearch=False)

        except async_cse.NoResults:
            return await ctx.send(f"No results for `{query}`. Sorry.")

        pages = MenuPages(GoogleResultPages(results, query), clear_reactions_after=True)
        await pages.start(ctx)

    @commands.group(
        description="Fetch a PyPI package.",
        aliases=["package", "pip"],
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

    def crop_skin(self, raw_img):
        img = Image.open(raw_img)
        # coords of the face in the skin
        cropped = img.crop((8, 8, 16, 16))
        resized = cropped.resize((500, 500), resample=Image.NEAREST)

        output = io.BytesIO()
        resized.save(output, format="png")
        output.seek(0)

        return output

    @commands.command(
        description="Fetch information about a Minecraft user", aliases=["mc"]
    )
    @commands.cooldown(2, 30, commands.BucketType.user)
    async def minecraft(self, ctx, *, user):
        # Get the user's UUID
        async with self.bot.session.get(
            f"https://api.mojang.com/users/profiles/minecraft/{user}"
        ) as resp:
            if resp.status != 200:
                return await ctx.send("Could not find user. Sorry")

            data = await resp.json()

        name = data["name"]
        uuid = data["id"]

        # Get the user's name history
        async with self.bot.session.get(
            f"https://api.mojang.com/user/profiles/{uuid}/names"
        ) as resp:
            if resp.status != 200:
                return await ctx.send(
                    "An error occurred while fetching name history from Mojang. Sorry."
                )

            name_history = await resp.json()

        previous_names = []

        for name_data in reversed(name_history):
            p_name = name_data["name"]
            timestamp = name_data.get("changedToAt")

            if not timestamp:
                previous_names.append(f"{p_name} (N/A)")
                continue

            seconds = timestamp / 1000
            date = datetime.fromtimestamp(seconds + (timestamp % 1000.0) / 1000.0)

            date_str = date.strftime("%m/%d/%y")
            human_friendly = f"{p_name} ({date_str})"
            previous_names.append(discord.utils.escape_markdown(human_friendly))

        # Get more information about the user
        async with self.bot.session.get(
            f"https://sessionserver.mojang.com/session/minecraft/profile/{uuid}"
        ) as resp:
            if resp.status != 200:
                return await ctx.send(
                    "An error occurred while fetching profile data from Mojang. Sorry."
                )

            profile_data = await resp.json()

        raw_texture_data = profile_data["properties"][0]["value"]
        texture_data = json.loads(base64.b64decode(raw_texture_data))

        # Get the skin image itself
        async with self.bot.session.get(
            texture_data["textures"]["SKIN"]["url"]
        ) as resp:
            if resp.status != 200:
                return await ctx.send(
                    "An error occurred while fetching skin data from Mojang. Sorry."
                )

            bytes = await resp.read()
            img = io.BytesIO(bytes)

        # Crop out only the face of the skin
        partial = functools.partial(self.crop_skin, img)
        face = await self.bot.loop.run_in_executor(None, partial)

        em = discord.Embed(
            title=name,
            color=0x70B237,
        )
        em.set_thumbnail(url="attachment://face.png")
        em.set_footer(text=f"UUID: {uuid}")

        formatted_names = "\n".join(previous_names)
        em.add_field(name="Previous Names", value=formatted_names)

        file = discord.File(face, filename="face.png")
        await ctx.send(embed=em, file=file)

    @commands.command(
        description="Fetch info about a Roblox profile", usage="[username]"
    )
    @commands.cooldown(2, 30, commands.BucketType.user)
    async def roblox(self, ctx, *, username):
        # Okay, so the Roblox API is a bit strange. You have to make
        # separate API requests to each API to get info. That means
        # I have to make a bunch of requests, which makes me enforce
        # a heavy cooldown. Most of this command uses the standard API, except
        # for the avatar. For some reason, Roblox does not provide a way (that I could find)
        # to get an avatar image URL. To get the avatar, I had to preform some
        # web scraping, which slows the command down quite a bit.
        # If I knew of a better way to do this, I would most certainly do it.

        await ctx.trigger_typing()

        session = self.bot.session

        # See if the user exists
        async with session.get(
            f"http://api.roblox.com/users/get-by-username/?username={username}"
        ) as resp:
            if resp.status != 200:
                return await ctx.send("I couldn't find that user. Sorry.")

            profile = await resp.json()

        if not profile.get("success") and not profile.get("Id"):
            return await ctx.send("I couldn't find that user. Sorry.")

        # Get basic info about them
        async with session.get(
            f"https://users.roblox.com/v1/users/{profile['Id']}"
        ) as resp:
            if resp.status != 200:
                return await ctx.send("I couldn't fetch that user. Sorry.")
            user_data = await resp.json()

        description = user_data["description"]
        created_at = dateparser.parse(user_data["created"])

        profile_url = f"https://www.roblox.com/users/{profile['Id']}/profile"

        # Get the avatar URL by web scraping
        async with session.get(profile_url) as resp:
            if resp.status != 200:
                return await ctx.send("I couldn't fetch that user's avatar. Sorry.")

            html = await resp.read()
            html = html.decode("utf-8")

        soup = BeautifulSoup(html, "html.parser")

        links = soup.find_all("img")

        avatar = links[0].get("src")

        # Get friend count
        async with session.get(
            f"https://friends.roblox.com/v1/users/{profile['Id']}/friends/count"
        ) as resp:
            if resp.status != 200:
                return await ctx.send(
                    "I couldn't fetch that user's friend count. Sorry."
                )
            friends_data = await resp.json()

        # Get status
        async with session.get(
            f"https://users.roblox.com/v1/users/{profile['Id']}/status"
        ) as resp:
            if resp.status != 200:
                return await ctx.send("I couldn't fetch that user's status. Sorry.")
            status_data = await resp.json()

        em = discord.Embed(
            title=profile["Username"],
            url=profile_url,
            description=description,
            timestamp=created_at,
            color=colors.PRIMARY,
        )

        em.set_thumbnail(url=avatar)
        em.set_footer(text="Created")

        em.add_field(
            name="Status", value=status_data.get("status") or "No status", inline=False
        )
        em.add_field(
            name="Friends",
            value=friends_data.get("count") or "No friends",
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
            url=data["html_url"],
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
            url=data["html_url"],
            timestamp=created_at,
        )

        # 2008-01-14T04:33:35Z

        em.set_footer(text="Created")

        owner = data["owner"]

        em.set_author(
            name=owner["login"],
            url=owner["html_url"],
            icon_url=owner["avatar_url"],
        )
        em.set_thumbnail(url=owner["avatar_url"])

        description = data["description"]
        if data["fork"]:
            parent = data["parent"]
            description = (
                f"Forked from [{parent['full_name']}]({parent['html_url']})\n\n"
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
        usage="<repo|user>",
        aliases=["gh"],
    )
    @commands.cooldown(5, 30)
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

    @commands.command(hidden=True)
    @commands.is_owner()
    async def reload_xkcd(self, ctx):
        importlib.reload(aioxkcd)
        await ctx.send("It has been done.")

    @commands.group(
        name="xkcd",
        description="Fetch an xdcd comic",
        invoke_without_command=True,
    )
    async def _xkcd(self, ctx, number: int = None):
        if not number:
            return await self._random_xkcd(ctx)
        try:
            comic = await aioxkcd.get_comic(number)
        except aioxkcd.XkcdError:
            return await ctx.send("That comic does not exist!")
        em = discord.Embed(
            title=f"#{comic.number} - {comic.title}",
            description=comic.alt_text,
            color=colors.PRIMARY,
            url=comic.url,
        )
        em.set_image(url=comic.image_url)
        em.set_footer(
            text=f"Comic published {comic.date_str}", icon_url=self.bot.user.avatar_url
        )
        await ctx.send(embed=em)

    @_xkcd.command(
        name="random", description="Fetch a random xdcd comic", aliases=["r"]
    )
    async def _random_xkcd(self, ctx):
        comic = await aioxkcd.get_random_comic()
        em = discord.Embed(
            title=f"#{comic.number} - {comic.title}",
            description=comic.alt_text,
            color=colors.PRIMARY,
            url=comic.url,
        )
        em.set_image(url=comic.image_url)
        em.set_footer(
            text=f"Comic published {comic.date_str}", icon_url=self.bot.user.avatar_url
        )
        await ctx.send(embed=em)

    @_xkcd.command(name="latest", description="Fetch the latest xkcd comic")
    async def _latest_xkcd(self, ctx):
        comic = await aioxkcd.get_latest_comic()
        em = discord.Embed(
            title=f"#{comic.number} - {comic.title}",
            description=comic.alt_text,
            color=colors.PRIMARY,
            url=comic.url,
        )
        em.set_image(url=comic.image_url)
        em.set_footer(
            text=f"Comic published {comic.date_str}", icon_url=self.bot.user.avatar_url
        )
        await ctx.send(embed=em)

    # https://github.com/Rapptz/RoboDanny/blob/rewrite/cogs/api.py#L198-L345
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

            if projname == "telegram.py":
                key = key.replace("telegrampy.ext.commands.", "").replace(
                    "telegrampy.", ""
                )

            result[f"{prefix}{key}"] = os.path.join(url, location)

        return result

    async def build_docs_lookup_table(self, page_types):
        cache = {}
        for key, page in page_types.items():
            sub = cache[key] = {}
            async with self.bot.session.get(page + "/objects.inv") as resp:
                if resp.status != 200:
                    raise RuntimeError(
                        "Cannot build docs lookup table, try again later."
                    )

                stream = SphinxObjectFileReader(await resp.read())
                cache[key] = self.parse_object_inv(stream, page)

        self._docs_cache = cache

    async def do_docs(self, ctx, key, obj):
        page_types = {
            "latest": "https://discordpy.readthedocs.io/en/latest",
            "stable": "https://discordpy.readthedocs.io/en/stable",
            "python": "https://docs.python.org/3",
            "aiohttp": "https://docs.aiohttp.org/en/stable",
            "asyncpg": "https://magicstack.github.io/asyncpg/current",
            "flask": "https://flask.palletsprojects.com/en/1.1.x",
            "sqlalchemy": "https://docs.sqlalchemy.org/en/13",
            "telegrampy": "https://telegrampy.readthedocs.io/en/latest",
        }

        if obj is None:
            await ctx.send(page_types[key])
            return

        if not hasattr(self, "_docs_cache"):
            await ctx.trigger_typing()
            await self.build_docs_lookup_table(page_types)

        obj = re.sub(r"^(?:discord\.(?:ext\.)?)?(?:commands\.)?(.+)", r"\1", obj)
        obj = re.sub(r"^(?:telegrampy\.(?:ext\.)?)?(?:commands\.)?(.+)", r"\1", obj)

        if key.startswith("latest") or key.startswith("stable"):
            # point the abc.Messageable types properly:
            q = obj.lower()
            for name in dir(discord.abc.Messageable):
                if name[0] == "_":
                    continue
                if q == name:
                    obj = f"abc.Messageable.{name}"
                    break

        cache = list(self._docs_cache[key].items())

        def transform(tup):
            return tup[0]

        matches = fuzzy.finder(obj, cache, key=lambda t: t[0], lazy=False)

        if len(matches) == 0:
            return await ctx.send("Could not find anything. Sorry.")

        pages = MenuPages(source=DocsSource(matches, obj), clear_reactions_after=True)
        await pages.start(ctx)

    @commands.group(
        aliases=["rtfm", "rtfd"],
        invoke_without_command=True,
    )
    async def docs(self, ctx, *, obj: str = None):
        """Searches discord.py documentation and returns a list of matching entities.
        Events, objects, and functions are all supported through a
        a cruddy fuzzy algorithm.
        """
        await self.do_docs(ctx, "latest", obj)

    @docs.command(name="stable", aliases=["st"])
    async def docs_stable(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a discord.py stable entity."""
        await self.do_docs(ctx, "stable", obj)

    @docs.command(name="python", aliases=["py"])
    async def docs_python(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a Python entity."""
        await self.do_docs(ctx, "python", obj)

    @docs.command(name="aiohttp", aliases=["ah"])
    async def docs_aiohttp(self, ctx, *, obj: str = None):
        """Gives you a documentation link for an aiohttp entity."""
        await self.do_docs(ctx, "aiohttp", obj)

    @docs.command(name="asyncpg", aliases=["pg"])
    async def docs_asyncpg(self, ctx, *, obj: str = None):
        """Gives you a documentation link for an asyncpg entity."""
        await self.do_docs(ctx, "asyncpg", obj)

    @docs.command(name="flask", aliases=["fl"])
    async def docs_flask(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a Flask entity."""
        await self.do_docs(ctx, "flask", obj)

    @docs.command(name="sqlalchemy", aliases=["sqla"])
    async def docs_sqlalchemy(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a SQLAlchemy entity."""
        await self.do_docs(ctx, "sqlalchemy", obj)

    @docs.command(name="telegram.py", aliases=["tpy", "telegram"])
    async def docs_telegampy(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a telegram.py entity."""
        await self.do_docs(ctx, "telegrampy", obj)


def setup(bot):
    bot.add_cog(Internet(bot))
