from discord.ext import commands
import discord

from datetime import datetime as d
from string import Formatter
import traceback

from .utils.utils import wait_for_deletion


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
        self.more_info_category = "For **more info** on a "\
            f"**specific category**, use: **`{self.bot.defaultPrefix}help "\
            "[category]`‍**"
        self.more_info_cmd = "For **more info** on a **specific command**, "\
            f"use: **`{self.bot.defaultPrefix}help [command]`‍**"

    def hover_link(self, ctx, msg):
        return ("[`?`](https://www.discordapp.com/"
                f"channels/{ctx.guild.id}/{ctx.channel.id} "
                f""""{msg}") to get more info """)

    @commands.group(
        name="help",
        description="You're looking at it!",
        aliases=['commands', 'command', 'h'],
        usage="[command]",
        invoke_without_command=True
    )
    async def help_command(self, ctx, commd="all"):
        em = discord.Embed(
            title=f"Help for {self.bot.user.name}",
            description=(f"{self.bot.description}\n\n**Prefix:** "
                         f"{self.bot.prefixes}. "
                         f"Ex: `{self.bot.defaultPrefix}help`"
                         f"\n{self.more_info_category}\n"),
            # color = 0x15DFEA,
            color=0xFF95B0,
            timestamp=d.utcnow()
        )
        if ctx.guild:
            hover = self.hover_link(ctx, "More info here")
            em.description += (f"Hover over {hover} to get more info """
                               "(sorry mobile users).\n")
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
        if commd == "all":
            all_categories = ""
            for cog in self.bot.ordered_cogs:
                cog_docstring = self.bot.get_cog(cog).__doc__

                if cog == "Jishaku":
                    pass
                else:

                    all_categories += f"\n{cog}"
                    if cog_docstring and ctx.guild:
                        all_categories += self.hover_link(ctx, cog_docstring)
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
                    inline=True
            )

        else:
            all_commands_list = [command for command in self.bot.commands]

            cog_search_lowered = [c.lower() for c in cog_class_names]

            # cog_search_lowered = [cog[1:].lower() for cog in cogs]
            if commd.lower() in cog_search_lowered:
                cog_called = self.bot.get_cog(
                    cog_names[cog_search_lowered.index(commd.lower())])

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
                        help_text += (f"**`{self.bot.defaultPrefix}"
                                      f"{command.name}{command_usage}`** - "
                                      f"{command.description}\n")

                        if len(command.aliases) > 0:
                            prefix_aliases = [f"`{self.bot.defaultPrefix}{a}`" for a in command.aliases]
                            help_text += f"Aliases : {', '.join(prefix_aliases)}\n"
                help_text += f"\n{self.more_info_cmd}"
                em.description = help_text

            elif commd.lower() in [command.name for command in self.bot.commands]:

                command = next((c for c in all_commands_list if c.name == commd.lower()), None) # Finds the command in the list based off the name
                if command.hidden == True:
                    return await ctx.send("That command is hidden!")

                em.description = f"**{command.name.capitalize()}**\n{command.description}\n\n\
                    Format: `{self.bot.defaultPrefix}{command.name}{' ' + command.usage if command.usage is not None else ''}`\n"
                if len(command.aliases) > 0:
                    prefix_aliases = [f"`{self.bot.defaultPrefix}{a}`" for a in command.aliases]
                    em.description += f"Aliases : {', '.join(prefix_aliases)}\n"


            else:
                return await ctx.send(f"Invalid category/command specified.\nUse `{self.bot.defaultPrefix}help` to view list of all categories and commands.")


        bot_message = await ctx.send(embed = em)

        self.bot.loop.create_task(
            wait_for_deletion(bot_message, user_ids=(ctx.author.id,), client=self.bot)
        )

    @help_command.command(
        name = "admin",
        description = "Displays all admin commands",
        aliases = ["a"],
        hidden = True
    )
    @commands.is_owner()
    async def help_admin_command(self, ctx, commd = "all"):
        # Create an embed that will be filled in with information
        # depending on user input
        em = discord.Embed(
            title = f"Admin Help For {self.bot.user.name}",
            description = f"{self.bot.description}\n\n**Prefixes:** {self.bot.prefixes}\
                \nFor **more info** on a **specific command**, use: **`{self.bot.defaultPrefix}help admin [command]`‍**\n‍",
            # color = 0x15DFEA,
            color = 0xFF95B0,
            timestamp = d.utcnow()
        )
        em.set_thumbnail(
            url = self.bot.user.avatar_url
        )
        em.set_footer(
            text = f"Requested by {ctx.message.author.name}#{ctx.message.author.discriminator}",
            icon_url = self.bot.user.avatar_url
        )


        cogs = [c for c in self.bot.cogs.keys()]

        # If the user didn't specify a command, the full help command is sent
        if commd == "all":
            for cog in cogs:
                cog_commands = self.bot.get_cog(cog).get_commands()

                hidden_counter = 0
                for comm in cog_commands:
                    if comm.hidden == True:
                        hidden_counter += 1
                if hidden_counter == 0:
                    pass
                else:
                    commands_list = ""
                    for comm in cog_commands:
                        if comm.hidden == True:
                            commands_list += f"**`{comm.name}`** - {comm.description}\n"

                    em.add_field(
                        name = cog,
                        value=commands_list + "‍",
                        inline = False
                    )

            dev = self.bot.get_user(224513210471022592)
            em.add_field(
                    name = ":information_source: Technical Info",
                    value= f"**Developed by** - {dev.mention}\n**Programming Language** - Python\n**Framework** - Discord.py Commands",
                    inline = False
            )

        else:
            all_commands_list = [command for command in self.bot.commands]

            if commd.lower() in [command.name for command in self.bot.commands]:

                command = next((c for c in all_commands_list if c.name == commd.lower()), None) # Finds the command in the list based off the name

                if len(command.aliases) != 0:
                    self.aliases_section = f"Aliases: {', '.join(command.aliases)}"
                else:
                    self.aliases_section = ""

                checks = [ch.__name__ for ch in command.checks]

                if len(checks) != 0:
                    joinedChecks = ', '.join(checks)
                else:
                    joinedChecks = "None"

                em.description = f"**{command.cog_name} - {command.name.capitalize()}**\nName: {command.name}\nDescription: {command.description}\n\
                    Format: `@{self.bot.user.name}#{self.bot.user.discriminator} {command.name} {command.usage if command.usage is not None else ''}`\
                    \n{self.aliases_section}\nHidden: `{command.hidden}`\nChecks: `{joinedChecks}`\nEnabled: `{command.enabled}`"


            else:
                return await ctx.send("Invalid command specified.\nUse `help` to view list of all commands.")


        bot_message = await ctx.send(embed = em)

        self.bot.loop.create_task(
            wait_for_deletion(bot_message, user_ids=(ctx.author.id,), client=self.bot)
        )


    @commands.command(
        name = "stats",
        description = "Display statistics about the bot",
        aliases = ["statistics"]
    )
    async def stats(self, ctx):
        em = discord.Embed(
            title = "Robot Clam Statistics",
            color = 0xFF95B0,
            timestamp = d.utcnow()
        )
        em.set_thumbnail(
            url = self.bot.user.avatar_url
        )
        em.set_footer(
            text = f"Requested by {ctx.message.author.name}#{ctx.message.author.discriminator}",
            icon_url = self.bot.user.avatar_url
        )
        dev = self.bot.get_user(224513210471022592)
        em.add_field(name = ":gear: Developer", value = dev.mention)
        em.add_field(name = ":adult: User Count", value = len(self.bot.users))
        em.add_field(name = ":family: Server Count", value = len(self.bot.guilds))
        em.add_field(name = ":speech_balloon: Channel Count", value = len(list(self.bot.get_all_channels())))
        now = d.now()
        startupt = self.bot.startup_time
        up = now-startupt
        em.add_field(name = "<:online:649270802088460299> Uptime", value = strfdelta(up, '`{D}D {H}H {M}M {S}S`'))

        await ctx.send(embed = em)


    @commands.command(
        name = "ping",
        description = "Ping command; replies with 'Pong!'"
    )
    async def ping_command(self, ctx):

        start = d.timestamp(d.now())

        msg = await ctx.send(content = "Pinging")

        await msg.edit(content = f"Pong!\nOne message round-trip took {(d.timestamp( d.now()) - start ) * 1000}ms.")


    @commands.command(
        name = "uptime",
        description = "Uptime command; replies with the uptime",
        aliases = ['up']
    )
    async def uptime(self, ctx):
        now = d.now()
        startupt = self.bot.startup_time
        up = now-startupt

        msg = f"<:online:649270802088460299> I have been **online** for {strfdelta(up, '`{D}D {H}H {M}M {S}S`')}"
        # Attach :02 to a time (Ex: {D:02}) to add the second 0

        await ctx.send(msg)

    @commands.command(
        name = "invite",
        description = "Invite me to your server"
    )
    async def invite_command(self, ctx):
        self.log.info(f"{str(ctx.author)} used the invite command")
        await ctx.send("Invite:\nhttps://discordapp.com/api/oauth2/authorize?client_id=639234650782564362&permissions=470150358&scope=bot")


    @commands.command(
        name = "reload",
        description = "Reload an extension",
        aliases = ['load'],
        usage = "[cog]",
        hidden = True
    )
    @commands.is_owner()
    async def _reload(self, ctx, cog = "all"):
        if cog == "all":
            msg = ""
            for ext in self.bot.cogsToLoad:
                try:
                    self.bot.reload_extension(ext)
                    msg += f"**:repeat: Reloaded** `{ext}`\n\n"
                    self.log.info(f"Extension '{cog.lower()}' successfully reloaded.")
                except Exception as e:
                    traceback_data = ''.join(traceback.format_exception(type(e), e, e.__traceback__, 1))
                    msg += f"**:warning: Extension `{ext}` not loaded.**\n```py\n{traceback_data}```\n\n"
                    self.log.warning(f"Extension 'cogs.{cog.lower()}' not loaded.\n{traceback_data}")
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
        name = "logout",
        description = "Logs out and shuts down bot",
        hidden = True
    )
    @commands.is_owner()
    async def logout_command(self, ctx):
        self.log.info("Logging out of Discord.")

        await self.bot.session.close()

        await ctx.send("Logging out :wave:")

        await self.bot.logout()

        import sys
        sys.exit()


def setup(bot):
    bot.add_cog(Meta(bot))
