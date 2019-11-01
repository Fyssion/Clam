from discord.ext import commands
import discord
from datetime import datetime as d

class Meta(commands.Cog):
    
    def __init__(self, bot):
        self.bot = bot

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
        description = "The help command",
        aliases = ['commands', 'command', 'nator', 'info', 'h'],
        usage = "[category]"
    )
    async def help_command(self, ctx, cog="all"):

        em = discord.Embed(
            title = "Help",\
            description = f"Commands are put in categories.\nFor more info on a specific category, use: `@{self.bot.user.name}#{self.bot.user.discriminator}` help [category]",
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

        if cog == 'all':
            for cog in cogs:
                cog_commands = self.bot.get_cog(cog).get_commands()
                commands_list = ''
                for comm in cog_commands:
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
            lower_cogs = [c.lower() for c in cogs]
            if cog.lower() in lower_cogs:
                commands_list = self.bot.get_cog(cogs[lower_cogs.index(cog.lower())]).get_commands()
                help_text = ''

                for command in commands_list:
                    help_text += f"`{command.name}`\n" \
                        f"**{command.description}**\n"

                    help_text += f'Format: `@{self.bot.user.name}#{self.bot.user.discriminator}' \
                   f' {command.name} {command.usage if command.usage is not None else ""}`\n\n'
                em.description = help_text    
            else:
                await ctx.send("Invalid command category specified.\nUse `help` to view list of all command categories.")
                return
        await ctx.send(embed = em)
        return
    

    @commands.command(
        name = "uptime",
        description = "Uptime command; replies with the uptime.",
        aliases = ['u']
    )
    async def uptime(self, ctx):
        now = d.now()
        startupt = self.bot.startup_time

        msg = "I have been online for " + str(now - startupt) + ". (Hour:Min:Sec)"

        await ctx.send(msg)

        return
        


def setup(bot):
    bot.add_cog(Meta(bot))
