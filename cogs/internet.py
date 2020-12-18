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
import traceback
from lxml import etree
import mediawiki
from urllib.parse import quote as uriquote
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

        self.log = bot.log

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

        em = discord.Embed(
            title=title, description=description, color=color, url=page.url
        )
        em.set_author(name=f"Wikipedia result for '{query}'")

        await ctx.send(embed=em)

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

    # modified google command from Rapptz
    def parse_google_card(self, node):
        e = discord.Embed(colour=0x4285F4)

        # check if it's a calculator card:
        calculator = node.find(".//div[@class='tyYmIf']")
        if calculator is not None:
            e.title = "Calculator"
            result = node.find(".//span[@class='qv3Wpe']")
            if result is not None:
                result = " ".join((calculator.text, result.text.strip()))
            else:
                result = calculator.text + " ???"
            e.description = result
            return e

        # check for unit conversion card

        # unit_conversions = node.xpath(".//input[contains(@class, '_eif') and @value]")
        unit_conversions = node.xpath(".//input[contains(@class, 'vXQmIe') and @value]")
        if len(unit_conversions) == 2:
            e.title = "Unit Conversion"

            # the <input> contains our values, first value = second value essentially.
            # these <input> also have siblings with <select> and <option selected=1>
            # that denote what units we're using

            # We will get 2 <option selected="1"> nodes by traversing the parent
            # The first unit being converted (e.g. Miles)
            # The second unit being converted (e.g. Feet)

            xpath = etree.XPath("parent::div/select/option[@selected='1']/text()")
            try:
                first_node = unit_conversions[0]
                first_unit = xpath(first_node)[0]
                first_value = float(first_node.get("value"))
                second_node = unit_conversions[1]
                second_unit = xpath(second_node)[0]
                second_value = float(second_node.get("value"))
                e.description = " ".join(
                    (str(first_value), first_unit, "=", str(second_value), second_unit)
                )
            except Exception:
                return None
            else:
                return e

        # check for currency conversion card
        if "obcontainer" in node.get("class", ""):
            currency_selectors = node.xpath(".//div[@class='J8cLR']")
            if len(currency_selectors) == 2:
                e.title = "Currency Conversion"
                # Inside this <div> is a <select> with <option selected="1"> nodes
                # just like the unit conversion card.

                first_node = currency_selectors[0]
                first_currency = first_node.find("./select/option[@selected='1']")

                second_node = currency_selectors[1]
                second_currency = second_node.find("./select/option[@selected='1']")

                # The parent of the nodes have a <input class='vk_gy vk_sh ccw_data' value=...>
                xpath = etree.XPath(
                    "parent::td/parent::tr/td/input[contains(@class, 'vk_gy vk_sh Hg3mWc')]"
                )
                try:
                    first_value = float(xpath(first_node)[0].get("value"))
                    second_value = float(xpath(second_node)[0].get("value"))

                    values = (
                        str(first_value),
                        first_currency.text,
                        f'({first_currency.get("value")})',
                        "=",
                        str(second_value),
                        second_currency.text,
                        f'({second_currency.get("value")})',
                    )
                    e.description = " ".join(values)
                except Exception:
                    return None
                else:
                    return e

        # check for generic information card
        info = node.find(".//div[@class='ifM9O']")
        if info is not None:
            try:
                title = info.find(".//span")
                if title is None:
                    try:
                        e.title = "".join(info.itertext()).strip()
                    except Exception:
                        pass
                else:
                    e.title = title.text
                actual_information = info.xpath(
                    ".//div[@class='_XWk' or@class='uyUSCd' or @class='Z0LcW XcVN5d AZCkJd' or contains(@class, 'kpd-ans')]"
                )[0]
                e.description = "".join(actual_information.itertext()).strip()
            except Exception:
                return None
            else:
                return e

        # check for translation card
        translation = node.find(".//div[@id='tw-ob']")
        if translation is not None:
            src_text = translation.find(".//pre[@id='tw-source-text']/span")
            dest_text = translation.find(".//pre[@id='tw-target-text']/span")

            # the language is in another div
            langs = node.find(".//div[@id='tw-plp']")
            if translation is None:
                return None

            src_lang = langs.find(".//div[@id='tw-sl']/span[@class='source-language']")
            dest_lang = langs.find(".//div[@id='tw-tl']/span[@class='target-language']")

            # TODO: bilingual dictionary nonsense?

            e.title = "Translation"
            try:
                e.add_field(name=src_lang.text, value=src_text.text, inline=True)
                e.add_field(name=dest_lang.text, value=dest_text.text, inline=True)
            except Exception:
                return None
            else:
                return e

        # check for "time in" card
        time = node.find("./div[@class='vk_c vk_gy vk_sh card-section sL6Rbf']")
        if time is not None:
            time = time[0]
            try:
                e.title = node.find("span").text
                e.description = f'{time.text}\n{"".join(time.itertext()).strip()}'
            except Exception:
                return None
            else:
                return e

        # time in has an alternative form without spans
        time = node.find("./div[@class='vk_bk vk_ans _nEd']")
        print("time2", time)
        if time is not None:
            converted = "".join(time.itertext()).strip()
            try:
                # remove the in-between text
                parent = time.getparent()
                parent.remove(time)
                original = "".join(parent.itertext()).strip()
                e.title = "Time Conversion"
                e.description = f"{original}...\n{converted}"
            except Exception:
                return None
            else:
                return e

        # check for definition card
        words = node.xpath(".//span[@data-dobid='hdw']")
        if words:
            lex = etree.XPath(".//div[@class='pgRvse vdBwhd']/i/span")

            # this one is derived if we were based on the position from lex
            xpath = etree.XPath(
                "../../../../../ol[@class='eQJLDd']//"
                "div[not(@class and @class='lr_dct_sf_subsen')]/"
                "div[@class='QIclbb']/div[@data-dobid='dfn']/span"
            )
            for word in words:
                # we must go three parents up to get the root node
                root = word.getparent().getparent().getparent().getparent()

                pronunciation = root.find(".//span[@class='XpoqFe']/span")
                if pronunciation is None:
                    continue

                lexical_category = lex(root)
                definitions = xpath(root)

                for category in lexical_category:
                    definitions = xpath(category)
                    try:
                        descrip = [f"*{category.text}*"]
                        for index, value in enumerate(definitions, 1):
                            descrip.append(f"{index}. {value.text}")

                        e.add_field(
                            name=f"{word.text} /{pronunciation.text}/",
                            value="\n".join(descrip),
                        )
                    except Exception:
                        continue

            return e

        # check for weather card
        location = node.find(".//div[@id='wob_loc']")
        if location is None:
            return None

        # these units should be metric

        date = node.find(".//div[@id='wob_dts']")

        # <img alt="category here" src="cool image">
        category = node.find(".//img[@id='wob_tci']")

        xpath = etree.XPath(
            ".//div[@id='wob_d']//div[contains(@class, 'vk_bk')]/..//span[contains(@class, 'wob_t')]"
        )
        temperatures = xpath(node)

        misc_info_node = node.find(".//div[@class='vk_gy vk_sh']")

        if misc_info_node is None:
            return None

        precipitation = misc_info_node.find("./div/span[@id='wob_pp']")
        humidity = misc_info_node.find("./div/span[@id='wob_hm']")
        wind = misc_info_node.find("./div/span/span[@id='wob_ws']")

        try:
            e.title = "Weather for " + location.text.strip()
            e.description = f'*{category.get("alt")}*'
            e.set_thumbnail(url="https:" + category.get("src"))

            if len(temperatures) == 4:
                first_unit = temperatures[0].text + temperatures[2].text
                second_unit = temperatures[1].text + temperatures[3].text
                units = f"{first_unit} | {second_unit}"
            else:
                units = "Unknown"

            e.add_field(name="Temperature", value=units, inline=False)

            if precipitation is not None:
                e.add_field(name="Precipitation", value=precipitation.text)

            if humidity is not None:
                e.add_field(name="Humidity", value=humidity.text)

            if wind is not None:
                e.add_field(name="Wind", value=wind.text)
        except Exception:
            traceback.print_exc()
            return None

        return e

    async def get_google_entries(self, query):
        url = f"https://www.google.com/search?q={uriquote(query)}"
        params = {"safe": "on", "lr": "lang_en", "hl": "en"}

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:83.0) Gecko/20100101 Firefox/83.0"
        }

        # list of URLs and title tuples
        entries = []

        # the result of a google card, an embed
        card = None

        async with self.bot.session.get(url, params=params, headers=headers) as resp:
            if resp.status != 200:
                self.log.info(
                    "Google failed to respond with %s status code.", resp.status
                )
                raise RuntimeError("Google has failed to respond.")

            root = etree.fromstring(await resp.text(), etree.HTMLParser())

            for bad in root.xpath("//style"):
                bad.getparent().remove(bad)

            for bad in root.xpath("//script"):
                bad.getparent().remove(bad)

            with open("google.html", "w", encoding="utf-8") as f:
                f.write(etree.tostring(root, pretty_print=True).decode("utf-8"))

            """
            Tree looks like this.. sort of..
            <div class="rc">
                <h3 class="r">
                    <a href="url here">title here</a>
                </h3>
            </div>
            """

            card_node = root.xpath(
                ".//div[@id='rso']/div[@class='ULSxyf' or @class='hlcw0c' or @class='g mnr-c g-blk']//"
                "div[contains(@class, 'vk_c') or @class='card-section' or @class='g mnr-c g-blk' "
                "or @class='kp-blk' or @class='RQXSBc' or @class='g obcontainer' "
                "or @class='xpdopen rYczAc' or @class='YQaNob']"
            )

            if card_node is None or len(card_node) == 0:
                card_node = root.xpath(
                    ".//div[@id='rso']//"
                    "div[contains(@class, 'vk_c') or @class='card-section' or @class='g mnr-c g-blk' "
                    "or @class='kp-blk' or @class='RQXSBc' or @class='g obcontainer' or @class='YQaNob']"
                )

            print("card node", card_node)

            if card_node is None or len(card_node) == 0:
                card = None
            else:
                card = self.parse_google_card(card_node[0])

            search_results = root.findall(".//div[@class='rc']")
            # print(len(search_results))
            for node in search_results:
                link = node.find("./div[@class='yuRUbf']/a")
                if link is not None:
                    # print(etree.tostring(link, pretty_print=True).decode())
                    span = link.find("./h3/div[@class='ellip']/span")
                    if span is None and link.text is None:
                        span = link.find(".//h3[@class='LC20lb DKV0Md']/span")
                        text = span.text if span is not None else "???"
                    else:
                        text = span.text if span is not None else link.text
                    entries.append((link.get("href"), text))

        return card, entries

    @commands.command(aliases=["google"])
    async def g(self, ctx, *, query):
        """Searches google and gives you the top result."""
        await ctx.trigger_typing()
        try:
            card, entries = await self.get_google_entries(query)
        except RuntimeError as e:
            await ctx.send(str(e))
        else:
            if card is not None:
                value = "\n".join(
                    f'[{title}]({url.replace(")", "%29")})'
                    for url, title in entries[:3]
                )
                if value:
                    card.add_field(name="Search Results", value=value, inline=False)
                return await ctx.send(embed=card)

            if len(entries) == 0:
                return await ctx.send("No results found... sorry.")

            next_two = [x[0] for x in entries[1:3]]
            first_entry = entries[0][0]
            if first_entry[-1] == ")":
                first_entry = first_entry[:-1] + "%29"

            if next_two:
                formatted = "\n".join(f"<{x}>" for x in next_two)
                msg = f"{first_entry}\n\n**See also:**\n{formatted}"
            else:
                msg = first_entry

            await ctx.send(msg)

    @commands.command(
        description="Preform a google search via the API and display the results",
        aliases=["gapi"],
    )
    @commands.cooldown(5, 30, commands.BucketType.user)
    async def googleapi(self, ctx, *, query):
        google_client = self.bot.google_client

        try:
            results = await google_client.search(query, safesearch=False)

        except async_cse.NoResults:
            return await ctx.send(f"No results for `{query}`. Sorry.")

        pages = MenuPages(GoogleResultPages(results, query), clear_reactions_after=True)
        await pages.start(ctx)

    @commands.command(aliases=["question"])
    @commands.cooldown(5, 30, commands.BucketType.user)
    async def q(self, ctx, *, question):
        """Ask the bot for information

        The bot will query wolfram alpha and then wikipedia
        if wolfram alpha returns no results.
        """
        partial = functools.partial(self.bot.wolfram.query, question)

        async with ctx.typing():
            result = await self.bot.loop.run_in_executor(None, partial)

        if result["@success"] != "true":
            return await ctx.invoke(self.g, query=question)

        menu = MenuPages(
            WolframResultSource(result, question), clear_reactions_after=True
        )
        await menu.start(ctx)

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

    async def get_roblox_profile(self, username):
        session = self.bot.session

        # See if the user exists
        async with session.get(
            f"http://api.roblox.com/users/get-by-username/?username={uriquote(username)}"
        ) as resp:
            if resp.status != 200:
                msg = f"Roblox has failed to respond with {resp.status} status code."
                self.log.info(msg)
                raise RuntimeError(msg)

            profile = await resp.json()

        if not profile.get("success") and not profile.get("Id"):
            raise RuntimeError("I couldn't find that user. Sorry.")

        base_url = f"https://www.roblox.com/users/{profile['Id']}"
        url = base_url + "/profile"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:83.0) Gecko/20100101 Firefox/83.0"
        }

        # the final profile (an embed)
        em = discord.Embed(title=profile["Username"], url=url, color=colors.PRIMARY)

        async with self.bot.session.get(url, headers=headers) as resp:
            if resp.status != 200:
                msg = f"Roblox has failed to respond with {resp.status} status code."
                self.log.info(msg)
                raise RuntimeError(msg)

            root = etree.fromstring(await resp.text(), etree.HTMLParser())

        # for bad in root.xpath("//style"):
        #     bad.getparent().remove(bad)

        # for bad in root.xpath("//script"):
        #     bad.getparent().remove(bad)

        # with open("roblox.html", "w", encoding="utf-8") as f:
        #     f.write(etree.tostring(root, pretty_print=True).decode("utf-8"))

        profile = root.xpath(".//div[contains(@class, 'profile-container')]")
        if profile is None or len(profile) == 0:
            raise RuntimeError("Failed to get info from Roblox.")

        profile = profile[0]

        # find the avatar
        avatar = profile.find(
            ".//div[@id='UserAvatar']/span[@class='thumbnail-span-original hidden']/img"
        )
        if avatar is not None:
            em.set_thumbnail(url=avatar.get("src"))

        # find user info
        divs = profile.xpath(
            "..//"
            # "div[@ng-controller='profileBaseController']/"
            # "div[@class='section profile-header']/"
            "div[@class='section-content profile-header-content']/"
            "div"
        )

        def insert_detail(detail, value, **embed_kwargs):
            if detail and value:
                em.add_field(name=detail, value=value, **embed_kwargs)

        def format_f_detail(details, detail, *, add_s=False):
            tag = f"{detail}s" if add_s else detail
            value = details.get(f"data-{tag}count")
            if not value:
                return None

            return f"{value} [(view)]({base_url}/friends#!/{detail})"

        if divs is not None and len(divs) > 0:
            details = divs[0]
            insert_detail("Friends", format_f_detail(details, "friends"))
            insert_detail("Followers", format_f_detail(details, "followers"))
            insert_detail(
                "Following", format_f_detail(details, "following", add_s=True)
            )

            status = details.get("data-statustext")
            set_status_at = details.get("data-statusdate")
            if status and set_status_at:
                # convert mm/dd/yyyy h:mm:ss to mm/dd/yyyy
                cut = set_status_at.split()[0]
                status += f"\n(set on {cut})"

            insert_detail("Status", status, inline=False)

        # getting other stats

        """
        Looks like this
        <ul class="profile-stats-container">
          <li class="profile-stat">
            <p class="text-label">Join Date</p>
            <p class="text-lead">x/x/xxxx</p>
          </li>
          <li class="profile-stat">
            <p class="text-label">Place Visits</p>
            <p class="text-lead">x</p>
          </li>
        </ul>
"""
        stats = profile.xpath(".//ul[@class='profile-stats-container']/li")

        if stats is not None and len(stats) > 0:
            for stat in stats:
                try:
                    paras = stat.xpath("./p")
                    detail = paras[0].text  # the title
                    value = paras[1].text  # the actual value
                    insert_detail(detail, value)
                except Exception:
                    continue

        # get whether they have premium
        emoji = "<:roblox_premium:789226760805023795>"
        premium = profile.xpath(".//span[contains(@class, 'icon-premium')]")
        if premium is not None and len(premium) > 0:
            em.description = f"{emoji} (this user has Roblox Premium)"

        # get the description
        description = profile.find(
            ".//span[@class='profile-about-content-text linkify']"
        )
        if description is not None:
            if em.description:
                em.description += f"\n\n{description.text}"
            else:
                em.description = description.text

        return em

    @commands.command(
        description="Fetch info about a Roblox profile", usage="[username]"
    )
    @commands.cooldown(3, 15, commands.BucketType.user)
    async def roblox(self, ctx, *, username):
        # Web scrape to get the rest of the info in one request instead of 4
        async with ctx.typing():
            try:
                em = await self.get_roblox_profile(username)
            except RuntimeError as e:
                return await ctx.send(str(e))

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
