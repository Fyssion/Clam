from discord.ext import commands
import discord

from datetime import datetime as d
from string import Formatter
import traceback
import codecs
import os
import pathlib

from .utils.utils import wait_for_deletion
from .utils import db
from .utils.utils import hover_link


def strfdelta(tdelta, fmt):
    """
    Similar to strftime from datetime.datetime
    Returns formatted string of a timedelta
    """
    f = Formatter()
    d = {}
    lang = {'D': 86400, 'H': 3600, 'M': 60, 'S': 1}
    k = map(lambda x: x[1], list(f.parse(fmt)))
    rem = int(tdelta.total_seconds())

    for i in ('D', 'H', 'M', 'S'):
        if i in k and i in lang.keys():
            d[i], rem = divmod(rem, lang[i])

    return f.format(fmt, **d)


class Meta(commands.Cog, name=":gear: Meta"):
    """Everything to do with the bot itself."""

    def __init__(self, bot):
        self.bot = bot
        self.log = self.bot.log
        self.more_info_category = ("For **more info** on a **specific category**, "
                                   f"use: **`{self.bot.default_prefix}help [category]`‍**")
        self.more_info_cmd = ("For **more info** on a **specific command**, "
                              f"use: **`{self.bot.default_prefix}help [command]`‍**")

    @commands.group(
        name="help",
        description="You're looking at it!",
        aliases=['commands', 'command', 'h'],
        usage="[command]",
        invoke_without_command=True
    )
    async def help_command(self, ctx, search="all"):
        search = search.lower()
        em = discord.Embed(
            title=f"Help for {self.bot.user.name}",
            description=(f"{self.bot.description}\n\n"
                         f"**Prefix:** {self.bot.prefixes}. "
                         f"Ex: `{self.bot.default_prefix}help`\n"
                         f"{self.more_info_category}\n"),
            # color = 0x15DFEA,
            color=0xFF95B0,
            timestamp=d.utcnow()
        )
        # if ctx.guild:
        #     hover = hover_link(ctx, "More info here")
        #     em.description += (f"Hover over {hover} to get more info """
        #                        "(sorry mobile users).\n")
        em.set_thumbnail(
            url=self.bot.user.avatar_url
        )
        em.set_footer(
            text=f"Requested by {ctx.message.author.name}#"
                 f"{ctx.message.author.discriminator}",
            icon_url=self.bot.user.avatar_url
        )

        # cogs = [c for c in self.bot.cogs.values()]
        cog_names = [c for c in self.bot.cogs.keys()]
        cog_class_names = []
        for cog in cog_names:
            args = cog.split(" ")
            cog = args[1:]
            cog = "".join(cog)
            cog_class_names.append(cog)

        # If the user didn't specify a command, the full help command is sent
        if search == "all":
            all_categories = ""
            for cog in self.bot.ordered_cogs:
                cog_docstring = self.bot.get_cog(cog).__doc__

                if cog == "Jishaku":
                    pass
                else:

                    all_categories += f"\n{cog}"
                    # if cog_docstring and ctx.guild:
                    #     all_categories += hover_link(ctx, cog_docstring)
            em.add_field(
                name="Categories",
                value=all_categories,
                inline=True
            )

            dev = self.bot.get_user(224513210471022592)
            em.add_field(
                    name=":information_source: Technical Info",
                    value=(f"Developed by - {dev.mention}\n"
                           "Programming Language - Python\n"
                           "Framework - Discord.py Commands"),
                    inline=True)

        else:
            all_commands_list = [command for command in self.bot.commands]
            cog_search_lowered = [c.lower() for c in cog_class_names]

            if search in cog_search_lowered:
                cog_called = self.bot.get_cog(
                    cog_names[cog_search_lowered.index(search)])

                commands_list = cog_called.get_commands()

                help_text = f"**{cog_called.qualified_name}**\n"
                help_text += (cog_called.description + "\n\n"
                              if cog_called.description is not None
                              else "\n")

                for command in commands_list:
                    if not command.hidden:
                        command_usage = (" " + command.usage
                                         if command.usage is not None
                                         else '')
                        help_text += (f"**`{self.bot.default_prefix}"
                                      f"{command.name}{command_usage}`** - "
                                      f"{command.description}\n")

                        if len(command.aliases) > 0:
                            prefix_aliases = [f"`{self.bot.default_prefix}{a}`"
                                              for a in command.aliases]
                            help_text += (f"Aliases : "
                                          f"{', '.join(prefix_aliases)}\n")

                help_text += f"\n{self.more_info_cmd}"

                em.description = help_text

            elif search in [command.name for command in self.bot.commands]:
                command = next((c for c in all_commands_list if
                                c.name == search), None)

                if command.hidden is True:
                    return await ctx.send("That command is hidden!")

                em.description = (f"**{command.name.capitalize()}**\n{command.description}\n\n"
                                  f"Format: `{self.bot.default_prefix}{command.name}"
                                  f"{' ' + command.usage if command.usage is not None else ''}`\n")
                if len(command.aliases) > 0:
                    prefix_aliases = [f"`{self.bot.default_prefix}{a}`" for a in command.aliases]
                    em.description += f"Aliases : {', '.join(prefix_aliases)}\n"

            else:
                return await ctx.send("Invalid category/command specified.\n"
                                      f"Use `{self.bot.default_prefix}help` "
                                      "to view list of all categories and commands.")

        bot_message = await ctx.send(embed = em)

        self.bot.loop.create_task(
            wait_for_deletion(bot_message, user_ids=(ctx.author.id,), client=self.bot)
        )

    @help_command.command(
        name="admin",
        description="Displays all admin commands",
        aliases=["a"],
        hidden=True
    )
    @commands.is_owner()
    async def help_admin_command(self, ctx, commd="all"):
        em = discord.Embed(
            title=f"Admin Help For {self.bot.user.name}",
            description=f"{self.bot.description}\n\n**Prefixes:** {self.bot.prefixes}\
                        \nFor **more info** on a **specific command**, \
                        use: **`{self.bot.default_prefix}help admin [command]`‍**\n‍",
            color=0xFF95B0,
            timestamp=d.utcnow()
        )
        em.set_thumbnail(url=self.bot.user.avatar_url)
        em.set_footer(
            text=f"Requested by {str(ctx.author)}",
            icon_url=self.bot.user.avatar_url
        )

        cogs = [c for c in self.bot.cogs.keys()]
        if commd == "all":
            for cog in cogs:
                cog_commands = self.bot.get_cog(cog).get_commands()

                hidden_counter = 0
                for comm in cog_commands:
                    if comm.hidden == True:
                        hidden_counter += 1
                if hidden_counter != 0:
                    commands_list = ""
                    # cmd_list = [c if c.hidden for c in cog_commands]
                    for comm in cog_commands:
                        if comm.hidden == True:
                            commands_list += f"**`{comm.name}`** - {comm.description}\n"

                    em.add_field(name=cog, value=commands_list, inline=False)

            dev = self.bot.get_user(224513210471022592)
            em.add_field(
                name=":information_source: Technical Info",
                value=(f"**Developed by** - {dev.mention}\n"
                        "**Programming Language** - Python\n"
                        "**Framework** - Discord.py Commands"),
                inline=False
            )

        else:
            all_commands_list = [command for command in self.bot.commands]
            if commd in [command.name for command in self.bot.commands]:
                command = next((c for c in all_commands_list if c.name == commd.lower()), None)

                if len(command.aliases) != 0:
                    self.aliases_section = f"Aliases: {', '.join(command.aliases)}"
                else:
                    self.aliases_section = ""

                checks = [ch.__name__ for ch in command.checks]

                if len(checks) != 0:
                    joinedChecks = ', '.join(checks)
                else:
                    joinedChecks = "None"

                if command.usage:
                    usage = command.usage
                else:
                    usage = ""

                em.description = (f"**{command.cog_name} - {command.name.capitalize()}**\n"
                                  f"Name: {command.name}\n"
                                  f"Description: {command.description}\n"
                                  f"Format: `@{str(self.bot.user)} {command.name} {usage}`\n"
                                  f"{self.aliases_section}\n"
                                  f"Hidden: `{command.hidden}`\n"
                                  f"Checks: `{joinedChecks}`\n"
                                  f"Enabled: `{command.enabled}`")

            else:
                return await ctx.send("Invalid command specified.\nUse `help` to view list of all commands.")

        bot_message = await ctx.send(embed=em)

        self.bot.loop.create_task(
            wait_for_deletion(bot_message, user_ids=(ctx.author.id,), client=self.bot)
        )

    async def get_lines_of_code(self):
        total = 0
        file_amount = 0
        for path, subdirs, files in os.walk('.'):
            for name in files:
                if name.endswith('.py'):
                    file_amount += 1
                    with codecs.open('./' + str(pathlib.PurePath(path, name)), 'r', 'utf-8') as f:
                        for i, l in enumerate(f):
                            if l.strip().startswith('#') or len(l.strip()) is 0:  # skip commented lines.
                                pass
                            else:
                                total += 1
        return f'I am made of {total:,} lines of Python, spread across {file_amount:,} files!'

    @commands.command(
        name="stats",
        description="Display statistics about the bot",
        aliases=["statistics"]
    )
    async def stats(self, ctx):
        em = discord.Embed(
            title="Robot Clam Statistics",
            color=0xFF95B0,
            timestamp=d.utcnow()
        )
        em.set_thumbnail(url=self.bot.user.avatar_url)
        em.set_footer(
            text=f"Requested by {str(ctx.author)}",
            icon_url=self.bot.user.avatar_url
        )

        dev = self.bot.get_user(224513210471022592)
        up = d.now() - self.bot.startup_time
        em.add_field(name=":gear: Developer", value=dev.mention)
        em.add_field(name=":adult: User Count", value=len(self.bot.users))
        em.add_field(name=":family: Server Count", value=len(self.bot.guilds))
        em.add_field(name=":speech_balloon: Channel Count", value=len(list(self.bot.get_all_channels())))
        em.add_field(name="<:online:649270802088460299> Uptime", value=strfdelta(up, '`{D}D {H}H {M}M {S}S`'))
        em.add_field(name=":page_facing_up: Code", value=await self.get_lines_of_code())

        await ctx.send(embed=em)

    @commands.command(
        name="ping",
        description="Ping command; replies with 'Pong!'"
    )
    async def ping_command(self, ctx):
        start = d.timestamp(d.now())
        msg = await ctx.send("Pinging")

        ping = (d.timestamp(d.now()) - start) * 1000
        await msg.edit(content=f"Pong!\nOne message round-trip took {ping}ms.")

    @commands.command(
        name="uptime",
        description="Uptime command; replies with the uptime",
        aliases=['up']
    )
    async def uptime(self, ctx):
        up = d.now() - self.bot.startup_time
        up = strfdelta(up, '`{D}D {H}H {M}M {S}S`')

        msg = ("<:online:649270802088460299> "
               f"I have been **online** for {up}")
        # Attach :02 to a time (Ex: {D:02}) to add the second 0
        await ctx.send(msg)

    @commands.command(
        name="invite",
        description="Invite me to your server"
    )
    async def invite_command(self, ctx):
        invite = "https://discordapp.com/api/oauth2/authorize?client_id=639234650782564362&permissions=470150358&scope=bot"
        await ctx.send(f"Invite:\n{invite}")
        self.log.info(f"{str(ctx.author)} used the invite command")

    @commands.group(description="Prefix settings.",
                    invoke_without_subcommand=True)
    async def prefix(self, ctx):
        # TODO Make command
        # prefixes = db.fetch("prefixes.db")
        msg = "Prefixes:\n"
        for i, prefix in enumerate(self.bot.prefixes):
            msg += f"`{i+1}` {prefix}"
        await ctx.send(msg)

    @prefix.command(name="add", description="Add a prefix.", usage="[prefix]")
    async def _add_prefix(self, ctx, prefix):
        # TODO Make command
        pass

    @commands.command(
        name="reload",
        description="Reload an extension",
        aliases=['load'],
        usage="[cog]",
        hidden=True
    )
    @commands.is_owner()
    async def _reload(self, ctx, cog="all"):
        if cog == "all":
            msg = ""

            for ext in self.bot.cogsToLoad:
                try:
                    self.bot.reload_extension(ext)
                    msg += f"**:repeat: Reloaded** `{ext}`\n\n"
                    self.log.info(f"Extension '{cog.lower()}' successfully reloaded.")

                except Exception as e:
                    traceback_data = ''.join(traceback.format_exception(type(e), e, e.__traceback__, 1))
                    msg += (f"**:warning: Extension `{ext}` not loaded.**\n"
                            f"```py\n{traceback_data}```\n\n")
                    self.log.warning(f"Extension 'cogs.{cog.lower()}' not loaded.\n"
                                     f"{traceback_data}")
            return await ctx.send(msg)

        try:
            self.bot.reload_extension(cog.lower())
            await ctx.send(f"**:repeat: Reloaded** `{cog.lower()}`")
            self.log.info(f"Extension '{cog.lower()}' successfully reloaded.")
        except Exception as e:
            traceback_data = ''.join(traceback.format_exception(type(e), e, e.__traceback__, 1))
            await ctx.send(f"**:warning: Extension `{cog.lower()}` not loaded.**\n```py\n{traceback_data}```")
            self.log.warning(f"Extension 'cogs.{cog.lower()}' not loaded.\n{traceback_data}")

    @commands.command(
        name="logout",
        description="Logs out and shuts down bot",
        hidden=True
    )
    @commands.is_owner()
    async def logout_command(self, ctx):
        self.log.info("Logging out of Discord.")
        await ctx.send("Logging out :wave:")
        await self.bot.session.close()
        await self.bot.logout()


def setup(bot):
    bot.add_cog(Meta(bot))
