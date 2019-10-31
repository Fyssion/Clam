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
        usage = "cog"
    )
    async def help_command(self, ctx, cog="all"):

        em = discord.Embed(
            title = "Help",
            color = 0xffffff
        )
        em.set_footer(
            text = f"Requested by {ctx.message.author.name}",
            icon_url = self.bot.user.avatar_url
        )

        cogs = [c for c in self.bot.cogs.keys()]

        if cog == 'all':
            for cog in cogs:
                cog_commands = self.bot.get_cog(cog).get_commands()
                commands_list = ''
                for comm in cog_commands:
                    commands_list += f"**{comm.name}** - *{comm.description}_\n"
                    
                em.add_field(
                    name = cog,
                    value=commands_list,
                    inline = False
                )

            pass
        else:
            lower_cogs = [c.lower() for c in cogs]
            if cog.lower() in lower_cogs:
                command_list = self.bot.get_cog(cogs[lower_cogs.index(cog.lower())]).get_commands()
                help_text = ''

                for command in commands_list:
                    help_text += f"`{command.name}`\n" \
                        f"**{command.description}**\n"
                    help_text += "\n"

                    help_text += f'Format: `@{self.bot.user.name}#{self.bot.user.discriminator}' \
                   f' {command.name} {command.usage if command.usage is not None else ""}`\n\n\n\n'
                em.description = help_text    
            else:
                await ctx.send("Invalid command specified.\nUse `help` to view list of all commands.")
                return
        await ctx.send(embed = em)
        return

def setup(bot):
    bot.add_cog(Meta(bot))
