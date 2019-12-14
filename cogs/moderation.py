from discord.ext import commands
import discord


class Moderation(commands.Cog, name = ":police_car: Moderation"):
    
    def __init__(self, bot):
        self.bot = bot
        self.log = self.bot.log

    
    @commands.command(
        name = "purge",
        description = "Purge messages in a channel.",
        aliases = ["cleanup"],
        usage = "[amount]"
    )
    @commands.has_permissions(manage_messages = True)
    @commands.bot_has_permissions(manage_messages = True)
    async def purge_command(self, ctx, amount = None):
        def is_not_ctx(msg):
            return msg.id != ctx.message.id


        if not amount:
            deleted = await ctx.channel.purge(limit = None, check = is_not_ctx)
            return await ctx.channel.send(f"Deleted {len(deleted)} message(s)", delete_after = 5)
            
        deleted = await ctx.channel.purge(limit = int(amount), check = is_not_ctx)
        return await ctx.channel.send(f"Deleted {len(deleted)} message(s)", delete_after = 5)



def setup(bot):
    bot.add_cog(Moderation(bot))