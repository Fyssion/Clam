import asyncio
import datetime
import random

import discord
from discord.ext import commands, menus
from pkg_resources import empty_provider

from clam.utils import colors


class Piece:
    def __init__(self, name, emoji_name, custom=False, id=None, animated=False):
        self.name = name
        self.emoji_name = emoji_name
        self.id = id

        if not custom:
            self.emoji = f":{emoji_name}:"
        else:
            if not animated:
                self.emoji = f"<:{emoji_name}:{id}>"
            else:
                self.emoji = f"<a:{emoji_name}:{id}>"


class SinglePlayerGame(discord.ui.View):
    def __init__(self, ctx, **kwargs):
        super().__init__(**kwargs)
        self.ctx = ctx

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user == self.ctx.author:
            return True
        else:
            await interaction.response.send_message("You didn't start this game.", ephemeral=True)
            return False


class TenSeconds(SinglePlayerGame):
    def __init__(self, ctx):
        super().__init__(ctx=ctx, timeout=30.0)

    async def start(self):
        em = discord.Embed(
            description="Click the button after 10 seconds! See how close you are.", color=colors.PRIMARY,
        )

        em.set_author(name=str(self.ctx.author), icon_url=self.ctx.author.display_avatar.url)

        em.set_footer(text=f"Confused? Learn more with {self.ctx.guild_prefix}help 10s")

        self.message = await self.ctx.send(embed=em, view=self)
        self.ten_seconds = datetime.datetime.utcnow() + datetime.timedelta(seconds=10)

    @discord.ui.button(emoji="\N{CLOCK FACE TWO OCLOCK}")
    async def time_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        tm = datetime.datetime.utcnow()
        end_time = tm - datetime.timedelta(microseconds=tm.microsecond % 100000)
        tm = self.ten_seconds
        ten_seconds = tm - datetime.timedelta(microseconds=tm.microsecond % 10000)

        msg = None

        if ten_seconds == end_time:
            msg = ":tada: You did it! I'm impressed!"

        elif ten_seconds < end_time:
            time = end_time - ten_seconds
            result = str(float(f"{time.seconds}.{time.microseconds}"))
            msg = f"You were slow by `{result}` seconds."

        elif ten_seconds > end_time:
            time = ten_seconds - end_time
            result = str(float(f"{time.seconds}.{time.microseconds}"))
            msg = f"You were fast by `{result}` seconds."

        em = self.message.embeds[0]
        em.description = msg

        self.time_button.disabled = True
        await interaction.response.edit_message(embed=em, view=self)

        self.stop()


class MultiPlayerGame(discord.ui.View):
    def __init__(self, ctx, players):
        super().__init__(timeout=600.0)  # 10 min
        self.ctx = ctx
        self.players = players

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user in self.players:
            return True
        else:
            await interaction.response.send_message("You aren't playing in this game.", ephemeral=True)
            return False


EMPTY_piece = ":black_large_square:"


class Connect4Row:
    def __init__(self, size):
        self.pieces = []
        for i in range(size):
            self.pieces.append(None)

    def __iter__(self):
        return iter(self.pieces)

    def __getitem__(self, item):
        return self.pieces[item]

    def find_4(self):
        counter = 0
        previous_piece = None
        winner = None

        for i, piece in enumerate(self.pieces):
            if not piece:
                counter = 0
                previous_piece = piece
                continue
            if previous_piece == piece or not previous_piece:
                counter += 1
                if counter == 4:
                    winner = piece
                    break
            else:
                counter = 1
            previous_piece = piece

        return winner


class Connect4Board:
    def __init__(self, x_size=6, y_size=5):
        self.x_size = x_size
        self.y_size = y_size

        self.rows = []
        for i in range(y_size):
            self.rows.append(Connect4Row(x_size))

    def __iter__(self):
        return iter(self.rows)

    def __getitem__(self, item):
        return self.rows[item]

    def make(self):
        board = ""

        for row in self.rows:
            for piece in row:
                board += piece.emoji if piece is not None else EMPTY_piece
            board += "\n"

        return board

    def find_column_4(self, column):
        rows = self.rows
        counter = 0
        previous_piece = None
        winner = None

        for i in range(5):
            row = rows[i]
            piece = row.pieces[column]
            if not piece:
                previous_piece = piece
                counter = 0
                continue
            if previous_piece == piece or not previous_piece:
                counter += 1
                if counter == 4:
                    winner = piece
                    break
            else:
                counter = 1
            previous_piece = piece

        return winner


# 1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣
class Connect4(MultiPlayerGame):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.board = Connect4Board()
        self.current_player = random.choice([0, 1])
        self.pieces = [Piece("Red", "red_circle"), Piece("Blue", "blue_circle")]
        self.winner = None

    async def on_timeout(self):
        await self.finalize(timeout=True)

    async def finalize(self, *, timeout):
        if timeout and not self.winner:
            em = self.make_embed(timeout=True)
            await self.message.edit(embed=em)

    async def start(self):
        em = self.make_embed()
        return await self.ctx.send(embed=em, view=self)

    def make_embed(self, winner=None, draw=False, timeout=False):
        # color is red if current is one, blue if current is zero
        color = 0x55ACEE if self.current_player else 0xDD2E44
        embed = discord.Embed(
            title="Connect 4", description=self.board.make(), color=color,
        )
        embed.description += "1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣"
        if winner:
            embed.description += (
                f"\n:tada: Winner: {winner.mention}\nThanks for playing!"
            )
        elif draw:
            embed.description += "\nDraw game!"
        elif timeout:
            embed.description += f"\nGame is over.\n{self.players[self.current_player].mention} timed out."
        else:
            embed.description += f"\nCurrent player: {self.players[self.current_player].mention} {self.pieces[self.current_player].emoji}"

        embed.set_footer(text=f"{self.players[0]} vs {self.players[1]}")

        return embed

    async def display(self, interaction):
        em = self.make_embed()
        await interaction.response.edit_message(embed=em, view=self)

    def find_diagonal_4(self):
        height = len(self.board[0].pieces)
        width = len(self.board.rows)
        board = self.board

        for piece in self.pieces:
            # check / diagonal spaces
            for x in range(width - 3):
                for y in range(3, height):
                    if (
                        board[x][y] == piece
                        and board[x + 1][y - 1] == piece
                        and board[x + 2][y - 2] == piece
                        and board[x + 3][y - 3] == piece
                    ):
                        return piece

            # check \ diagonal spaces
            for x in range(width - 3):
                for y in range(height - 3):
                    if (
                        board[x][y] == piece
                        and board[x + 1][y + 1] == piece
                        and board[x + 2][y + 2] == piece
                        and board[x + 3][y + 3] == piece
                    ):
                        return piece

        return None

    def find_4(self):
        winner = None

        for row in self.board.rows:
            winner = row.find_4()
            if winner:
                break

        if not winner:
            for i in range(6):
                winner = self.board.find_column_4(i)
                if winner:
                    break

            if not winner:
                winner = self.find_diagonal_4()

        if winner:
            winner = self.players[self.pieces.index(winner)]

        return winner

    async def play_piece(self, interaction: discord.Interaction, number):
        number -= 1
        rows = self.board.rows
        member = interaction.user
        if member != self.players[self.current_player]:
            return await interaction.response.send_message("It isn't your turn!", ephemeral=True)

        piece = self.pieces[self.players.index(member)]
        placed = False

        for i in range(5):
            row = rows[i]
            if i == 0 and row.pieces[number]:
                break
            if i == 4:
                if not row.pieces[number]:
                    row.pieces[number] = piece
                    placed = True
                    break
            next_row = rows[i + 1]
            if next_row.pieces[number]:
                row.pieces[number] = piece
                placed = True
                break

        winner = self.find_4()

        if winner:
            self.winner = winner
            em = self.make_embed(winner=winner)
            await interaction.response.edit_message(embed=em, view=self)
            self.stop()
            return

        all_pieces = []

        for row in self.board.rows:
            for piece in row.pieces:
                all_pieces.append(piece)

        if None not in all_pieces:
            self.winner = "draw"
            em = self.make_embed(draw=True)
            await interaction.response.edit_message(embed=em, view=self)
            self.stop()
            return

        if placed:
            if self.current_player == 0:
                self.current_player = 1
            else:
                self.current_player = 0

        await self.display(interaction)

    @discord.ui.button(label="1")
    async def one_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play_piece(interaction, 1)

    @discord.ui.button(label="2")
    async def two_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play_piece(interaction, 2)

    @discord.ui.button(label="3️")
    async def three_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play_piece(interaction, 3)

    @discord.ui.button(label="4️", row=1)
    async def four_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play_piece(interaction, 4)

    @discord.ui.button(label="5️", row=1)
    async def five_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play_piece(interaction, 5)

    @discord.ui.button(label="6️", row=1)
    async def six_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play_piece(interaction, 6)


class Hangman:
    def __init__(self, words):
        self.words = words
        self.guesses_left = 6
        self.correct_letters = []
        self.incorrect_letters = []
        self.game_status = None
        self.timeout = None

    @property
    def wordlist(self):
        return "".join(self.words)

    async def timeout_task(self):
        await asyncio.sleep(600)  # 10m
        await self.stop("Game timed out. Nobody guessed for 10 minutes.")
        self.ctx.cog.hangman_games.pop(self.channel.id)  # delete itself

    def create_embed(self):
        desc = f"Guess a letter with `{self.ctx.guild_prefix}guess <letter>`"

        if self.game_status == "win":
            desc = ":tada: All letters guessed correctly!"
        elif self.game_status == "lose":
            word = " ".join(self.words)
            desc = (
                f"Game over. You ran out of guesses.\nThe word was... ||{word}||"
            )

        em = discord.Embed(title="Hangman", description=desc, color=colors.PRIMARY)

        # Add the correct hangman thumbnail
        num = self.guesses_left
        url = f"https://raw.githubusercontent.com/Fyssion/Clam/main/assets/hangman/hangman{num}.png"

        em.set_thumbnail(url=url)

        # Generate the word display
        words = []
        for word in self.words:
            words.append(" ".join(
                l if l.lower() in self.correct_letters else "_" for l in word
            ))

        word_display = "   ".join(words)
        word_display = f"`{word_display}`"

        em.add_field(name="Word", value=word_display, inline=False)

        value = ", ".join(self.incorrect_letters) if self.incorrect_letters else "None"

        em.add_field(
            name="Incorrect Guesses", value=value,
        )
        em.add_field(name="Guesses Left", value=self.guesses_left or "No guesses left.")
        em.add_field(name="Game Creator", value=str(self.creator))

        return em

    async def start(self, ctx):
        self.ctx = ctx
        self.channel = ctx.channel
        self.creator = ctx.author

        embed = self.create_embed()
        self.message = await ctx.send(embed=embed)

        self.timeout = ctx.bot.loop.create_task(self.timeout_task())

    async def mark_error(self, ctx, message):
        await ctx.message.add_reaction("\N{HEAVY EXCLAMATION MARK SYMBOL}")
        await ctx.send(ctx.tick(False, message), delete_after=5.0)

    async def guess(self, ctx, letter):
        if self.timeout:
            self.timeout.cancel()

        self.timeout = ctx.bot.loop.create_task(self.timeout_task())

        if letter in self.correct_letters or letter in self.incorrect_letters:
            return await self.mark_error(ctx, "That letter has already been guessed.")

        if letter in list(self.wordlist.lower()):
            self.correct_letters.append(letter)

            unguessed = []
            for letter in self.wordlist.lower():
                if letter not in self.correct_letters:
                    unguessed.append(letter)

            if not unguessed:
                self.game_status = "win"

            await ctx.message.add_reaction(ctx.tick(True))

        else:
            self.incorrect_letters.append(letter)

            self.guesses_left -= 1

            if self.guesses_left <= 0:
                self.game_status = "lose"

            await ctx.message.add_reaction(ctx.tick(False))

        await self.message.edit(embed=self.create_embed())

        return self.game_status

    async def stop(self, message="Game stopped by creator or moderator."):
        em = self.create_embed()
        word = " ".join(self.words)
        em.description = f"{self.ctx.tick(False)} {message}\nThe word was... ||{word}||"

        await self.message.edit(embed=em)


class Games(commands.Cog):
    """Games to play with friends."""

    def __init__(self, bot):
        self.bot = bot
        self.emoji = "\N{VIDEO GAME}"

        if not hasattr(bot, "hangman_games"):
            # channel_id: Hangman
            self.bot.hangman_games = {}

        self.hangman_games = bot.hangman_games

    async def cog_before_invoke(self, ctx):
        if ctx.channel.id in self.hangman_games.keys():
            ctx.hangman = self.hangman_games[ctx.channel.id]
        else:
            ctx.hangman = None

    @commands.hybrid_command()
    async def connect4(self, ctx, *, opponent: discord.Member):
        """Starts a game of Connect 4."""

        if str(opponent.id) in self.bot.blacklist:
            return await ctx.send(f"Opponent `{opponent}` is blacklisted from the bot.")

        if opponent.bot:
            return await ctx.send("You cannot play with a bot.")

        if ctx.author == opponent:
            return await ctx.send("You can't play Connect 4 with yourself.")

        game = Connect4(ctx=ctx, players=[ctx.author, opponent])
        await game.start()

    @commands.hybrid_command(name="10s")
    async def ten_seconds(self, ctx):
        """Can you count to 10 seconds?

        How to play:
        - Use the 10s command
        - Count to 10 seconds
        - Click/tap the reaction under the 10s message when you finish counting
        - See how far off you were

        Timer starts as soon as my message is sent.
        """

        game = TenSeconds(ctx=ctx)
        await game.start()

    @commands.group(invoke_without_command=True)
    async def hangman(self, ctx):
        """Starts a game of hangman.

        When you use this command, a new hangman game will be
        created in this channel. You will be asked to provide a word.

        Other members in this channel will be able to guess with the
        guess subcommand below.

        Note that moderators (specifically, members with manage messages)
        can stop any hangman game.
        """

        if ctx.hangman:
            return await ctx.send(
                f"{ctx.tick(False)} There is already a hangman game in this channel."
            )

        await ctx.send("Please enter a word in your DMs...", delete_after=5.0)
        await ctx.author.send(
            "What is your word? Note that the word can only have letters A-Z and spaces."
        )

        def check(ms):
            return ms.author == ctx.author and ms.channel == ctx.author.dm_channel

        try:
            message = await self.bot.wait_for("message", check=check, timeout=180.0)
        except asyncio.TimeoutError:
            return await ctx.send(f"{ctx.tick(False)} You timed out. Aborting.")

        # Remove mentions from the word and strip it
        word = discord.utils.escape_mentions(message.content.strip())

        words = word.split()

        if not "".join(words).isalpha():
            return await ctx.author.send(
                f"{ctx.tick(False)}  That word has characters that aren't in the English alphabet. Aborting."
            )

        hangman = Hangman(words)
        self.hangman_games[ctx.channel.id] = hangman
        await hangman.start(ctx)

        await ctx.author.send(f"{ctx.tick(True)} Hangman game created")

    @hangman.command(name="guess", aliases=["g"])
    async def hangman_guess(self, ctx, letter):
        """Guess a letter in the running hangman game."""

        if not ctx.hangman:
            return await ctx.send(
                f"{ctx.tick(False)} There is no running hangman game in this channel."
            )

        mark_error = ctx.hangman.mark_error

        if ctx.author == ctx.hangman.creator:
            return await mark_error(
                ctx, "You can't guess in your own game."
            )

        letter = letter.lower().strip()

        if len(letter) > 1:
            return await mark_error(ctx, "Your letter must be a single character.")

        if not letter.isalpha():
            return await mark_error(ctx, "Your letter must be in the English alphabet.")

        status = await ctx.hangman.guess(ctx, letter)

        if status:
            del self.hangman_games[ctx.channel.id]

    @commands.command()
    async def guess(self, ctx, letter):
        """Alias for `{prefix}hangman guess`."""

        await ctx.invoke(self.hangman_guess, letter)

    @hangman.command(name="stop")
    async def hangman_stop(self, ctx):
        """Stops the running hangman game.

        You can only use this command if you started
        the hangman game or are a moderator.
        """

        if not ctx.hangman:
            return await ctx.send(
                f"{ctx.tick(False)} There is no running hangman game in this channel."
            )

        if (
            ctx.hangman.creator == ctx.author
            or ctx.author.guild_permissions.manage_messages
        ):
            await ctx.hangman.stop()
            del self.hangman_games[ctx.channel.id]
            await ctx.send(f"{ctx.tick(True)} Stopped hangman game.")

        else:
            await ctx.send(f"{ctx.tick(False)} You did not create that hangman game.")

    @hangman.command(name="all", aliases=["list"])
    @commands.is_owner()
    async def hangman_all(self, ctx):
        """Shows all hangman games."""

        games = [f"{h.ctx.guild} #{h.channel}" for h in self.hangman_games.values()]

        if not games:
            return await ctx.send("No running hangman games.")

        pages = ctx.pages(games, title="All Running Hangman Games")
        await pages.start()


async def setup(bot):
    await bot.add_cog(Games(bot))
