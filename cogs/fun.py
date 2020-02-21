from discord.ext import commands
import discord

from datetime import datetime as d
import math
import random
import functools
import importlib

from random import choice

from .utils import aioxkcd
# from .utils.utils import thesaurize

num2words1 = {1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five',
              6: 'six', 7: 'seven', 8: 'eight', 9: 'nine', 10: 'ten',
              11: 'eleven', 12: 'twelve', 13: 'thirteen', 14: 'fourteen',
              15: 'fifteen', 16: 'sixteen', 17: 'seventeen', 18: 'eighteen',
              19: 'nineteen'}
num2words2 = ['twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy',
              'eighty', 'ninety']


class Fun(commands.Cog, name=":tada: Fun"):
    """
    Fun commands to mess around with.
    """

    def __init__(self, bot):
        self.bot = bot
        self.log = self.bot.log

    def number(self, num):
        if 1 <= num <= 19:
            return num2words1[num]
        elif 20 <= num <= 99:
            tens, below_ten = divmod(num, 10)
            return num2words2[tens - 2] + '-' + num2words1[below_ten]
        else:
            return str(num)

    @commands.command(description="Flip a coin.")
    async def flipcoin(self, ctx):
        result = random.choice(['heads', 'tails'])
        await ctx.send(f"You flipped a **{result}**.")

    @commands.group(
        description=("Roll a die or two. "
                     "Also see `c.rolldice sides [# of sides]`"),
        usage="<# of dice>", aliases=['diceroll'],
        invoke_without_command=True)
    async def rolldice(self, ctx, dice: int = 1):
        if dice > 10:
            return await ctx.send(":warning: Too many dice. "
                                  "You can roll up to 10 dice.")
        rolls = []
        for i in range(dice):
            rolls.append(random.randrange(1, 6))
        if dice == 1:
            result = self.number(rolls[0])
            return await ctx.send(f":game_die: You rolled **{result}**.")

        word_rolls = [f"**{self.number(num)}**" for num in rolls]
        await ctx.send(f":game_die: You rolled {', '.join(word_rolls[:-1])} "
                       f"and **{word_rolls[-1]}** for a "
                       f"total of **{self.number(sum(rolls))}**.")

    @rolldice.command(name="sides",
                      description="Roll a dice with a specified # of sizes.",
                      aliases=['side'], usage="[# of sides] <# of dice>")
    async def rolldice_sides(self, ctx, sides: int = 6, dice: int = 1):
        if dice > 10:
            return await ctx.send(":warning: Too many dice. "
                                  "You can roll up to 10 dice.")
        if sides < 2:
            return await ctx.send(":warning: You must have "
                                  "more than two sides.")
        rolls = []
        for i in range(dice):
            rolls.append(random.randrange(1, sides))
        if dice == 1:
            result = self.number(rolls[0])
            return await ctx.send(f":game_die: You rolled **{result}**.")

        word_rolls = [f"**{self.number(num)}**" for num in rolls]
        await ctx.send(f":game_die: You rolled {', '.join(word_rolls[:-1])} "
                       f"and **{word_rolls[-1]}** for a "
                       f"total of **{self.number(sum(rolls))}**.")

    @commands.command(
        name="birthday",
        description="Sends a user a bday message straight to their DMs",
        aliases=["bday"],
        usage="[mentioned user] [IRL Name ('None' to mention them)] [age]"
    )
    async def birthday_command(self, ctx, user: discord.Member=None, name=None, age=None):
        if user is None or name is None or age is None:
            return await ctx.send("Please enter in the required values.\n"
                                  "Ex: `c.birthday [user] "
                                  "[IRL Name ('None' to mention them)] [age]`")

        ageToGrowOn = str(int(age) + 1)

        recipient = user

        if name.lower() == "none":
            mention = recipient.mention
        else:
            mention = None

        def get_ordinal():
            return lambda n: "%d%s" % (n, "tsnrhtdd"[(math.floor(n/10) % 10 !=
                                                      1) * (n % 10 < 4) *
                                                     n % 10::4])

        ordinal = get_ordinal()

        msg = f"Happy {ordinal(int(age))} Birthday, {mention or name}!\n"

        for i in range(int(ageToGrowOn)):
            msg += ":candle: "

        # OK OK OK I know this for-loop is super jank,
        # but I'm too lazy to write good code for this
        cakes = ""
        isCupcake = False
        for i in range(math.ceil(int(ageToGrowOn) / 2)):
            if isCupcake is False:
                cakes += ":cake: "
                isCupcake = True
            else:
                cakes += ":cupcake: "
                isCupcake = False

        await recipient.send(msg)
        await recipient.send(cakes)
        await recipient.send(f"`From: {ctx.author.name}#"
                             f"{ctx.author.discriminator}`")

        await ctx.send("Sent birthday message to "
                       f"`{recipient.name}#{recipient.discriminator}`")

    @commands.command(
        name="downvote",
        description="Downvotes previous message or specified message",
        usage="[optional message id]"
    )
    async def downvote_commmand(self, ctx, *args):

        # latest = max((x for x in self.bot.cached_messages
        #              if x.id < ctx.author.id and x.channel==ctx.channel.id),
        #             key=lambda x:x.id)

        # latest = await ctx.channel.history(limit=1).flatten()
        # latest = "".join(latest)

        # latest = ctx.channel.last_message_id
        # latest = self.bot.get_message(latest_id)

        # latest = ctx.channel.history(limit = 100).get(id = )

        # async for msg in ctx.channel.history(limit=2):
        #     latest = msg

        # await ctx.send(f"Message: \n> {latest.content}\nID: {latest.id}")

        if len(args) < 1:

            latest = await ctx.channel.history(limit=100,
                                               before=ctx.message).get()

            base = self.bot.get_guild(454469821376102410)

            emoji = await base.fetch_emoji(644308837897207811)

            await latest.add_reaction(emoji)

            self.log.info(f"{str(ctx.author)} used the downvote command "
                          "on previous message")
            em = discord.Embed(
                title="I just downvoted your message.\nFAQ",
                timestamp=d.utcnow()
                )
            em.add_field(
                name="What does this mean?",
                value=("The amount of points on your message has "
                       "decreased by one."),
                inline=False
            )
            em.add_field(
                name="Why did you do this?",
                value="There are several reasons I may deem a message to be \
                       unworthy of positive or neutral points. \
                       These include, but are not limited to:\n• \
                       Rudeness towards other users,\n• \
                       Spreading incorrect information,\n• \
                       Sarcasm not correctly flagged with a `/s`.",
                inline=False
            )
            em.add_field(
                name="Am I banned from the Discord?",
                value="No - not yet. But you should refrain from writing \
                       messages like this in the future. \
                       Otherwise I will be forced to issue an additional \
                       downvote, which may put your messaging \
                       privileges in jeopardy.",
                inline=False
            )
            em.add_field(
                name=("I don't believe my message deserved a downvote. "
                      "Can you un-downvote it?"),
                value="Sure, mistakes happen. But only in exceedingly rare \
                       circumstances will I undo a downvote. \
                       If you would like to issue an appeal, \
                       shoot me a direct message explaining what I got wrong. \
                       I tend to respond to Discord DMs within several \
                       minutes. Do note, however, that \
                       over 99.9% of downvote appeals are rejected, \
                       and yours is likely no exception.",
                inline=False
            )
            em.add_field(
                name="How can I prevent this from happening in the future?",
                value="Accept the downvote and move on. \
                         But learn from this mistake: your behavior \
                         will not be tolerated on Discordapp.com. \
                         I will continue to issue downvotes until you \
                         improve your conduct. Remember: Discord is \
                         a privilege, not a right.\n\n\
                         [What's this?](https://www.reddit.com/r/copypasta/comments/dfcuzs/i_just_downvoted_your_comment/)",
                inline=False
            )
            em.set_footer(
                    text=f"Requested by {str(ctx.author)}",
                    icon_url=self.bot.user.avatar_url
                    )

            await ctx.send(embed=em)

        else:
            self.log.info(f"{str(ctx.author)} used the downvote command "
                          "on custom message")

            try:
                await ctx.send("This feature is in development.\n~~:warning: "
                               "Message not found! "
                               "Please use a vaild message ID.~~")
            except Exception():
                await ctx.send("This feature is in development.\n~~:warning: "
                               "Message not found! "
                               "Please use a vaild message ID.~~")

    @commands.command(hidden=True)
    @commands.is_owner()
    async def reload_xkcd(self, ctx):
        importlib.reload(aioxkcd)
        await ctx.send("It has been done.")

    @commands.group(name="xkcd", description="Fetch an xdcd comic",
                    usage="<comic> (random if left blank)", invoke_without_command=True)
    async def _xkcd(self, ctx, number: int = None):
        if not number:
            return await self._random_xkcd(ctx)
        try:
            comic = await aioxkcd.get_comic(number)
        except aioxkcd.XkcdError:
            return await ctx.send("That comic does not exist!")
        em = discord.Embed(title=f"#{comic.number} - {comic.title}", description=comic.alt_text,
                           color=discord.Color.blurple(), url=comic.url)
        em.set_image(url=comic.image_url)
        em.set_footer(text=f"Comic published {comic.date_str}", icon_url=self.bot.user.avatar_url)
        await ctx.send(embed=em)

    @_xkcd.command(name="random", description="Fetch a random xdcd comic",
                   aliases=["r"])
    async def _random_xkcd(self, ctx):
        comic = await aioxkcd.get_random_comic()
        em = discord.Embed(title=f"#{comic.number} - {comic.title}", description=comic.alt_text,
                           color=discord.Color.blurple(), url=comic.url)
        em.set_image(url=comic.image_url)
        em.set_footer(text=f"Comic published {comic.date_str}", icon_url=self.bot.user.avatar_url)
        await ctx.send(embed=em)

    @_xkcd.command(name="latest", description="Fetch the latest xkcd comic")
    async def _latest_xkcd(self, ctx):
        comic = await aioxkcd.get_latest_comic()
        em = discord.Embed(title=f"#{comic.number} - {comic.title}", description=comic.alt_text,
                           color=discord.Color.blurple(), url=comic.url)
        em.set_image(url=comic.image_url)
        em.set_footer(text=f"Comic published {comic.date_str}", icon_url=self.bot.user.avatar_url)
        await ctx.send(embed=em)

    # async def do_thethaurize(self, sentence):
    #     words = sentence.split(" ")
    #     final_words = []
    #     for word in words:
    #         if not random.choice([True, False]):
    #             final_words.append(word)
    #             continue
    #         data = self.oxford.get_synonyms(word).json()
    #         synonyms = data['results'][0]['lexicalEntries'][0]['entries'][0]['senses'][0]['synonyms']
    #         new_word = synonyms[0]
    #         final_words.append(new_word)
    #     return " ".join(final_words)

    # @commands.command(
    #     description="Thesaurize any sentence CURRENTLY BROKEN",
    #     usage="[sentence]",
    #     aliases=["thethis", "tt"],
    #     hidden=True
    # )
    # async def thesaurize(self, ctx, *, sentence):
    #     await ctx.send(await self.do_thethaurize(sentence))


def setup(bot):
    bot.add_cog(Fun(bot))
