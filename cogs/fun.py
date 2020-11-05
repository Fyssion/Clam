from discord.ext import commands, menus
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
from cleverbot import async_ as cleverbot

from .utils import colors, human_time, fuzzy
from .utils.utils import is_int, quote
from .utils.menus import MenuPages
from .utils.human_time import plural

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


class GuildConverter(commands.Converter):
    async def convert(self, ctx, argument):
        try:
            int_argument = int(argument)
            guild = ctx.bot.get_guild(int_argument)
            if guild:
                return guild

        except ValueError:
            pass

        guild = discord.utils.get(ctx.bot.guilds, name=argument)
        if not guild:
            raise commands.BadArgument("No matching guilds.")

        return guild


class EmojiResultSource(menus.ListPageSource):
    def __init__(self, descriptions, emojis, title):
        self.descriptions = descriptions
        self.emojis = emojis
        self.title = title

        super().__init__(descriptions, per_page=1)

    def format_page(self, menu, entry):
        em = discord.Embed(title=self.title, color=colors.PRIMARY)

        em.description = entry

        em.set_footer(
            text=f"{plural(len(self.emojis)):result} | Page {menu.current_page + 1}/{self.get_max_pages()}"
        )

        return em


class Fun(commands.Cog):
    """
    Fun commands to mess around with.
    """

    def __init__(self, bot):
        self.bot = bot
        self.emoji = ":tada:"
        self.log = self.bot.log

        if not hasattr(bot, "cleverbot_convos"):
            # user_id: (convo, last_used_datetime)
            bot.cleverbot_convos = {}

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

    @commands.command(name="8ball", aliases=["eightball"])
    async def eightball(self, ctx, *, question):
        result = random.choice(
            [
                "Yes",
                "Certainly",
                "Of course",
                "Without a doubt",
                "No",
                "Not a chance",
                "Nope",
                "No way",
                "Maybe",
                "Quite possibly",
                "There is a chance",
                "It could go either way",
            ]
        )
        await quote(ctx.message, result, quote=question)

    @commands.group(aliases=["cleverbot"], invoke_without_command=True)
    @commands.cooldown(5, 10, commands.BucketType.user)
    async def ask(self, ctx, *, anything=None):
        """Ask the bot anything through the Cleverbot API"""
        async with ctx.typing():
            convo, last_used = self.bot.cleverbot_convos.get(
                ctx.author.id, (None, d.utcnow())
            )

            if last_used > d.utcnow() - timedelta(minutes=10):
                convo = None

            if not convo:
                convo = self.bot.cleverbot.conversation()
                self.bot.cleverbot_convos[ctx.author.id] = (convo, d.utcnow())

            if anything:
                reply = await convo.say()

            else:
                reply = await convo.say(anything)

            await quote(ctx.message, reply, quote=anything)

    @ask.command(name="reset")
    async def ask_reset(self, ctx):
        """Reset your current conversation"""
        if not self.bot.cleverbot_convos.get(ctx.author.id):
            return await ctx.send("You don't have a conversation.")

        self.bot.cleverbot_convos.pop(ctx.author.id)

        await ctx.send(ctx.tick(True, "Reset conversation"))

    @commands.command(aliases=["conversation"])
    async def convo(self, ctx, starting_phrase=None):
        """Start a conversation with Cleverbot.
        Use the `done` command when you are done.
        """

        convo = self.bot.cleverbot.conversation()

        async with ctx.typing():
            if starting_phrase:
                reply = await convo.say(starting_phrase)

            else:
                reply = await convo.say()
            await ctx.send(
                "Starting a Cleverbot conversation...\n\n"
                f"Use **`{ctx.guild_prefix}done`** to stop the conversation.\n"
                "The conversation also times out after 2 minutes if you do not respond.\n\n"
                f"**Cleverbot:**\n"
                f"{reply}"
            )

        def check(ms):
            return ms.author == ctx.author and ms.channel == ctx.channel

        while True:
            try:
                message = await self.bot.wait_for("message", check=check, timeout=120)

            except asyncio.TimeoutError:
                await ctx.send("You timed out. Ended conversation.")
                return

            message_ctx = await self.bot.get_context(message)

            invoked_with = message_ctx.invoked_with
            if invoked_with and invoked_with.lower().startswith("done"):
                break

            if message_ctx.valid:
                continue

            async with ctx.typing():
                reply = await convo.say(message.content)
                await quote(message, reply)

        await ctx.send("Ended conversation. Thanks for talking!")

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

    def format_emojis(self, emojis):
        descriptions = [""]
        current_column = 0

        for name, emoji in emojis:
            if current_column >= 15:
                to_add = f"\n{emoji} "
                current_column = 0

            else:
                to_add = f"{emoji} "

            if len(descriptions[-1]) + len(to_add) > 2048:
                descriptions.append("")
                current_column = 0
                to_add = f"{emoji} "

            descriptions[-1] += to_add
            current_column += 1

        return descriptions

    @commands.command()
    async def emojisearch(self, ctx, *, query):
        """Search Clam's emojis"""
        emojis = [(emoji.name, str(emoji)) for emoji in self.bot.emojis]

        def transform(tup):
            return tup[0]

        matches = fuzzy.finder(query, emojis, key=transform, lazy=False)

        if not matches:
            return await ctx.send("Could not find anything. Sorry.")

        descriptions = self.format_emojis(matches)

        menu = MenuPages(
            source=EmojiResultSource(descriptions, matches, f"Results for '{query}'"), clear_reactions_after=True
        )
        await menu.start(ctx)

    @commands.command()
    @commands.is_owner()
    async def emojis(self, ctx, *, guild: GuildConverter = None):
        """List all of Clam's emojis or emojis for a specific guild"""
        if guild:
            emojis = [(e.name, str(e)) for e in self.bot.emojis if e.guild == guild]
            title = f"Emojis in {guild}"

        else:
            emojis = [(e.name, str(e)) for e in self.bot.emojis]
            title = "All Emojis"

        if not emojis:
            return await ctx.send("No emojis found.")

        descriptions = self.format_emojis(emojis)

        menu = MenuPages(
            source=EmojiResultSource(descriptions, emojis, title), clear_reactions_after=True
        )
        await menu.start(ctx)

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

    @commands.command(description="Choose a random option", aliases=["choice"])
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
    @commands.is_owner()
    async def timeme(self, ctx):
        def check(ms):
            return ms.channel == ctx.channel and ms.author == ctx.author

        await self.bot.loop.create_task(
            ctx.send("**Start typing!** The timer started when you sent your message.")
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
