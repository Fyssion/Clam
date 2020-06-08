import discord
from discord.ext import commands

import math
import matplotlib


class PointsConverter(commands.Converter):
    def __init__(self, max_points=2):
        self.max_points = max_points

    async def convert(self, ctx, argument):
        # I need to parse any given number of points
        # An example would be: "(5, 6) (2, 4)"
        # This will also handle "(5,6) (2,4)"

        bad_points = "You must format your points like actual points. Ex: (5, 6) (2, 4)"
        if "(" not in argument:
            raise commands.BadArgument(
                "I didn't find any complete parenthesis in your input. " + bad_points
            )
        elif ")" not in argument:
            raise commands.BadArgument(
                "I didn't find any complete parenthesis in your input. " + bad_points
            )

        # First, find all indexes where the input needs to be split
        indexes_to_split = []
        for i, char in enumerate(list(argument)):
            if char == " " and argument[i - 1] == ")":
                indexes_to_split.append(i)

        # Next split the argument by those indexes
        args = []
        for i, index in enumerate(indexes_to_split):
            previous = indexes_to_split[i - 1] + 1 if i != 0 else 0
            args.append(argument[previous:index])
        last = indexes_to_split[len(args) - 1]
        args.append(argument[last + 1 :])

        # Finally, parse those points and return a list of points
        points = []
        for arg in args:
            if "(" not in arg:
                raise commands.BadArgument(
                    "I didn't find any complete parenthesis in one of your points. "
                    + bad_points
                )
            elif ")" not in arg:
                raise commands.BadArgument(
                    "I didn't find any complete parenthesis in one of your points. "
                    + bad_points
                )
            arg = arg[arg.find("(") + 1 : arg.find(")")]
            if ", " in arg:
                numbers = arg.split(", ")
            elif "," in arg:
                numbers = arg.split(",")
            else:
                raise commands.BadArgument(bad_points)

            if len(numbers) != 2:
                raise commands.BadArgument("I only support points with x and y. Sorry.")

            try:
                x, y = [int(n) for n in numbers]
            except ValueError:
                raise commands.BadArgument("X and Y must be numbers.")

            points.append((x, y))

        if len(args) > self.max_points:
            raise commands.BadArgument(
                f"You cannot have more than {self.max_points} point(s)."
            )

        return points


class Math(commands.Cog):
    """Math commands to help with homework or to mess around with."""

    def __init__(self, bot):
        self.bot = bot
        self.emoji = ":triangular_ruler:"
        self.log = self.bot.log

    @commands.command(
        description="Solve the quadratic formula", aliases=["quad"], usage="[a b c]"
    )
    async def quadratic(self, ctx, a: int, b: int, c: int):
        delta = (b ** 2) - (4 * a * c)
        msg = f"Quadratic formula: **`( -{b} ± √( {b}^2 - 4({a})({c}) ) ) / ( 2({a}) )`**\n"
        if delta < 0:
            msg += "This equation has no real solution."
        elif delta == 0:
            ans = (-b + math.sqrt(b ** 2 - 4 * a * c)) / 2 * a
            msg += f"This equation has a single solution: `{ans}`"
        else:
            ans1 = (-b + math.sqrt((b ** 2) - (4 * (a * c)))) / (2 * a)
            ans2 = (-b - math.sqrt((b ** 2) - (4 * (a * c)))) / (2 * a)
            msg += f"Solution one: `{ans1}`\n" f"Solution two: `{ans2}`"

        await ctx.send(msg)

    @commands.command(
        aliases=["dist"], usage="[points]",
    )
    async def distance(self, ctx, *, points: PointsConverter(max_points=2)):
        """Calculate the distance between two points.

        Example point inputs:

        - "(2, 6) (5, 4)"
        - "(8,9) (10,5)"
        """
        x1, y1 = points[0]
        x2, y2 = points[1]

        delta = (x2 - x1) ** 2 + (y2 - y1) ** 2
        ans = math.sqrt(delta)
        formula = f"√(({x2} - {x1})^2 + ({y2} - {y1})^2)"
        await ctx.send(f"Distance fomula: `{formula}`\n" f"Solution: `{ans}`")

    @commands.command(aliases=["midpt"], usage="[points]")
    async def midpoint(self, ctx, *, points: PointsConverter(max_points=5)):
        """Calculate the midpoint when given two or more points.

        Example point inputs:

        - "(2, 6) (5, 4)"
        - "(8,9) (10,5)"
        - "(15, 22) (5, 7) (14, 18)"
        """
        total_x = 0
        total_y = 0

        # For when generating the formula
        all_x = []
        all_y = []

        # Add up the x's and y's for each point
        for x, y in points:
            total_x += x
            total_y += y

            all_x.append(str(x))
            all_y.append(str(y))

        # Divide by the how many points there are
        final_x = total_x / len(points)
        final_y = total_y / len(points)

        ans = f"({final_x}, {final_y})"

        # Create the formula
        formula = (
            f"({' + '.join(all_x)})/{len(points)}, ({' + '.join(all_y)})/{len(points)}"
        )

        await ctx.send(f"Midpoint formula: `{formula}`\nSolution: `{ans}`")


def setup(bot):
    bot.add_cog(Math(bot))
