from discord.ext import commands
import discord
from datetime import datetime as d

from string import Formatter

from cogs.utils import wait_for_deletion

def strfdelta(tdelta, fmt):
    """
    Similar to strftime from datetime.datetime
    Returns formatted string of a timedelta
    """
    f = Formatter()
    d = {}
    l = {'D': 86400, 'H': 3600, 'M': 60, 'S': 1}
    k = map( lambda x: x[1], list(f.parse(fmt)))
    rem = int(tdelta.total_seconds())

    for i in ('D', 'H', 'M', 'S'):
        if i in k and i in l.keys():
            d[i], rem = divmod(rem, l[i])

    return f.format(fmt, **d)

class Meta(commands.Cog, name = ":gear: Meta"):
    
    def __init__(self, bot):
        self.bot = bot
        self.log = self.bot.log

    
    @commands.group(
        name = "help",
        description = "You're looking at it!",
        aliases = ['commands', 'command', 'info', 'h'],
        usage = "[command]",
        invoke_without_command = True
    )
    async def help_command(self, ctx, commd = "all"):
        def check(ms):
            return ms.channel == ctx.author.dm_channel and ms.author == ctx.author and ms.message == ctx.message and str(ms.emoji) == '❌'

        # Create an embed that will be filled in with information
        # depending on user input 
        em = discord.Embed(
            title = f"Help For {self.bot.user.name}",
            description = f"{self.bot.description}\n\n**Prefixes:** {self.bot.prefixes}\
                \nFor **more info** on a **specific command**, use: **`{self.bot.defaultPrefix}help [command]`‍**\n‍",
            color = 0x15DFEA,
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

                commands_list = ""
                for comm in cog_commands:
                    if comm.hidden == False:
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
                if command.hidden == True:
                    return await ctx.send("That command is hidden!")

                if len(command.aliases) != 0:
                    self.aliases_section = f"Aliases: {', '.join(command.aliases)}"
                else:
                    self.aliases_section = ""

                em.description = f"**{command.name.capitalize()}**\n\n{command.description}\n\n\
                    Format: `@{self.bot.user.name}#{self.bot.user.discriminator} {command.name} {command.usage if command.usage is not None else ''}`\
                    \n\n{self.aliases_section}"


            else:
                return await ctx.send("Invalid command specified.\nUse `help` to view list of all commands.")


        bot_message = await ctx.send(embed = em)

        self.bot.loop.create_task(
            wait_for_deletion(bot_message, user_ids=(ctx.author.id,), client=self.bot)
        )
    
    @help_command.command(
        name = "admin",
        description = "Displays all admin commands",
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
            color = 0x15DFEA,
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

                em.description = f"**{command.cog_name} - {command.name.capitalize()}**\nName: {command.name}\nDescription: {command.description}\n\
                    Format: `@{self.bot.user.name}#{self.bot.user.discriminator} {command.name} {command.usage if command.usage is not None else ''}`\
                    \n{self.aliases_section}\nHidden: `{command.hidden}`\nChecks: `{', '.join(checks) if len(checks) != 0 else 'None'}`\nEnabled: `{command.enabled}`"


            else:
                return await ctx.send("Invalid command specified.\nUse `help` to view list of all commands.")


        bot_message = await ctx.send(embed = em)

        self.bot.loop.create_task(
            wait_for_deletion(bot_message, user_ids=(ctx.author.id,), client=self.bot)
        )



    @commands.command(
        name = "ping",
        description = "Ping command; replies with 'Pong!'",
        aliases = ['p']
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

        return
    
    @commands.command(
        name = "invite",
        description = "Invite me to your server"
    )
    async def invite_command(self, ctx):
        self.log.info(f"{str(ctx.author)} used the invite command")
        await ctx.send("Invite:\nhttps://discordapp.com/api/oauth2/authorize?client_id=639234650782564362&permissions=0&scope=bot")


    @commands.command(
        name = "reload",
        description = "Reload an extension",
        usage = "[cog]",
        hidden = True
    )
    @commands.is_owner()
    async def reload_cog_command(self, ctx, cog = "all"):
        if cog == "all":
            for ext in self.bot.cogsToLoad:
                await ctx.send(f"Reloading `{ext}`")
                self.bot.reload_extension(ext)
            return await ctx.send("All cogs successfully reloaded.")
        
        try:
            self.bot.reload_extension(f"cogs.{cog.lower()}")
            await ctx.send(f"Extension `cogs.{cog.lower()}` successfully reloaded.")
            self.log.info(f"Extension 'cogs.{cog.lower()}' successfully reloaded.")
        except commands.ExtensionNotFound as error:
            await ctx.send(f":warning: Extension not found.\n```{error}```")
            self.log.warning(f"Extension 'cogs.{cog.lower()}' not found.")
            print(error)
        except commands.ExtensionFailed as error:
            await ctx.send(f":warning: Extension failed.\n```{error}```")
            self.log.warning(f"Extension 'cogs.{cog.lower()}' failed.")
            print(error)
        except commands.ExtensionNotLoaded as error:
            await ctx.send(f":warning: Extension not loaded.\n```{error}```")
            self.log.warning(f"Extension 'cogs.{cog.lower()}' not loaded.")
            print(error)


    @commands.command(
        name = "logout",
        description = "Logs out and shuts down bot",
        hidden = True
    )
    @commands.is_owner()
    async def logout_command(self, ctx):
        self.log.info("Logging out of Discord.")

        await ctx.send("Logging out :wave:")

        await self.bot.logout()

        import sys
        sys.exit()


def setup(bot):
    bot.add_cog(Meta(bot))
