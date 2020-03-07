from discord.ext import commands, menus
import discord

from datetime import datetime as d
import re
import os
import ast

from .utils import fuzzy
from .utils.utils import SphinxObjectFileReader


def snowstamp(snowflake):
    timestamp = (int(snowflake) >> 22) + 1420070400000
    timestamp /= 1000

    return d.utcfromtimestamp(timestamp).strftime('%b %d, %Y at %#I:%M %p')


class SearchPages(menus.ListPageSource):
    def __init__(self, data):
        super().__init__(data, per_page=10)

    async def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page
        msg = f"Found **{len(entries)}** {'matches' if len(entries) > 1 else 'match'}! ```ini\n"
        for i, member in enumerate(entries, start=offset):
            if member.nick:
                nick = f"{member.nick} - "
            else:
                nick = ""
            msg += f"\n[{i+1}] {nick}{member.name}#{member.discriminator} ({member.id})"
        # msg += '\n'.join(f'{i+1}. {v}' for i, v in enumerate(entries, start=offset))
        msg += "\n```"
        return msg


class Tools(commands.Cog, name=":tools: Tools"):
    """Useful Discord tools."""

    def __init__(self, bot):
        self.bot = bot
        self.log = self.bot.log

    @commands.command(
        name="userinfo",
        description="Get information about a user",
        aliases=["memberinfo", "ui", "whois"],
        usage="[user]"
    )
    async def userinfo_command(self, ctx, *, user: discord.Member = None):
        user = user or ctx.author

        if user == ctx.author:
            self.log.info(f"{str(ctx.author)} successfully used the "
                          "userinfo command on themself")
        else:
            self.log.info(f"{str(ctx.author)} successfully used the "
                          f"userinfo command on '{user}'")

        member = ctx.guild.get_member(user.id)

        # def time_ago(user, dt):
        #     if dt is None:
        #         return ""
        #     return f"{snowstamp(user.id)}\n"
        #            f"({time.human_timedelta(dt, accuracy=3)})"

        desc = ""
        if user == self.bot.user:
            desc += "\n:wave:Hey, that's me!"
        if user.bot is True:
            desc += "\n:robot: This user is a bot."
        if user.id == ctx.guild.owner_id:
            desc += ("\n<:owner:649355683598303260> "
                     "This user is the server owner.")
        if user.id == self.bot.owner_id:
            desc += "\n:gear: This user owns this bot."
        if member.premium_since:
            formatted = member.premium_since.strftime('%b %d, %Y at %#I:%M %p')
            desc += ("\n<:boost:649644112034922516> "
                     "This user has been boosting this server since "
                     f"{formatted}.")

        author = str(user)
        if member.nick:
            author += f" ({member.nick})"
        author += f" - {str(user.id)}"
        em = discord.Embed(description=desc, timestamp=d.utcnow())
        if user.color.value:
            em.color = user.color
        em.set_thumbnail(url=user.avatar_url)
        em.set_author(name=author, icon_url=user.avatar_url)
        em.set_footer(text=f"Requested by {str(ctx.author)}",
                      icon_url=self.bot.user.avatar_url)
        em.add_field(name=":clock1: Account Created",
                     value=snowstamp(user.id),
                     inline=True)
        em.add_field(
            name="<:join:649722959958638643> Joined Server",
            value=member.joined_at.strftime('%b %d, %Y at %#I:%M %p'),
            inline=True)
        members = ctx.guild.members
        members.sort(key=lambda x: x.joined_at)
        position = members.index(member)
        em.add_field(name=":family: Join Position",
                     value=position + 1)
        if member.roles[1:]:
            roles = ""
            for role in member.roles[1:]:
                roles += f"{role.mention} "
            em.add_field(name="Roles", value=roles, inline=False)
        await ctx.send(embed=em)

    @commands.command(name="serverinfo",
                      description="Get information about the current server",
                      aliases=["guildinfo"])
    async def serverinfo_command(self, ctx):
        self.log.info(f"{str(ctx.author)} used the serverinfo command")

        if ctx.guild.unavailable == True:
            self.log.warning("Woah... {ctx.guild} is unavailable.")
            return await ctx.send("This guild is unavailable.\nWhat does this mean? I don't know either.\nMaybe Discord is having an outage...")

        desc = ""
        if ctx.guild.description:
            desc += f"\n{ctx.guild.description}\n"
        if ctx.guild.large == True:
            desc += "\n:information_source: This guild is considered large (over 250 members)."


        self.em = discord.Embed(
            description = desc,
            timestamp = d.utcnow()
        )

        self.em.set_thumbnail(
            url = ctx.guild.icon_url
            )
        if ctx.guild.banner_url:
            self.em.set_image(
                url = ctx.guild.banner_url
            )
        self.em.set_author(
            name = f"{ctx.guild.name} ({ctx.guild.id})",
            icon_url = ctx.guild.icon_url
            )
        self.em.set_footer(
                    text = f"Requested by {ctx.author.name}#{ctx.author.discriminator}",
                    icon_url = self.bot.user.avatar_url
                    )
        self.em.add_field(
            name = "<:owner:649355683598303260> Owner",
            value = ctx.guild.owner.mention,
            inline = True
        )
        self.em.add_field(
            name = ":clock1: Server Created",
            value = snowstamp(ctx.guild.id),
            inline = True
        )
        self.em.add_field(
            name = "<:boost:649644112034922516> Nitro Boosts",
            value = f"Tier {ctx.guild.premium_tier} with {ctx.guild.premium_subscription_count} boosts",
            inline = True
        )
        self.em.add_field(
            name = ":earth_americas: Region",
            value = str(ctx.guild.region).replace("-", " ").upper(),
            inline = True
        )
        self.em.add_field(
            name = ":family: Members",
            value = len(ctx.guild.members),
            inline = True
        )
        self.em.add_field(
            name = ":speech_balloon: Channels",
            value = f"<:text_channel:661798072384225307> {len(ctx.guild.text_channels)} • <:voice_channel:665577300552843294> {len(ctx.guild.voice_channels)}",
            inline = True
        )

        # roles = ""
        # for role in member.roles[1:]:
        #     roles += f"{role.mention} "
        # self.em.add_field(
        #     name = "Roles",
        #     value = roles,
        #     inline = False
        # )
        await ctx.send(embed = self.em)

    @commands.command(
        name = "snowstamp",
        description = "Get timestamp from a Discord snowflake",
        usage = "[snowflake]",
        hidden = True
    )
    async def snowstamp_command(self, ctx, snowflake = None):
        if snowflake == None:
            return await ctx.send("Please specify a snowflake to convert.")

        await ctx.send(snowstamp(snowflake))

    @commands.command(
        name = "embed",
        description = "Create a custom embed and send it to a specified channel.",
        aliases = ['em'],
        hidden = True
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

        await ctx.send("Check your DMs!", delete_after = 5)
        await ctx.author.send("**Create an embed:**\nWhat server would you like to send the embed to? Type `here` to send the embed where you called the command.")

        self.msg = await self.bot.wait_for("message", check = check)

        if self.msg == 'here':
            self.em_guild = ctx.guild
        else:
            await ctx.author.send("Custom servers not supported yet :(\nServer set to where you called the command.")
            self.em_guild = ctx.guild

        # Check to see if bot has permission to view perms

        await ctx.author.send(f"Server set to `{self.em_guild.name}`.\nWhat channel would you like to send to?")

        self.msg = await self.bot.wait_for("message", check = check)

        # Check for permission here

        # while hasPermissionToSend == False:

    @commands.group(description="Search for things in a server.",
                    aliases=["find"], invoke_without_command=True)
    async def search(self, ctx):
        dp = self.bot.guild_prefix(ctx.guild.id)
        await ctx.send("You can use:\n"
                       f"`{dp}search username [username]`\n"
                       f"`{dp}search nickname [nickname]`\n"
                       f"`{dp}search discriminator [discriminator]`")

    def compile_list(self, list):
        msg = f"Found **{len(list)}** {'matches' if len(list) > 1 else 'match'}! ```ini\n"
        for i, member in enumerate(list):
            if member.nick:
                nick = f"{member.nick} - "
            else:
                nick = ""
            msg += f"\n[{i+1}] {nick}{member.name}#{member.discriminator} ({member.id})"
        msg += "\n```"
        return msg

    @search.command(name="username",
                    description="Search server for a specified username",
                    usage="[username]", aliases=["user"])
    async def search_username(self, ctx, username: str):
        matches = []
        for member in ctx.guild.members:
            if username.lower() in member.name.lower():
                matches.append(member)
        if matches:
            pages = menus.MenuPages(source=SearchPages(matches), clear_reactions_after=True)
            return await pages.start(ctx)
            # return await ctx.send(self.compile_list(matches))
        await ctx.send("No matches found.")

    @search.command(name="nickname",
                    description="Search server for a specified nickname",
                    usage="[nickname]", aliases=["nick"])
    async def search_nickname(self, ctx, nickname: str):
        matches = []
        for member in ctx.guild.members:
            if member.nick:
                if nickname.lower() in member.nick.lower():
                    matches.append(member)
        if matches:
            pages = menus.MenuPages(source=SearchPages(matches), clear_reactions_after=True)
            return await pages.start(ctx)
        await ctx.send("No matches found.")

    @search.command(name="discriminator",
                    description="Search server for a specified descrininator",
                    usage="[discriminator]", aliases=["number"])
    async def search_discriminator(self, ctx, discriminator: int):
        matches = []
        for member in ctx.guild.members:
            if discriminator == int(member.discriminator):
                matches.append(member)
        if matches:
            pages = menus.MenuPages(source=SearchPages(matches), clear_reactions_after=True)
            return await pages.start(ctx)
        await ctx.send("No matches found.")

    def parse_object_inv(self, stream, url):
        # key: URL
        # n.b.: key doesn't have `discord` or `discord.ext.commands` namespaces
        result = {}

        # first line is version info
        inv_version = stream.readline().rstrip()

        if inv_version != '# Sphinx inventory version 2':
            raise RuntimeError('Invalid objects.inv file version.')

        # next line is "# Project: <name>"
        # then after that is "# Version: <version>"
        projname = stream.readline().rstrip()[11:]
        version = stream.readline().rstrip()[11:]

        # next line says if it's a zlib header
        line = stream.readline()
        if 'zlib' not in line:
            raise RuntimeError('Invalid objects.inv file, not z-lib compatible.')

        # This code mostly comes from the Sphinx repository.
        entry_regex = re.compile(r'(?x)(.+?)\s+(\S*:\S*)\s+(-?\d+)\s+(\S+)\s+(.*)')
        for line in stream.read_compressed_lines():
            match = entry_regex.match(line.rstrip())
            if not match:
                continue

            name, directive, prio, location, dispname = match.groups()
            domain, _, subdirective = directive.partition(':')
            if directive == 'py:module' and name in result:
                # From the Sphinx Repository:
                # due to a bug in 1.1 and below,
                # two inventory entries are created
                # for Python modules, and the first
                # one is correct
                continue

            # Most documentation pages have a label
            if directive == 'std:doc':
                subdirective = 'label'

            if location.endswith('$'):
                location = location[:-1] + name

            key = name if dispname == '-' else dispname
            prefix = f'{subdirective}:' if domain == 'std' else ''

            if projname == 'discord.py':
                key = key.replace('discord.ext.commands.', '').replace('discord.', '')

            result[f'{prefix}{key}'] = os.path.join(url, location)

        return result

    async def build_rtfm_lookup_table(self, page_types):
        cache = {}
        for key, page in page_types.items():
            sub = cache[key] = {}
            async with self.bot.session.get(page + '/objects.inv') as resp:
                if resp.status != 200:
                    raise RuntimeError('Cannot build rtfm lookup table, try again later.')

                stream = SphinxObjectFileReader(await resp.read())
                cache[key] = self.parse_object_inv(stream, page)

        self._rtfm_cache = cache

    async def do_rtfm(self, ctx, key, obj):
        page_types = {
            'latest': 'https://discordpy.readthedocs.io/en/latest',
            'latest-jp': 'https://discordpy.readthedocs.io/ja/latest',
            'python': 'https://docs.python.org/3',
            'python-jp': 'https://docs.python.org/ja/3',
        }

        if obj is None:
            await ctx.send(page_types[key])
            return

        if not hasattr(self, '_rtfm_cache'):
            await ctx.trigger_typing()
            # em = discord.Embed(colour = discord.Colour.blurple())
            # em.add_field(name = "\u200b", value = ":mag: `Searching the docs...`")
            # bot_msg = await ctx.send(embed = em)
            await self.build_rtfm_lookup_table(page_types)

        obj = re.sub(r'^(?:discord\.(?:ext\.)?)?(?:commands\.)?(.+)', r'\1', obj)

        if key.startswith('latest'):
            # point the abc.Messageable types properly:
            q = obj.lower()
            for name in dir(discord.abc.Messageable):
                if name[0] == '_':
                    continue
                if q == name:
                    obj = f'abc.Messageable.{name}'
                    break

        cache = list(self._rtfm_cache[key].items())
        def transform(tup):
            return tup[0]

        matches = fuzzy.finder(obj, cache, key=lambda t: t[0], lazy=False)[:7]

        em = discord.Embed(colour=discord.Colour.blurple())
        if len(matches) == 0:
            return await ctx.send('Could not find anything. Sorry.')
        em.add_field(name = f"`Results for '{obj}'`", value = '\n'.join(f'[`{key}`]({url})' for key, url in matches))
        em.set_footer(
            text = f"Requested by {ctx.author.name}#{ctx.author.discriminator}",
            icon_url = self.bot.user.avatar_url
        )
        # em.description = '\n'.join(f'[`{key}`]({url})' for key, url in matches)
        # await bot_msg.edit(embed = em)
        await ctx.send(embed=em)

    def transform_rtfm_language_key(self, ctx, prefix):
        if ctx.guild is not None:
            #                             日本語 category
            if ctx.channel.category_id == 490287576670928914:
                return prefix + '-jp'
            #                    d.py unofficial JP
            elif ctx.guild.id == 463986890190749698:
                return prefix + '-jp'
        return prefix

    @commands.group(
        aliases=['rtfm', 'rtfd'],
        invoke_without_command=True,
        description = "Gives you a documentation link for a discord.py entity."
        )
    async def docs(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a discord.py entity.
        Events, objects, and functions are all supported through a
        a cruddy fuzzy algorithm.
        """
        key = self.transform_rtfm_language_key(ctx, 'latest')
        await self.do_rtfm(ctx, key, obj)

    @docs.command(
        name='python',
        aliases=['py']
        )
    async def docs_python(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a Python entity."""
        key = self.transform_rtfm_language_key(ctx, 'python')
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


    @commands.command(
        name = "eval",
        description = "Evaluates python code.",
        usage = "[code]",
        hidden = True
        )
    @commands.is_owner()
    async def eval_fn(self, ctx, *, cmd):
        """Evaluates input.
        Input is interpreted as newline seperated statements.
        If the last statement is an expression, that is the return value.
        Usable globals:
        - `bot`: the bot instance
        - `discord`: the discord module
        - `commands`: the discord.ext.commands module
        - `ctx`: the invokation context
        - `__import__`: the builtin `__import__` function
        Such that `>eval 1 + 1` gives `2` as the result.
        The following invokation will cause the bot to send the text '9'
        to the channel of invokation and return '3' as the result of evaluating
        >eval ```
        a = 1 + 2
        b = a * 2
        await ctx.send(a + b)
        a
        ```
        """
        fn_name = "_eval_expr"

        cmd = cmd.strip("` ")

        # add a layer of indentation
        cmd = "\n".join(f"    {i}" for i in cmd.splitlines())

        # wrap in async def body
        body = f"async def {fn_name}():\n{cmd}"

        parsed = ast.parse(body)
        body = parsed.body[0].body

        self.insert_returns(body)

        env = {
            'bot': ctx.bot,
            'discord': discord,
            'commands': commands,
            'ctx': ctx,
            '__import__': __import__
        }
        exec(compile(parsed, filename="<ast>", mode="exec"), env)

        result = (await eval(f"{fn_name}()", env))
        await ctx.send(result)





def setup(bot):
    bot.add_cog(Tools(bot))