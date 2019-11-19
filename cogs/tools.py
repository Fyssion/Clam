from discord.ext import commands
import discord
from datetime import datetime as d


class Tools(commands.Cog):
    
    def __init__(self, bot):
        self.bot = bot
        self.log = self.bot.l

    @commands.command(
        name = "embed",
        description = "Create a custom embed and send it to a specified channel.",
        aliases = ['em']
    )
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

def setup(bot):
    bot.add_cog(Tools(bot))
