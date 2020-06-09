import discord
from discord.ext import commands

from datetime import datetime
import dateparser
import aiohttp
import importlib
import re
import os
import ast

from .utils import aiopypi, aioxkcd, fuzzy, colors
from .utils.utils import SphinxObjectFileReader


class Internet(commands.Cog):
    """Various commands that use the internet.

    Okay yes, I know. Technically all the commands use the
    internet because I have to communicate with Discord.

    However, these are commands that use external APIs.
    """

    def __init__(self, bot):
        self.bot = bot
        self.emoji = ":globe_with_meridians:"

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
            timestamp=datetime.utcnow(),
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
            name=owner["login"], url=owner["html_url"], icon_url=owner["avatar_url"],
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

    @commands.command(hidden=True)
    @commands.is_owner()
    async def reload_xkcd(self, ctx):
        importlib.reload(aioxkcd)
        await ctx.send("It has been done.")

    @commands.group(
        name="xkcd",
        description="Fetch an xdcd comic",
        usage="<comic> (random if left blank)",
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
            # em = discord.Embed(colour = colors.PRIMARY)
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

        em = discord.Embed(colour=colors.PRIMARY)
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
    bot.add_cog(Internet(bot))
