from discord.ext import commands
import discord
from datetime import datetime as d

from string import Formatter

def strfdelta(tdelta, fmt):
    f = Formatter()
    d = {}
    l = {'D': 86400, 'H': 3600, 'M': 60, 'S': 1}
    k = map( lambda x: x[1], list(f.parse(fmt)))
    rem = int(tdelta.total_seconds())

    for i in ('D', 'H', 'M', 'S'):
        if i in k and i in l.keys():
            d[i], rem = divmod(rem, l[i])

    return f.format(fmt, **d)

class Meta(commands.Cog):
    
    def __init__(self, bot):
        self.bot = bot
        self.log = self.bot.log

    @commands.command(
        name = "ping",
        description = "Ping command; replies with 'Pong!'",
        aliases = ['p']
    )
    async def ping_command(self, ctx):
        
        start = d.timestamp(d.now())

        msg = await ctx.send(content = "Pinging")

        await msg.edit(content = f"Pong!\nOne message round-trip took {(d.timestamp( d.now()) - start ) * 1000}ms.")

        return



    
    @commands.command(
        name = "help",
        description = "You're looking at it!",
        aliases = ['commands', 'command', 'info', 'h'],
        usage = "[category]"
    )
    async def help_command(self, ctx, commd="all"):

        em = discord.Embed(
            title = "Help",\
            description = f"Prefixes: {self.bot.prefixes}\nCommands are put in categories.\
                \nFor more info on a specific category, use: `@{self.bot.user.name}#{self.bot.user.discriminator} help [category]`‍\n‍\n‍",
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

        if commd == 'all':
            for cog in cogs:
                cog_commands = self.bot.get_cog(cog).get_commands()
                commands_list = ''
                for comm in cog_commands:
                    if comm.hidden == False:
                        commands_list += f"**{comm.name}** - *{comm.description}*\n"
                    
                em.add_field(
                    name = cog,
                    value=commands_list,
                    inline = False
                ).add_field(
               name='\u200b', value='\u200b', inline=False
                )

            dev = self.bot.get_user(224513210471022592)
            em.add_field(
                    name = "Other Info",
                    value= f"This bot was developed by {dev.mention}.\n*Programming Language* - Python\n*Framework* - Discord.py Commands",
                    inline = False
            )
            pass

        else:
            all_commands_list = [command for command in self.bot.commands]

            if commd.lower() in [command.name for command in self.bot.commands]:

                command = next((c for c in all_commands_list if c.name == commd.lower()), None) # Finds the command in the list based off the name

                em.description = f"**{command.name.capitalize()}**\n\n{command.description}\n\n\
                    Format: `@{self.bot.user.name}#{self.bot.user.discriminator} {command.name} {command.usage if command.usage is not None else ''}`\
                    \n\nAliases: {', '.join(command.aliases)}"


            else:
                await ctx.send("Invalid command specified.\nUse `help` to view list of all commands.")
                return

        await ctx.send(embed = em)
        return
    

    @commands.command(
        name = "uptime",
        description = "Uptime command; replies with the uptime.",
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
        description = "Invite me to your server."
    )
    async def invite_command(self, ctx):
        self.log.info(f"{str(ctx.author)} used the invite command")
        await ctx.send("Invite:\nhttps://discordapp.com/api/oauth2/authorize?client_id=639234650782564362&permissions=0&scope=bot")



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
