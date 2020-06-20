from discord.ext import commands
import discord

from datetime import datetime as d
import math
import random
import functools
import importlib
import asyncio

from random import choice

from .utils import colors
from .utils.utils import is_int

# from .utils.utils import thesaurize

num2words1 = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
}
num2words2 = [
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
]


class Fun(commands.Cog):
    """
    Fun commands to mess around with.
    """

    def __init__(self, bot):
        self.bot = bot
        self.emoji = ":tada:"
        self.log = self.bot.log

    def number(self, num):
        if 1 <= num <= 19:
            return num2words1[num]
        elif 20 <= num <= 99:
            tens, below_ten = divmod(num, 10)
            if below_ten == 0:
                return num2words2[tens - 2]
            return num2words2[tens - 2] + "-" + num2words1[below_ten]
        else:
            return str(num)

    @commands.command(
        description="Search for an emoji I have access to.",
        aliases=["emote", "nitro"],
        usage="[emoji name]",
    )
    async def emoji(self, ctx, query):
        emoji = discord.utils.get(self.bot.emojis, name=query)

        if not emoji:
            return await ctx.send("Sorry, I couldn't find that emoji.")
        if not emoji.is_usable():
            return await ctx.send("Sorry, I can't use this emoji.")

        await ctx.send(str(emoji))

    @commands.command(description="Flip a coin.")
    async def flipcoin(self, ctx):
        result = random.choice(["heads", "tails"])
        await ctx.send(f"You flipped a **{result}**.")

    @commands.group(
        description=("Roll a die or two. " "Also see `c.rolldice sides [# of sides]`"),
        usage="<# of dice> <# of sides>",
        aliases=["diceroll"],
        invoke_without_command=True,
    )
    async def rolldice(self, ctx, dice: int = 1, sides: int = 6):
        if dice > 10:
            raise commands.BadArgument("Too many dice. You can roll up to 10 dice.")
        if sides < 2:
            raise commands.BadArgument("You must have two or more sides.")
        if sides > 99:
            raise commands.BadArgument("You can have up to 99 sides.")
        rolls = []
        for i in range(dice):
            rolls.append(random.randrange(1, sides))
        if dice == 1:
            result = self.number(rolls[0])
            return await ctx.send(f":game_die: You rolled **{result}**.")

        word_rolls = [f"**{num}**" for num in rolls]
        await ctx.send(
            f":game_die: You rolled {', '.join(word_rolls[:-1])} "
            f"and **{word_rolls[-1]}** for a "
            f"total of **{self.number(sum(rolls))}**."
        )

    @commands.command(
        description="Choose a random option", usage="[choices]", aliases=["choice"]
    )
    async def choose(self, ctx, *choices):
        await ctx.send(random.choice(choices))

    @commands.command(
        description="Similar to choose, except it's a best of three",
        usage="[choices]",
        aliases=["bo3", "bestofthree"],
    )
    async def bestof3(self, ctx, *choices):
        outcomes = [random.choice(choices) for i in range(3)]
        occurrences = [outcomes.count(c) for c in choices]

        human_friendly = []
        for i in range(len(choices)):
            human_friendly.append(f"{choices[i]} ({occurrences[i]})")

        formatted = "\n".join(human_friendly)

        await ctx.send(f"Outcomes:\n{formatted}")

    async def wait_for_message(self, ctx, timeout=60):
        def check(ms):
            return ms.author == ctx.author and ms.channel == ctx.channel

        try:
            return await self.bot.wait_for("message", check=check, timeout=60)
        except asyncio.TimeoutError:
            return None

    @commands.command(
        name="birthday",
        description="Sends a user a bday message straight to their DMs",
        aliases=["bday"],
        usage="[mentioned user] [IRL Name ('None' to mention them)] [age]",
    )
    async def birthday_command(self, ctx):
        await ctx.send(
            "Who would you like to send the birthday message to? They must be in this server."
        )
        msg = await self.wait_for_message(ctx)
        recipient = await commands.MemberConverter().convert(ctx, msg.content)

        await ctx.send(f"How old is {recipient.name}?")
        msg = await self.wait_for_message(ctx)
        try:
            age = int(msg.content)
        except ValueError:
            raise commands.BadArgument(
                "Please specify a non-word number. Ex: 23 and not twenty-three"
            )
        age_to_grow_on = str(age + 1)

        if int(age_to_grow_on) > 500:
            raise commands.BadArgument("That age is too large. Must be less than 500.")

        await ctx.send(
            "Would you like to specify a name for the recipient? Type `no` to use their username."
        )
        msg = await self.wait_for_message(ctx)
        if msg.content.lower() == "no":
            name = recipient.name
        else:
            name = msg.content

        def get_ordinal():
            return lambda n: "%d%s" % (
                n,
                "tsnrhtdd"[(math.floor(n / 10) % 10 != 1) * (n % 10 < 4) * n % 10 :: 4],
            )

        ordinal = get_ordinal()

        msg = f"Happy {ordinal(int(age))} Birthday, {name}!\n"
        for i in range(int(age_to_grow_on)):
            msg += ":candle: "

        # OK OK OK I know this for-loop is super jank,
        # but I'm too lazy to write good code for this
        cakes = ""
        isCupcake = False
        for i in range(math.ceil(int(age_to_grow_on) / 2)):
            if isCupcake is False:
                cakes += ":cake: "
                isCupcake = True
            else:
                cakes += ":cupcake: "
                isCupcake = False

        if len(msg) > 2000 or len(cakes) > 2000:
            raise commands.BadArgument("Sorry, that message is too big to send.")

        await recipient.send(msg)
        await recipient.send(cakes)
        await recipient.send(f"`From: {ctx.author}`")

        await ctx.send(f"{ctx.tick(True)} Sent birthday message to `{recipient}`")

    @commands.command(
        description="Generate a typing message for a name", usage="[name]"
    )
    async def typing(self, ctx, *, member=None):
        if not member:
            name = ctx.author.display_name
        elif ctx.message.mentions:
            name = ctx.message.mentions[0].display_name
        else:
            name = member

        emoji = "<a:typing:702612001733738517>"

        name = discord.utils.escape_mentions(name)
        await ctx.send(f"{emoji} **{name}** is typing...")

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
