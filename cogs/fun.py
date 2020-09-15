from discord.ext import commands
import discord

from datetime import datetime as d
from datetime import timedelta
import math
import random
import functools
import importlib
import asyncio
import collections
import humanize
from random import choice

from .utils import colors, human_time
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
    )
    async def emoji(self, ctx, emoji):
        emoji = discord.utils.get(self.bot.emojis, name=emoji)

        if not emoji:
            return await ctx.send(
                f"{ctx.tick(False)} Sorry, I couldn't find that emoji."
            )
        if not emoji.is_usable():
            return await ctx.send(f"{ctx.tick(False)} Sorry, I can't use this emoji.")

        await ctx.send(str(emoji))

    @commands.command(
        description="Search for an emoji I have access to and react with it",
    )
    async def react(self, ctx, emoji, message=-1):
        emoji = discord.utils.get(self.bot.emojis, name=emoji)

        try:
            await ctx.message.delete()
            deleted = True

        except discord.Forbidden:
            deleted = False

        if not emoji:
            return await ctx.send(
                f"{ctx.tick(False)} Sorry, I couldn't find that emoji.",
                delete_after=5.0,
            )
        if not emoji.is_usable():
            return await ctx.send(
                f"{ctx.tick(False)} Sorry, I can't use this emoji.", delete_after=5.0
            )

        # Manual conversion so I can delete_after
        try:
            position = int(message)
        except ValueError:
            return await ctx.send(
                f"{ctx.tick(False)} You must provide a valid position or message ID. Ex: -2",
                delete_after=10.0,
            )

        if position == 0:
            return await ctx.send(
                f"{ctx.tick(False)} 0 is not a vaild position or message ID.",
                delete_after=10.0,
            )

        if position < 0:
            if position < -10:
                return await ctx.send(
                    f"{ctx.tick(False)} If you specify relative position, it must be no further back than 10.",
                    delete_after=10.0,
                )

            limit = abs(position)
            if not deleted:
                limit += 1

            history = await ctx.channel.history(limit=limit).flatten()
            message = history[limit - 1]

        else:
            try:
                message = await ctx.channel.fetch_message(position)
            except (discord.NotFound, discord.Forbidden):
                return await ctx.send(
                    f"{ctx.tick(False)} Message not found. Sorry.", delete_after=5.0
                )

        await message.add_reaction(emoji)

        bot_message = await ctx.send(
            f"{ctx.tick(True)} Added reaction. "
            "React and I will remove the reaction and this message."
        )

        def check(pd):
            return (
                pd.user_id == ctx.author.id
                and pd.channel_id == ctx.channel.id
                and pd.message_id == message.id
            )

        try:
            await self.bot.wait_for("raw_reaction_add", check=check, timeout=180.0)

        except asyncio.TimeoutError:
            pass

        await message.remove_reaction(emoji, ctx.me)
        await bot_message.delete()

    @commands.command(description="Flip a coin.")
    async def flipcoin(self, ctx):
        result = random.choice(["heads", "tails"])
        await ctx.send(f"You flipped a **{result}**.")

    @commands.group(
        description=("Roll a die or two"),
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
        description="Choose a random option", aliases=["choice"]
    )
    async def choose(self, ctx, *choices):
        await ctx.send(random.choice(choices))

    @commands.command(
        description="Similar to choose, except it's the best of a specified number",
        aliases=["bo"],
    )
    async def bestof(self, ctx, number: int, *choices):
        if len(choices) > 20:
            raise commands.BadArgument("You can have up to 20 choices.")

        Outcome = collections.namedtuple("Outcome", "choice occurrences")

        randomized = [random.choice(choices) for i in range(number)]
        outcomes = [Outcome(choice=c, occurrences=randomized.count(c)) for c in choices]
        outcomes.sort(key=lambda x: x.occurrences)
        outcomes.reverse()

        human_friendly = []
        for outcome in outcomes:
            if outcome.occurrences == 0:
                break
            percentage = int(outcome.occurrences / number * 100)
            human_friendly.append(
                f"`{percentage}%` **{outcome.choice}** ({human_time.plural(outcome.occurrences):occurrence})"
            )

        formatted = "\n".join(human_friendly)

        await ctx.send(f"Outcomes:\n{formatted}")

    async def wait_for_message(self, ctx, timeout=60):
        def check(ms):
            return ms.author == ctx.author and ms.channel == ctx.channel

        try:
            return await self.bot.wait_for("message", check=check, timeout=60)
        except asyncio.TimeoutError:
            return None

    @commands.command(description="See how fast you can type something")
    async def timeme(self, ctx):
        def check(ms):
            return ms.channel == ctx.channel and ms.author == ctx.author

        await ctx.send(
            "**Start typing!** The timer started when you sent your message."
        )
        start = ctx.message.created_at

        try:
            message = await self.bot.wait_for("message", check=check, timeout=180)
        except asyncio.TimeoutError:
            return await ctx.send("You took too long.")

        end = message.created_at

        start = start - timedelta(microseconds=start.microsecond % 10000)
        end = end - timedelta(microseconds=end.microsecond % 10000)

        time = end - start

        human_friendly = str(float(f"{time.seconds}.{time.microseconds}"))

        await ctx.send(
            f"You took **`{human_friendly} seconds`** to type **`{discord.utils.escape_mentions(message.content)}`**."
        )

    @commands.command(
        name="birthday",
        description="Sends a user a bday message straight to their DMs",
        aliases=["bday"],
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
        description="Generate a typing message for a name", usage="<name>"
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
