import asyncio
import datetime
import logging
import re

import asyncpg
import discord
from discord.ext import commands, tasks


from .utils import colors, db, humantime
from .utils.formats import plural


log = logging.getLogger("clam.highlight")


class HighlightWords(db.Table, table_name="highlight_words"):
    id = db.PrimaryKeyColumn()

    word = db.Column(db.String, index=True)
    user_id = db.Column(db.Integer(big=True), index=True)
    guild_id = db.Column(db.Integer(big=True), index=True)
    created_at = db.Column(db.Datetime, default="now() at time zone 'utc'")

    @classmethod
    def create_table(cls, *, exists_ok=True):
        statement = super().create_table(exists_ok=exists_ok)
        sql = "CREATE UNIQUE INDEX IF NOT EXISTS words_uniq_idx ON highlight_words (LOWER(word), user_id, guild_id);"
        return statement + "\n" + sql


class HighlightWord:
    @classmethod
    def from_record(cls, record):
        self = cls()

        self.id = record["id"]
        self.word = record["word"]
        self.user_id = record["user_id"]
        self.guild_id = record["guild_id"]
        self.created_at = record["created_at"]

        return self


class HighlightUserConfig(db.Table, table_name="highlight_user_config"):
    id = db.PrimaryKeyColumn()

    user_id = db.Column(db.Integer(big=True), index=True)
    blocked_users = db.Column(db.Array(db.Integer(big=True)))
    blocked_channels = db.Column(db.Array(db.Integer(big=True)))


class UserConfigHelper:
    @classmethod
    def from_record(cls, record):
        self = cls()

        self.id = record["id"]

        self.user_id = record["user_id"]
        self.blocked_users = record["blocked_users"]
        self.blocked_channels = record["blocked_channels"]

        return self


class AlreadyBlocked(commands.CommandError):
    pass


class NotBlocked(commands.CommandError):
    pass


class BlockConverter(commands.Converter):
    async def convert(self, ctx, arg):
        if not arg:
            return None

        try:
            user = await commands.UserConverter().convert(ctx, arg)
            return user

        except commands.BadArgument:
            pass

        channel = await commands.TextChannelConverter().convert(ctx, arg)
        return channel


class Highlights(db.Table):
    id = db.PrimaryKeyColumn()
    word = db.Column(db.String, index=True)
    guild_id = db.Column(db.Integer(big=True), index=True)
    channel_id = db.Column(db.Integer(big=True), index=True)
    author_id = db.Column(db.Integer(big=True), index=True)
    user_id = db.Column(db.Integer(big=True), index=True)
    invoked_at = db.Column(db.Datetime, index=True)


class Highlight(commands.Cog):
    """Get notified when your highlight words are said in chat.

    This is meant to emulate Skype's highlighted words feature.
    Concept was taken from Danny's Highlight bot, source code is original.
    """

    def __init__(self, bot):
        self.bot = bot
        self.emoji = "\N{LOWER LEFT CRAYON}"

        self.bot.loop.create_task(self.prepare_cache())

        self._batch_lock = asyncio.Lock(loop=bot.loop)
        self._highlight_data_batch = []
        self.bulk_insert_loop.add_exception_type(asyncpg.PostgresConnectionError)
        self.bulk_insert_loop.start()

        # channel: {member: task}
        self.typing_users = {}

    async def cog_check(self, ctx):
        return await commands.guild_only().predicate(ctx)

    async def prepare_cache(self):
        while True:
            if hasattr(self.bot, "pool") and self.bot.pool:
                break
            await asyncio.sleep(0.2)

        log.info("Preparing highlight cache...")

        # Cache a list of highlight words for lookup
        query = "SELECT word FROM highlight_words;"
        records = await self.bot.pool.fetch(query)

        self.bot.highlight_words = [r[0] for r in records]

        # Remove duplicates
        self.bot.highlight_words = list(dict.fromkeys(self.bot.highlight_words))

    async def delete_message_in(self, message, seconds=0.0):
        await asyncio.sleep(seconds)
        await message.delete()

    def delete_timer(self, message, seconds=0.0):
        self.bot.loop.create_task(self.delete_message_in(message, seconds))

    def format_message(self, message, *, highlight=None):
        time_formatting = "%H:%M "

        content = discord.utils.escape_markdown(message.content)

        if highlight:
            # Bold the word in the highlighted message

            words = []

            escaped = re.escape(highlight)
            hl = re.compile(r"(\W+)?((?:{0})[{0}]*)(?:(\W+)|((?:'|\")?s))?($)".format(escaped), re.I)
            for word in content.split(" "):
                words.append(hl.sub(r"\1**\2\4**\3\5", word))

            content = " ".join(words)

        sent = message.created_at.strftime(time_formatting)
        timezone = message.created_at.strftime("%Z")
        sent += timezone or "UTC"

        if not highlight and len(content) > 50:
            content = content[:50] + "..."

        else:
            content = content

        formatted = f"`{sent}` {message.author}: {content}"

        if highlight:
            formatted = f"> {formatted}"

        return formatted

    async def send_notification(self, message, word, record):
        highlight = HighlightWord.from_record(record)
        user = self.bot.get_user(highlight.user_id)

        log.debug(f"Recieved highlight with word {word} for user {highlight.user_id}")

        if not user:
            log.debug(f"User {highlight.user_id} not found in cache, aborting")
            return

        if user == message.author:
            log.debug(f"User {user} is the message author, aborting")
            return

        guild = message.guild
        channel = message.channel

        if user.id not in [m.id for m in channel.members]:
            log.debug(f"User {user} can't see #{channel}, aborting")
            return

        # Fetch user config to see if the author is blocked
        config = self.bot.get_cog("Config")

        if config:
            log.debug(f"Fetching user config for {user}")
            user_config = await config.get_config(user.id)

            if user_config:
                log.debug(f"User config found for {user}")

                if message.author.id in user_config.blocked_users:
                    log.debug(f"{message.author} is in {user}'s blocked list, aborting")
                    return

                if message.channel.id in user_config.blocked_channels:
                    log.debug(f"{message.channel} is in {user}'s blocked list, aborting")
                    return

        log.debug(f"Building notification for message {message.id}")

        log.debug(f"Getting list of previous messages for message {message.id}")
        # Get a list of messages that meet certain requirements
        matching_messages = [
            m
            for m in reversed(self.bot.cached_messages)
            if m.channel == channel
            and m.created_at <= message.created_at
            and m.id != message.id
        ]

        # Get the first three messages in that list
        previous_messages = matching_messages[:3]

        messages = []

        for msg in reversed(previous_messages):
            messages.append(self.format_message(msg))

        log.debug(f"Adding highlight message for message {message.id}")

        messages.append(self.format_message(message, highlight=word))

        # See if there are any messages after

        if self.is_typing(message.channel, user):
            return

        log.debug(f"Getting list of next messages for message {message.id}")
        # First, see if there are any messages after that have already been sent
        next_messages = []

        matching_messages = [
            m
            for m in reversed(self.bot.cached_messages)
            if m.channel == channel
            and m.created_at >= message.created_at
            and m.id != message.id
        ]

        # If there are messages already sent, append those and continue
        if len(matching_messages) > 2:
            log.debug(f"Found 2+ cached messages for message {message.id}")
            next_messages.append(matching_messages[0])
            next_messages.append(matching_messages[1])

        # Otherwise, add the cached message(s)
        # and/or wait for the remaining message(s)
        else:
            log.debug(
                f"Found {len(matching_messages)} cached messages for message {message.id}"
            )
            for msg in matching_messages:
                next_messages.append(msg)

            def check(ms):
                return (
                    ms.channel == channel
                    and ms.id != message.id
                    and ms.created_at > message.created_at
                )

            # Waiting for next messages
            for i in range(2 - len(matching_messages)):
                log.debug(
                    f"Waiting for message {i+1}/{2-len(matching_messages)} for message {message.id}"
                )
                try:
                    msg = await self.bot.wait_for("message", timeout=5.0, check=check)
                    log.debug(
                        f"Found message {i+1}/{2-len(matching_messages)} (ID: {msg.id}) for message {message.id}"
                    )
                    next_messages.append(msg)

                except asyncio.TimeoutError:
                    log.debug(
                        f"Timed out while waiting for message {i+1}/{2-len(matching_messages)} for message {message.id}"
                    )

        # Add the next messages to the formatted list
        for msg in next_messages:
            if msg.author == user:
                return

            messages.append(self.format_message(msg))

        if self.is_typing(message.channel, user):
            return

        em = discord.Embed(
            title=f"Highlight word: {word}",
            description="\n".join(messages),
            color=discord.Color.blurple(),
            timestamp=message.created_at,
        )

        em.add_field(
            name="Jump To Message", value=f"[Jump]({message.jump_url})", inline=False
        )
        em.set_footer(text="Message sent")

        msg = (
            f"I found a highlight word: **{word}**\n"
            f"Channel: {channel.mention}\n"
            f"Server: {guild}"
        )

        try:
            await user.send(msg, embed=em)
            self.bot.dispatch("highlight", message, highlight)
            log.info(
                f"{user} was highlighted by {message.author} for word {word}: {message.content}"
            )

        except (discord.HTTPException, discord.Forbidden):
            pass

    async def highlight_words(self, message, word, already_seen):
        query = """SELECT * FROM highlight_words
                   WHERE word=$1 AND guild_id=$2;
                """

        records = await self.bot.pool.fetch(query, word, message.guild.id)

        seen = []

        for record in records:
            log.debug(
                f"Word: {word} | Found record for user {record['user_id']} for message {message.id}"
            )

            if record["user_id"] not in already_seen and record["user_id"] not in seen:
                self.bot.loop.create_task(self.send_notification(message, word, record))
                seen.append(record["user_id"])

            else:
                log.debug(
                    f"Word: {word} | User {record['user_id']} has already seen message {message.id}, aborting"
                )

        return seen

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        # Check if the word is in the highlight words cache
        # Create a task so I can run the queries and send the messages concurrently
        # and not one at a time

        already_seen = []

        for highlight in self.bot.highlight_words:
            escaped = re.escape(highlight)
            hl = re.compile(r"(?:\W+)?(?:{0})[{0}]*(?:\W+|('|\")?s)?$".format(escaped), re.I)
            for word in message.content.lower().split():
                match = hl.match(word)

                if not match:
                    continue

                seen = await self.highlight_words(message, highlight, already_seen)
                already_seen.extend(seen)

    @commands.group(aliases=["hl"], invoke_without_command=True)
    async def highlight(self, ctx):
        """Get notified when your highlight words are said in chat.

        This is meant to emulate Skype's highlighted words feature.
        Concept was taken from Danny's Highlight bot, source code is original.
        """
        await ctx.send_help(ctx.command)

    @highlight.command(
        name="add",
        description="Add a word to your highlight words",
        usage="[word]",
    )
    async def highlight_add(self, ctx, *, word):
        self.delete_timer(ctx.message)

        word = word.lower().strip()

        # if len(word) < 3:
        #     raise commands.BadArgument(
        #         "Your word is too small. Must be three or more characters."
        #     )

        query = """INSERT INTO highlight_words (word, user_id, guild_id)
                   VALUES ($1, $2, $3);
                """

        query = """INSERT INTO highlight_words (word, user_id, guild_id)
                   VALUES ($1, $2, $3);
                """

        async with ctx.db.acquire() as con:
            tr = con.transaction()
            await tr.start()

            try:
                await ctx.db.execute(query, word, ctx.author.id, ctx.guild.id)

            except asyncpg.UniqueViolationError:
                await tr.rollback()
                await ctx.delete_send(f"You already have this highlight word registered.")

            except Exception:
                await tr.rollback()
                await ctx.delete_send(f"Could not add that word to your list. Sorry.")

            else:
                await tr.commit()

                if word not in self.bot.highlight_words:
                    self.bot.highlight_words.append(word)

                await ctx.delete_send(ctx.tick(True, "Successfully updated your highlight words."))

    @highlight.command(
        name="remove",
        description="Remove a word from your highlight words",
        usage="[word]",
    )
    async def highlight_remove(self, ctx, word):
        self.delete_timer(ctx.message)

        query = """DELETE FROM highlight_words
                   WHERE word=$1 AND user_id=$2 AND guild_id=$3
                   RETURNING id;
                """
        deleted = await ctx.db.fetchrow(
            query, word.lower(), ctx.author.id, ctx.guild.id
        )

        if deleted is None:
            await ctx.delete_send("That word isn't in your highlight words.")

        else:
            if word in self.bot.highlight_words:
                self.bot.highlight_words.pop(self.bot.highlight_words.index(word))

            await ctx.delete_send(ctx.tick(True, "Successfully updated your highlight words."))

    @highlight.command(
        name="all",
        description="View all your highlight words for this server",
        aliases=["list", "show"],
    )
    async def highlight_all(self, ctx):
        self.delete_timer(ctx.message, 5)

        query = """SELECT word FROM highlight_words
                   WHERE user_id=$1 AND guild_id=$2;
                """

        records = await ctx.db.fetch(query, ctx.author.id, ctx.guild.id)

        if not records:
            return await ctx.delete_send("You have no highlight words for this server.")

        words = "\n".join([r[0] for r in records])

        em = discord.Embed(
            title="Your highlight words",
            description=words,
            color=discord.Color.blurple(),
        )

        em.set_footer(text=f"Total highlight words: {len(records)}")

        await ctx.delete_send(embed=em, delete_after=10.0)

    @highlight.command(
        name="import",
        description="Import your words from another server",
        aliases=["transfer"]
    )
    async def highlight_import(self, ctx, guild_id: int):
        # self.delete_timer(ctx.message)

        # query = """INSERT INTO highlight_words (word, user_id, guild_id)
        #            SELECT word, user_id, $3 FROM highlight_words
        #            WHERE guild_id=$1 AND user_id=$2
        #         """

        # await ctx.db.execute(query, guild_id, ctx.author.id, ctx.guild.id)

        # await ctx.delete_send(ctx.tick(True, "Transferred words"))

        self.delete_timer(ctx.message)

        query = """SELECT word, user_id, guild_id
                   FROM highlight_words
                   WHERE (guild_id=$1 OR guild_id=$3) AND user_id=$2;
                """

        records = await ctx.db.fetch(query, guild_id, ctx.author.id, ctx.guild.id)

        current_words = [r["word"] for r in records if r["guild_id"] == ctx.guild.id]
        words = []

        for record in records:
            gid = record["guild_id"]
            if gid == ctx.guild.id or (gid == guild_id and record["word"] in current_words):
                continue

            words.append({
                "word": record["word"],
                "user_id": record["user_id"],
                "guild_id": ctx.guild.id
                })

        if not words:
            return await ctx.delete_send("No words to transfer.")

        query = """INSERT INTO highlight_words (word, user_id, guild_id)
                   SELECT x.word, x.user_id, x.guild_id
                   FROM jsonb_to_recordset($1::jsonb) AS
                   x(word TEXT, user_id BIGINT, guild_id BIGINT)
                """

        await ctx.db.execute(query, words)

        await ctx.delete_send(ctx.tick(True, "Transferred words"))

    @highlight.command(name="clear")
    async def highlight_clear(self, ctx):
        """Clears all your highlight words in this server"""
        self.delete_timer(ctx.message)

        query = """DELETE FROM highlight_words
                   WHERE guild_id=$1 AND user_id=$2
                   RETURNING id;
                """

        records = await ctx.db.fetch(query, ctx.guild.id, ctx.author.id)

        if not records:
            return await ctx.send("You have no highlight words in this server.", delete_after=5.0)

        await ctx.send(ctx.tick(True, f"Deleted {plural(len(records)):highlight word|highlight words}."))

    # CONFIG SECTION

    async def cog_command_error(self, ctx, error):
        if isinstance(error, AlreadyBlocked):
            await ctx.delete_send("That user or channel is already blocked.")

        elif isinstance(error, NotBlocked):
            await ctx.delete_send("That user or channel isn't blocked.")

    async def get_config(self, user):
        query = """SELECT *
                   FROM highlight_user_config
                   WHERE user_id=$1;
                """

        record = await self.bot.pool.fetchrow(query, user)

        if not record:
            return None

        return UserConfigHelper.from_record(record)

    async def block_user(self, author, user):
        query = """SELECT *
                   FROM highlight_user_config
                   WHERE user_id=$1;
                """

        record = await self.bot.pool.fetchrow(query, author)

        if not record:
            query = """INSERT INTO highlight_user_config (user_id, blocked_users)
                       VALUES ($1, $2);
                    """

            await self.bot.pool.execute(query, author, [user])

        else:
            blocked_users = record["blocked_users"]

            blocked_users = blocked_users or []

            if blocked_users and user in blocked_users:
                raise AlreadyBlocked()

            blocked_users.append(user)

            query = """UPDATE highlight_user_config
                       SET blocked_users=$2
                       WHERE user_id=$1;
                    """

            await self.bot.pool.execute(query, author, blocked_users)

    async def unblock_user(self, author, user):
        query = """SELECT *
                   FROM highlight_user_config
                   WHERE user_id=$1;
                """

        record = await self.bot.pool.fetchrow(query, author)

        if not record:
            raise NotBlocked()

        else:
            blocked_users = record["blocked_users"]

            if not blocked_users or user not in blocked_users:
                raise NotBlocked()

            blocked_users.pop(blocked_users.index(user))

            query = """UPDATE highlight_user_config
                       SET blocked_users=$2
                       WHERE user_id=$1;
                    """

            await self.bot.pool.execute(query, author, blocked_users)

    async def block_channel(self, author, channel):
        query = """SELECT *
                   FROM highlight_user_config
                   WHERE user_id=$1;
                """

        record = await self.bot.pool.fetchrow(query, author)

        if not record:
            query = """INSERT INTO highlight_user_config (user_id, blocked_channels)
                       VALUES ($1, $2);
                    """

            await self.bot.pool.execute(query, author, [channel])

        else:
            blocked_channels = record["blocked_channels"]

            if not blocked_channels:
                blocked_channels = [channel]

            else:
                if channel in blocked_channels:
                    raise AlreadyBlocked()

                blocked_channels.append(channel)

            query = """UPDATE highlight_user_config
                       SET blocked_channels=$2
                       WHERE user_id=$1;
                    """

            await self.bot.pool.execute(query, author, blocked_channels)

    async def unblock_channel(self, author, channel):
        query = """SELECT *
                   FROM highlight_user_config
                   WHERE user_id=$1;
                """

        record = await self.bot.pool.fetchrow(query, author)

        if not record:
            raise NotBlocked()

        else:
            blocked_channels = record["blocked_channels"]

            if not blocked_channels or channel not in blocked_channels:
                raise NotBlocked()

            blocked_channels.pop(blocked_channels.index(channel))

            query = """UPDATE highlight_user_config
                       SET blocked_channels=$2
                       WHERE user_id=$1;
                    """

            await self.bot.pool.execute(query, author, blocked_channels)

    @highlight.command(
        description="Block a user or channel from notifiying you with your highlight words",
        aliases=["ignore"],
        usage="<user or channel>",
    )
    async def block(self, ctx, *, entity: BlockConverter = None):
        self.delete_timer(ctx.message)

        entity = entity or ctx.channel

        if isinstance(entity, discord.User):
            await self.block_user(ctx.author.id, entity.id)

        elif isinstance(entity, discord.TextChannel):
            await self.block_channel(ctx.author.id, entity.id)

        await ctx.delete_send("Successfully updated your blocked list.")

    @highlight.command(
        description="Unblock a user or channel in your blocked list",
        aliases=["unignore"],
        usage="<user or channel>",
    )
    async def unblock(self, ctx, *, entity: BlockConverter = None):
        self.delete_timer(ctx.message)

        entity = entity or ctx.channel

        if isinstance(entity, discord.User):
            await self.unblock_user(ctx.author.id, entity.id)

        elif isinstance(entity, discord.TextChannel):
            await self.unblock_channel(ctx.author.id, entity.id)

        await ctx.delete_send("Successfully updated your blocked list.")

    @highlight.command(
        description="Temporarily block a user",
        aliases=["tempignore"],
        usage="<user/channel and time>",
    )
    async def tempblock(
        self, ctx, *, when: humantime.UserFriendlyTime(BlockConverter, default="")
    ):
        self.delete_timer(ctx.message)

        timers = self.bot.get_cog("Timers")

        if not timers:
            return await ctx.delete_send(
                "This functionality is not available right now. Please try again later."
            )

        entity = when.arg or ctx.channel
        time = when.dt

        if isinstance(entity, discord.User):
            await self.block_user(ctx.author.id, entity.id)
            await timers.create_timer(
                time, "highlight_user_block", ctx.author.id, entity.id
            )
            friendly = "user"

        elif isinstance(entity, discord.TextChannel):
            await self.block_channel(ctx.author.id, entity.id)
            await timers.create_timer(
                time, "highlight_channel_block", ctx.author.id, entity.id
            )
            friendly = "channel"

        await ctx.delete_send(
            f"Temporarily blocked {friendly} for {humantime.timedelta(time)}"
        )

    @commands.Cog.listener()
    async def on_highlight_user_block_timer_complete(self, timer):
        author, user = timer.args

        try:
            await self.unblock_user(author, user)

        except NotBlocked:
            return

    @commands.Cog.listener()
    async def on_highlight_channel_block_timer_complete(self, timer):
        author, channel = timer.args

        try:
            await self.unblock_channel(author, channel)

        except NotBlocked:
            return

    @highlight.command(description="Display your blocked list")
    async def blocked(self, ctx):
        self.delete_timer(ctx.message, 5)

        em = discord.Embed(
            title="Your blocked list",
            color=discord.Color.blurple(),
        )

        query = """SELECT blocked_users
                   FROM highlight_user_config
                   WHERE user_id=$1;
                """

        record = await ctx.db.fetchrow(query, ctx.author.id)

        users = []

        if not record or not record[0]:
            pass

        else:
            for user_id in record[0]:
                user = self.bot.get_user(user_id)
                users.append(str(user) if user else str(user_id))

        em.add_field(name="Users", value="\n".join(users) or "No blocked users")

        query = """SELECT blocked_channels
                   FROM highlight_user_config
                   WHERE user_id=$1;
                """

        record = await ctx.db.fetchrow(query, ctx.author.id)

        channels = []

        if not record or not record[0]:
            pass

        else:
            for channel_id in record[0]:
                channel = self.bot.get_channel(channel_id)
                channels.append(channel.mention if channel else str(channel_id))

        em.add_field(
            name="Channels", value="\n".join(channels) or "No blocked channels"
        )

        await ctx.delete_send(embed=em, delete_after=10.0)

    # STATS

    async def bulk_insert(self):
        query = """INSERT INTO highlights (word, guild_id, channel_id, author_id, user_id, invoked_at)
                   SELECT x.word, x.guild, x.channel, x.author, x.uid, x.invoked_at
                   FROM jsonb_to_recordset($1::jsonb) AS
                   x(word TEXT, guild BIGINT, channel BIGINT, author BIGINT, uid BIGINT, invoked_at TIMESTAMP)
                """

        if self._highlight_data_batch:
            await self.bot.pool.execute(query, self._highlight_data_batch)
            total = len(self._highlight_data_batch)
            if total > 1:
                log.info("Registered %s highlights to the database.", total)
            self._highlight_data_batch.clear()

    def cog_unload(self):
        self.bulk_insert_loop.stop()

    @tasks.loop(seconds=10.0)
    async def bulk_insert_loop(self):
        async with self._batch_lock:
            await self.bulk_insert()

    @commands.Cog.listener()
    async def on_highlight(self, message, highlight):
        await self.register_highlight(message, highlight)

    async def register_highlight(self, message, highlight):
        async with self._batch_lock:
            self._highlight_data_batch.append(
                {
                    "word": highlight.word,
                    "guild": highlight.guild_id,
                    "channel": message.channel.id,
                    "author": message.author.id,
                    "uid": highlight.user_id,
                    "invoked_at": message.created_at.isoformat(),
                }
            )

    @highlight.command()
    async def stats(self, ctx):
        em = discord.Embed(title="Highlight Stats", color=colors.PRIMARY)

        query = "SELECT COUNT(*) FROM highlights"
        count = await ctx.db.fetchrow(query)

        em.add_field(name="Total highlights", value=count[0])

        query = "SELECT COUNT(*) FROM highlights WHERE guild_id=$1"
        count = await ctx.db.fetchrow(query, ctx.guild.id)

        em.add_field(name="Total highlights here", value=count[0])

        await ctx.send(embed=em)

    async def typing_wait(self, channel, user, stop_time):
        await discord.utils.sleep_until(stop_time)
        self.typing_users[channel].pop(user)
        if not self.typing_users[channel]:
            self.typing_users.pop(channel)

    # Typing tracking
    @commands.Cog.listener()
    async def on_typing(self, channel, user, when):
        typing_channel = self.typing_users.get(channel.id)

        if not typing_channel:
            typing_channel = self.typing_users[channel.id] = {}

        wait_task = typing_channel.get(user.id)

        if wait_task and not wait_task.cancelled():
            wait_task.cancel()

        stop_time = when + datetime.timedelta(seconds=10)
        task = self.bot.loop.create_task(self.typing_wait(channel.id, user.id, stop_time))
        typing_channel[user.id] = task

    @commands.Cog.listener("on_message")
    async def typing_message_canceller(self, message):
        if self.is_typing(message.channel, message.author):
            self.typing_users[message.channel.id][message.author.id].cancel()
            self.typing_users[message.channel.id].pop(message.author.id)
            if not self.typing_users[message.channel.id]:
                self.typing_users.pop(message.channel.id)

    def is_typing(self, channel, user):
        typing_channel = self.typing_users.get(channel.id)

        if not typing_channel:
            return False

        if not typing_channel.get(user.id):
            return False

        return True


def setup(bot):
    bot.add_cog(Highlight(bot))
