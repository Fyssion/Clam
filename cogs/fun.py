from discord.ext import commands
import discord
from datetime import datetime as d
import math
from cogs.utils import thesaurize


class Fun(commands.Cog, name = ":tada: Fun"):
    
    def __init__(self, bot):
        self.bot = bot
        self.log = self.bot.log

    @commands.command(
        name = "birthday",
        description = "Sends specified user a bday message straight to their DMs",
        aliases = ["bday"],
        usage = "[mentioned user] [IRL Name ('None' to mention them)] [age]"
    )
    async def birthday_command(self, ctx, user = None, name = None, age = None):
        if user is None or name is None or age is None:
            return await ctx.send("Please enter in the required values.\nEx: `r.birthday [mentioned user] [IRL Name ('None' to mention them)] [age]`")

        ageToGrowOn = str(int(age) + 1)

        recipientID = ctx.message.mentions[0].id
        recipient = self.bot.get_user(recipientID)

        if name.lower() == "none":
            mention = recipient.mention
        else:
            mention = None

        ordinal = lambda n: "%d%s" % (n,"tsnrhtdd"[(math.floor(n/10)%10!=1)*(n%10<4)*n%10::4])

        msg = f"Happy {ordinal(int(age))} Birthday, {mention or name}!\n"

        for i in range(int(ageToGrowOn)):
            msg += ":candle: "
        
        # OK OK OK I know this for-loop is super jank, but I'm too lazy to write good code for this
        cakes = ""
        isCupcake = False
        for i in range(math.ceil(int(ageToGrowOn) / 2)):
            if isCupcake == False:
                cakes += ":cake: "
                isCupcake = True
            else:
                cakes += ":cupcake: "
                isCupcake = False
    
        await recipient.send(msg)
        await recipient.send(cakes)
        await recipient.send(f"`From: {ctx.author.name}#{ctx.author.discriminator}`")

        await ctx.send(f"Sent birthday message to `{recipient.name}#{recipient.discriminator}`")
    @commands.command(
        name = "downvote",
        description = "Downvotes previous message or specified message",
        usage = "[optional message id]"
    )
    async def downvote_commmand(self, ctx, *args):

        # latest = max((x for x in self.bot.cached_messages if x.id < ctx.author.id and x.channel==ctx.channel.id), key=lambda x:x.id)

        # latest = await ctx.channel.history(limit=1).flatten()
        # latest = "".join(latest)

        # latest = ctx.channel.last_message_id
        # latest = self.bot.get_message(latest_id)

        # latest = ctx.channel.history(limit = 100).get(id = )

        # async for msg in ctx.channel.history(limit=2):
        #     latest = msg

        

        # await ctx.send(f"Message: \n> {latest.content}\nID: {latest.id}")
                
        if len(args) < 1:

            latest = await ctx.channel.history(limit = 100, before = ctx.message).get()

            base = self.bot.get_guild(454469821376102410)

            emoji = await base.fetch_emoji(644308837897207811)

            await latest.add_reaction(emoji)
            
            self.log.info(f"{str(ctx.author)} used the downvote command on previous message")
            em = discord.Embed(
                title = "I just downvoted your message.\nFAQ",
                timestamp = d.utcnow()
                )
            em.add_field(
                name = "What does this mean?",
                value = "The amount of points on your message has decreased by one.",
                inline = False
            )
            em.add_field(
                name = "Why did you do this?",
                value = "There are several reasons I may deem a message to be unworthy of positive or neutral points. \
                    These include, but are not limited to:\n• Rudeness towards other users,\n• \
                        Spreading incorrect information,\n• Sarcasm not correctly flagged with a `/s`.",
                inline = False
            )
            em.add_field(
                name = "Am I banned from the Discord?",
                value = "No - not yet. But you should refrain from writing messages like this in the future. \
                    Otherwise I will be forced to issue an additional downvote, which may put your messaging privileges in jeopardy.",
                inline = False
            )
            em.add_field(
                name = "I don't believe my message deserved a downvote. Can you un-downvote it?",
                value = "Sure, mistakes happen. But only in exceedingly rare circumstances will I undo a downvote. \
                    If you would like to issue an appeal, shoot me a direct message explaining what I got wrong. \
                        I tend to respond to Discord DMs within several minutes. Do note, however, that \
                            over 99.9% of downvote appeals are rejected, and yours is likely no exception.",
                inline = False
            )
            em.add_field(
                name = "How can I prevent this from happening in the future?",
                value = "Accept the downvote and move on. But learn from this mistake: your behavior will not be \
                    tolerated on Discordapp.com. I will continue to issue downvotes until you improve your conduct. \
                        Remember: Discord is privilege, not a right.\n\n\
                            [What's this?](https://www.reddit.com/r/copypasta/comments/dfcuzs/i_just_downvoted_your_comment/)",
                inline = False
            )
            em.set_footer(
                    text = f"Requested by {ctx.author.name}#{ctx.author.discriminator}",
                    icon_url = self.bot.user.avatar_url
                    )

            await ctx.send(embed = em)

        else:
            self.log.info(f"{str(ctx.author)} used the downvote command on custom message")

            try:
                 await ctx.send("This feature is in development.\n~~:warning: Message not found! Please use a vaild message ID.~~") # get message
            except:
                await ctx.send("This feature is in development.\n~~:warning: Message not found! Please use a vaild message ID.~~")
        
    
    # @commands.command(
    #     name = "thesaurize",
    #     description = "Thesaurize any sentence",
    #     usage = "[sentence]",
    #     aliases = ["thethis", "tt"]
    # )
    # async def thesaurize_command(self, ctx, *, sentence = None):
    #     if not sentence:
    #         return await ctx.send("Please include a sentence.")
    #     await ctx.send(await thesaurize(sentence))
            



def setup(bot):
    bot.add_cog(Fun(bot))