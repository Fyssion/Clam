from datetime import datetime, timedelta

import discord
from discord.ext import commands, menus


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


class SinglePlayerGame(menus.Menu):
    pass


class TenSeconds(SinglePlayerGame):
    def __init__(self):
        super().__init__(timeout=30.0)

    async def send_initial_message(self, ctx, channel):
        em = discord.Embed(description="Click the reaction after 10 seconds!",
                           color=discord.Color.blurple())
        msg = await channel.send(embed=em)
        self.ten_seconds = datetime.utcnow() + timedelta(seconds=10)
        return msg

    @menus.button("⏲️")
    async def on_time(self, payload):
        tm = datetime.utcnow()
        end_time = tm - timedelta(microseconds=tm.microsecond % 100000)
        tm = self.ten_seconds
        ten_seconds = tm - timedelta(microseconds=tm.microsecond % 10000)
        if ten_seconds == end_time:
            msg = ":tada: You did it!"
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
        await self.message.edit(embed=em)
        self.stop()


class MultiPlayerGame(menus.Menu):

    def __init__(self, players):
        super().__init__(timeout=600.0) # 10 min
        self.players = players

    def reaction_check(self, payload):
        if payload.message_id != self.message.id:
            return False
        if payload.user_id not in [p.id for p in self.players]:
            return False
        return payload.emoji in self.buttons


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
                continue
            if previous_piece == piece or not previous_piece:
                counter += 1
                if counter == 4:
                    winner = piece
                    break
            else:
                counter = 0
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
                counter = 0
            previous_piece = piece

        return winner


# 1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣
class Connect4(MultiPlayerGame):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.board = Connect4Board()
        self.current_player = 0
        self.pieces = [Piece("Red", "red_circle"), Piece("Blue", "blue_circle")]

    async def finalize(self):
        if self.timeout:
            em = self.make_embed(timeout=True)
            await self.message.edit(embed=em)

    async def send_initial_message(self, ctx, channel):
        em = self.make_embed()
        return await channel.send(embed=em)

    def make_embed(self, winner=None, draw=False, timeout=False):
        embed = discord.Embed(title="Connect 4", description=self.board.make(),
                              color=discord.Color.blurple())
        embed.description += "1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣"
        if winner:
            embed.description += f"\n:tada: Winner: {winner.mention}\nThanks for playing!"
        elif draw:
            embed.description += "\nDraw game!"
        elif timeout:
            embed.description += f"\nGame is over.\n{self.players[self.current_player].mention} timed out."
        else:
            embed.description += f"\nCurrent player: {self.players[self.current_player].mention}"

        embed.set_footer(text=f"{self.players[0]} vs {self.players[1]}")

        return embed

    async def display(self):
        em = self.make_embed()
        await self.message.edit(embed=em)

    def find_diagonal_4(self):
        height = len(self.board[0].pieces)
        width = len(self.board.rows)
        board = self.board

        for piece in self.pieces:
            # check / diagonal spaces
            for x in range(width - 3):
                for y in range(3, height):
                    if board[x][y] == piece and board[x+1][y-1] == piece and board[x+2][y-2] == piece and board[x+3][y-3] == piece:
                        return piece

            # check \ diagonal spaces
            for x in range(width - 3):
                for y in range(height - 3):
                    if board[x][y] == piece and board[x+1][y+1] == piece and board[x+2][y+2] == piece and board[x+3][y+3] == piece:
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

        print(winner)

        if winner:
            winner = self.players[self.pieces.index(winner)]

        return winner

    async def play_piece(self, payload, number):
        number -= 1
        rows = self.board.rows
        member = discord.utils.get(self.players, id=payload.user_id)
        if member != self.players[self.current_player]:
            return
        piece = self.pieces[self.players.index(member)]
        placed = False

        for i in range(5):
            row = rows[i]
            if i == 4:
                if not row.pieces[number]:
                    row.pieces[number] = piece
                    placed = True
                    break
            next_row = rows[i+1]
            if next_row.pieces[number]:
                row.pieces[number] = piece
                placed = True
                break

        if placed:
            if self.current_player == 0:
                self.current_player = 1
            else:
                self.current_player = 0

        winner = self.find_4()

        if winner:
            em = self.make_embed(winner=winner)
            await self.message.edit(embed=em)
            self.stop()
            return

        all_pieces = []

        for row in self.board.rows:
            for piece in row.pieces:
                all_pieces.append(piece)

        if None not in all_pieces:
            em = self.make_embed(draw=True)
            await self.message.edit(embed=em)
            self.stop()
            return

        await self.display()

    @menus.button("1️⃣")
    async def on_one(self, payload):
        await self.play_piece(payload, 1)

    @menus.button("2️⃣")
    async def on_two(self, payload):
        await self.play_piece(payload, 2)

    @menus.button("3️⃣")
    async def on_three(self, payload):
        await self.play_piece(payload, 3)

    @menus.button("4️⃣")
    async def on_four(self, payload):
        await self.play_piece(payload, 4)

    @menus.button("5️⃣")
    async def on_five(self, payload):
        await self.play_piece(payload, 5)

    @menus.button("6️⃣")
    async def on_six(self, payload):
        await self.play_piece(payload, 6)


class Games(commands.Cog, name=":video_game: Games"):
    """Games to play with friends"""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="game", description="Play a game")
    async def _game(self, ctx):
        pass

    @commands.command(desciption="Start a Connect 4 game", usage="[opponent]")
    async def connect4(self, ctx, opponent: discord.Member):
        game = Connect4([ctx.author, opponent])
        await game.start(ctx)

    @commands.command(name="10s", description="Start a ten seconds game. Timer starts as soon as my message is sent.")
    async def ten_seconds(self, ctx):
        m = TenSeconds()
        await m.start(ctx)


def setup(bot):
    bot.add_cog(Games(bot))

if __name__ == "__main__":

    def find_diagonal_4(board, pieces):
        height = len(board[0].pieces)
        width = len(board.rows)

        for piece in pieces:
            # check / diagonal spaces
            for x in range(width - 3):
                for y in range(3, height):
                    print(x, y)
                    if board[x][y] == piece and board[x+1][y-1] == piece and board[x+2][y-2] == piece and board[x+3][y-3] == piece:
                        return piece

            # check \ diagonal spaces
            for x in range(width - 3):
                for y in range(height - 3):
                    if board[x][y] == piece and board[x+1][y+1] == piece and board[x+2][y+2] == piece and board[x+3][y+3] == piece:
                        return piece

        return None

    board = Connect4Board()
    pieces = [Piece("Red", "red_circle"), Piece("Blue", "blue_circle")]
    red = pieces[0]
    board.rows[4].pieces[2] = red
    board.rows[3].pieces[3] = red
    board.rows[2].pieces[4] = red

    print(find_diagonal_4(board, pieces))
