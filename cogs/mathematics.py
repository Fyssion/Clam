import discord
from discord.ext import commands

import math
import matplotlib


class Math(commands.Cog, name=":triangular_ruler: Math"):

    def __init__(self, bot):
        self.bot = bot
        self.log = self.bot.log

    @commands.command(description="Solve the quadratic formula",
                      aliases=["quad"], usage="[a b c]")
    async def quadratic(self, ctx, a: int, b: int, c: int):
        delta = (b**2) - (4*a*c)
        msg = "Quadratic formula: **( -b ± √( b^2 - 4ac ) ) / ( 2a )**\n"
        if delta < 0:
            msg += "This equation has **no real solution.**"
        elif delta == 0:
            ans = (-b+math.sqrt(b**2-4*a*c))/2*a
            msg += f"This equation has a **single solution: {ans}**"
        else:
            ans1 = (-b+math.sqrt((b**2)-(4*(a*c))))/(2*a)
            ans2 = (-b-math.sqrt((b**2)-(4*(a*c))))/(2*a)
            msg += (f"Solution one: **{ans1}**\n"
                    f"Solution two: **{ans2}**")

        await ctx.send(msg)

    @commands.command(description="Solve the distance formula",
                      aliases=["dist"], usage="[x₁ y₁ x₂ y₂]")
    async def distance(self, ctx, w: int, x: int, y: int, z: int):
        # Points (w, y) and (y, z)
        delta = (y - x)**2 + (z - y)**2
        ans = math.sqrt(delta)
        formula = "√((x₂ - x₁)^2 + (y₂ - y₁)^2)"
        await ctx.send(f"Distance fomula: **{formula}**\n"
                       f"Solution: **{ans}**")


def setup(bot):
    bot.add_cog(Math(bot))
