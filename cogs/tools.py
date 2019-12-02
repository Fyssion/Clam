from discord.ext import commands
import discord
from datetime import datetime as d
import re
from .utils import fuzzy
from .utils.utils import SphinxObjectFileReader
import os


def snowstamp(snowflake):
    timestamp = (int(snowflake) >> 22) + 1420070400000
    timestamp /= 1000

    return d.utcfromtimestamp(timestamp).strftime('%b %d, %Y at %#I:%M %p')


class Tools(commands.Cog, name = ":tools: Tools"):
    
    def __init__(self, bot):
        self.bot = bot
        self.log = self.bot.log


    @commands.command(
        name = "userinfo",
        description = "Get information about a user",
        aliases = ["memberinfo"],
        usage = "[user]"
    )
    async def userinfo_command(self, ctx, *, user = None):

        if user and len(ctx.message.mentions) == 0:
            user = ctx.guild.get_member_named(user)
            if not user:
                self.log.info(f"{str(ctx.author)} unsuccessfully used the userinfo command on a nonexistent user")
                return await ctx.send(":warning: Member not found! Search for members with their username or nickname.")
        elif user:
            user = ctx.message.mentions[0]

        user = user or ctx.author

        if user == ctx.author:
            self.log.info(f"{str(ctx.author)} successfully used the userinfo command on themself")
        else:
            self.log.info(f"{str(ctx.author)} successfully used the userinfo command on '{user}'")

        member = ctx.guild.get_member(user.id)

        desc = ""
        if user == self.bot.user:
            desc += "\n:wave:Hey, that's me!"
        if user.bot == True:
            desc += "\n:robot: This user is a bot."
        if user.id == ctx.guild.owner_id:
            desc += "\n<:owner:649355683598303260> This user is the server owner."
        if user.id == self.bot.owner_id:
            desc += "\n:gear: This user owns this bot."
        if member.premium_since:
            desc += f"\n<:boost:649644112034922516> This user has been boosting this server since {member.premium_since.strftime('%b %d, %Y at %#I:%M %p')}."

        author = str(user)
        if member.nick:
            author += f" ({member.nick})"
        author += f" - {str(user.id)}"

        self.em = discord.Embed(
            description = desc,
            timestamp = d.utcnow()
        )
        if user.color.value:
            self.em.color = user.color

        self.em.set_thumbnail(
            url = user.avatar_url
            )
        self.em.set_author(
            name = author,
            icon_url = user.avatar_url
            )
        self.em.set_footer(
                    text = f"Requested by {ctx.author.name}#{ctx.author.discriminator}",
                    icon_url = self.bot.user.avatar_url
                    )
        self.em.add_field(
            name = ":clock1: Account Created",
            value = snowstamp(user.id),
            inline = True
        )
        self.em.add_field(
            name = "<:join:649722959958638643> Joined Server",
            value = member.joined_at.strftime('%b %d, %Y at %#I:%M %p'),
            inline = True
        )
        roles = ""
        for role in member.roles[1:]:
            roles += f"{role.mention} "
        self.em.add_field(
            name = "Roles",
            value = roles,
            inline = False
        )
        await ctx.send(embed = self.em)


    @commands.command(
        name = "serverinfo",
        description = "Get information about the current server",
        aliases = ["guildinfo"]
    )
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

        matches = fuzzy.finder(obj, cache, key=lambda t: t[0], lazy=False)[:8]

        e = discord.Embed(colour=discord.Colour.blurple())
        if len(matches) == 0:
            return await ctx.send('Could not find anything. Sorry.')
        e.add_field(name = f"`Results for '{obj}'`", value = '\n'.join(f'[`{key}`]({url})' for key, url in matches))
        # e.description = '\n'.join(f'[`{key}`]({url})' for key, url in matches)
        await ctx.send(embed=e)

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
        hidden = True,
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
   

def setup(bot):
    bot.add_cog(Tools(bot))
