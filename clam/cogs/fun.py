import calendar
import datetime
import math
import random
import asyncio
import collections
from typing import Union

import discord
from dateutil import tz
from discord.ext import commands, menus

from .utils import colors, fuzzy
from .utils.formats import plural
from .utils.menus import MenuPages
from .utils.tabulate import tabulate
from .utils.utils import quote


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
        self.emoji = "\N{PARTY POPPER}"
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
        """Ask the all-knowing."""

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
    @commands.is_owner()
    async def ask(self, ctx, *, anything=None):
        """Ask the bot anything.

        This actually just makes a request to Cleverbot.
        """

        async with ctx.typing():
            convo, last_used = self.bot.cleverbot_convos.get(
                ctx.author.id, (None, datetime.datetime.utcnow())
            )

            if last_used > datetime.datetime.utcnow() - datetime.timedelta(minutes=10):
                convo = None

            if not convo:
                convo = self.bot.cleverbot.conversation()
                self.bot.cleverbot_convos[ctx.author.id] = (convo, datetime.datetime.utcnow())

            if anything:
                reply = await convo.say()

            else:
                reply = await convo.say(anything)

            await quote(ctx.message, reply, quote=anything)

    @ask.command(name="reset")
    async def ask_reset(self, ctx):
        """Resets your current conversation with Cleverbot."""

        if not self.bot.cleverbot_convos.get(ctx.author.id):
            return await ctx.send("You don't have a conversation.")

        self.bot.cleverbot_convos.pop(ctx.author.id)

        await ctx.send(ctx.tick(True, "Reset conversation"))

    @commands.command(aliases=["conversation"])
    @commands.is_owner()
    async def convo(self, ctx, starting_phrase=None):
        """Starts a conversation with Cleverbot.

        Use the `{prefix}done` command when you are done.
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

    @commands.command(aliases=["emote", "nitro"])
    async def emoji(self, ctx, emoji):
        """Shows one of my emojis."""

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
        """Searchs my emojis and shows the closest matches."""

        emojis = [(emoji.name, str(emoji)) for emoji in self.bot.emojis]

        def transform(tup):
            return tup[0]

        matches = fuzzy.finder(query, emojis, key=transform, lazy=False)

        if not matches:
            return await ctx.send("Could not find anything. Sorry.")

        descriptions = self.format_emojis(matches)

        menu = MenuPages(EmojiResultSource(descriptions, matches, f"Results for '{query}'"), ctx=ctx)
        await menu.start()

    @commands.command()
    @commands.is_owner()
    async def emojis(self, ctx, *, guild: GuildConverter = None):
        """Lists all my emojis or the emojis in a specific server."""

        if guild:
            emojis = [(e.name, str(e)) for e in self.bot.emojis if e.guild == guild]
            title = f"Emojis in {guild}"

        else:
            emojis = [(e.name, str(e)) for e in self.bot.emojis]
            title = "All Emojis"

        if not emojis:
            return await ctx.send("No emojis found.")

        descriptions = self.format_emojis(emojis)

        menu = MenuPages(EmojiResultSource(descriptions, emojis, title), ctx=ctx)
        await menu.start()

    @commands.command()
    async def react(self, ctx, emoji, message=-1):
        """Reacts with one of my emojis to a message."""

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

    @commands.command()
    async def flipcoin(self, ctx):
        """Flips a virtual coin and shows the result."""

        result = random.choice(["heads", "tails"])
        await ctx.send(f"You flipped **{result}**.")

    @commands.group(aliases=["diceroll", "rolldie"], invoke_without_command=True)
    async def rolldice(self, ctx, dice: int = 1, sides: int = 6):
        """Rolls a die or two.

        You can roll up to 10 dice with up to 99 sides.
        """
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

    @commands.command(aliases=["choice"])
    async def choose(self, ctx, *choices):
        """Makes a choice for you.

        You can have up to 20 choices.
        """
        if len(choices) > 20:
            raise commands.BadArgument(f"You can have up to 20 choices ({len(choices)}/20).")

        await ctx.send(random.choice(choices))

    @commands.command(
        aliases=["bo"],
    )
    async def bestof(self, ctx, number: int, *choices):
        """Similar to `{prefix}choose`, except it's the best of a specified number.

        The number can be up to 100, and you can have up to 20 choices.
        """
        if number > 100:
            raise commands.BadArgument("The number can only be up to 100.")
        if len(choices) > 20:
            raise commands.BadArgument(f"You can have up to 20 choices ({len(choices)}/20).")

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
                f"`{percentage}%` **{outcome.choice}** ({plural(outcome.occurrences):occurrence})"
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

    @commands.command()
    async def timeme(self, ctx):
        """Times your typing speed, sorta.

        It's a pretty dumb command.
        """

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

        start = start - datetime.timedelta(microseconds=start.microsecond % 10000)
        end = end - datetime.timedelta(microseconds=end.microsecond % 10000)

        time = end - start

        human_friendly = str(float(f"{time.seconds}.{time.microseconds}"))

        await ctx.send(
            f"You took **`{human_friendly} seconds`** to type **`{discord.utils.escape_mentions(message.content)}`**."
        )

    @commands.command(name="birthday", aliases=["bday"])
    async def birthday_command(self, ctx):
        """DMs a user a birthday message."""

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

    @commands.command()
    async def typing(self, ctx, *, user=None):
        """Sends a fake typing message."""

        if not user:
            name = ctx.author.display_name
        elif ctx.message.mentions:
            name = ctx.message.mentions[0].display_name
        else:
            name = user

        emoji = "<a:typing:702612001733738517>"

        name = discord.utils.escape_mentions(name)
        await ctx.send(f"{emoji} **{name}** is typing...")

    def generate_percentage_bar(self, filled_blocks):
        bar = "".join(
            (["\N{FULL BLOCK}"] * filled_blocks) + (["."] * (20 - filled_blocks))
        )
        return f"|{bar}|"

    @commands.command(aliases=["timebars"])
    async def timebar(self, ctx, utc_offset: int = 0):
        """Shows a progress bar for various portions of time."""

        def format_portion(when, percentage_bar, percent):
            return f"{when}: `|{percentage_bar}|` ({percent:.2f}%)"

        if utc_offset < -12 or utc_offset > 12:
            raise commands.BadArgument("Timezone must be within -12-12.")

        utc_now = datetime.datetime.utcnow().replace(tzinfo=tz.UTC)

        if utc_now.hour + utc_offset > 23:
            hour = utc_now.hour - (24 - utc_offset)
            day = utc_now.day + 1
            month = utc_now.month
            year = utc_now.year

            days_in_month = calendar.monthrange(utc_now.year, utc_now.month)[1]
            if day > days_in_month:
                month = utc_now.month + 1
                if month > 12:
                    month = 1
                    year += 1
                day = 1

        elif utc_now.hour + utc_offset < 0:
            hour = utc_now.hour + (24 + utc_offset)
            day = utc_now.day - 1
            month = utc_now.month
            year = utc_now.year

            if day < 1:
                month = utc_now.month - 1

                if month < 1:
                    month = 12
                    year -= 1
                day = calendar.monthrange(year, month)[1]

        else:
            hour = utc_now.hour + utc_offset
            day = utc_now.day
            month = utc_now.month
            year = utc_now.year

        utc_now = utc_now.replace(year=year, month=month, day=day, hour=hour)

        percentages = []

        # Day percentage bar
        current_day = utc_now.replace(hour=0, minute=0, second=0, microsecond=0)

        days_in_month = calendar.monthrange(utc_now.year, utc_now.month)[1]
        if current_day.day == days_in_month:
            month = current_day.month + 1
            year = current_day.year
            if month > 12:
                month = 1
                year = current_day.year + 1

            next_day = current_day.replace(year=year, month=month, day=1)
        else:
            next_day = current_day.replace(day=current_day.day + 1)

        current_seconds = (utc_now - current_day).total_seconds()
        total_seconds = (next_day - current_day).total_seconds()

        day_percentage = current_seconds / total_seconds
        filled_blocks = int(day_percentage * 20)
        percentage_bar = self.generate_percentage_bar(filled_blocks)
        day_percent = day_percentage * 100

        percentages.append(
            (utc_now.strftime("%A"), f"{percentage_bar} ({day_percent:.2f}%)")
        )

        # Month percentage bar
        current_month = utc_now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

        if current_month.month == 12:
            next_month = current_month.replace(year=current_month.year + 1, month=1)
        else:
            next_month = current_month.replace(month=current_month.month + 1)

        current_seconds = (utc_now - current_month).total_seconds()
        total_seconds = (next_month - current_month).total_seconds()

        month_percentage = current_seconds / total_seconds
        filled_blocks = int(month_percentage * 20)
        percentage_bar = self.generate_percentage_bar(filled_blocks)
        month_percent = month_percentage * 100

        percentages.append(
            (utc_now.strftime("%B"), f"{percentage_bar} ({month_percent:.2f}%)")
        )

        # Year percentage bar
        current_year = utc_now.replace(
            month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
        next_year = current_year.replace(year=current_year.year + 1)

        current_seconds = (utc_now - current_year).total_seconds()
        total_seconds = (next_year - current_year).total_seconds()

        year_percentage = current_seconds / total_seconds
        filled_blocks = int(year_percentage * 20)
        percentage_bar = self.generate_percentage_bar(filled_blocks)
        year_percent = year_percentage * 100

        percentages.append(
            (str(current_year.year), f"{percentage_bar} ({year_percent:.2f}%)")
        )

        codeblock = tabulate(percentages, codeblock=True, language="asciidoc")
        timezone = f"UTC{utc_offset}" if utc_offset != 0 else "UTC±0"
        await ctx.send(f"Time Progress Bars (for {timezone})\n{codeblock}")

    @commands.command(hidden=True)
    async def re_text(self, ctx, *, text: Union[discord.Message, str]):
        """dumb command pls ignore"""

        if isinstance(text, discord.Message):
            text = text.content
            if not text:
                return await ctx.send(" ".join(list("i can't reify that u dummy")))

        await ctx.send(" ".join(list(text)))


def setup(bot):
    bot.add_cog(Fun(bot))
