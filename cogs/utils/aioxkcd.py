import aiohttp
import json
from datetime import datetime
from random import randint


class XkcdError(Exception):
    pass


class Comic:
    """An xkcd comic.

    INSTANCES SHOULD ONLY BE CREATED VIA THE fetch_comic() CLASSMETHOD.

    Parameters:
        unparsed_data (bytes): The data to be parsed and decoded
        number (int): The number of the comic
        url (str): The url of the comic

    Attributes:
        data (dict): The decoded and parsed JSON data
        url (str): The permaurl for the comic
        number (int): The comic's number
        title (str): Title of the comic
        alt_text (str): The hover text (alt text)
        image_url (str): URL to the image
        year (int): The year the comic was published
        month (int): The month the comic was published
        day (int): The day of the month the comic was published
        publish_date (datetime): datetime from year, month, and day
        date_str (str): Formatted datetime ({month} {day}, {year})

    """
    __slots__ = ["number", "url", "_unparsed_data", "data",
                 "title", "alt_text", "description", "image_url",
                 "year", "month", "day", "publish_date", "date_str"]

    XKCD_URL = "https://www.xkcd.com/"
    IMAGE_URL = "https://imgs.xkcd.com/comics/"

    def __init__(self, unparsed_data: bytes, number: int, url: str):
        self._unparsed_data = unparsed_data
        self.number = number
        self.url = url
        self.data = json.loads(self._unparsed_data.decode())
        self.title = self.data['safe_title']
        self.alt_text = self.data['alt']
        self.image_url = self.data['img']
        self.year = int(self.data["year"])
        self.month = int(self.data["month"])
        self.day = int(self.data["day"])
        self.publish_date = datetime(self.year, self.month, self.day)
        self.date_str = self.publish_date.strftime("%B %#d, %Y")

    def __str__(self):
        return self.title

    @classmethod
    async def fetch_comic(cls, number: int):
        """Fetches an xkcd comic and returns an instance of the Comic class.

        Parameters:
            number (int): The comic number

        Returns:
            Comic: A comic object

        Raises:
            XkcdError -- The comic does not exist
        """
        if number <= 0:
            raise XkcdError("That comic does not exist.")

        url = cls.XKCD_URL + str(number)
        xkcd_json = url + "/info.0.json"

        async with aiohttp.ClientSession() as session:
            async with session.get(xkcd_json) as resp:
                unparsed_data = await resp.read()

        return cls(unparsed_data, number, url)


async def get_latest_comic_num() -> int:
    """Fetches the number of the latest xkcd comic.

    Returns:
        number (int): The latest comic number
    """
    async with aiohttp.ClientSession() as session:
        async with session.get("https://xkcd.com/info.0.json") as resp:
            unparsed = await resp.read()
    data = json.loads(unparsed.decode())
    number = data['num']
    return int(number)


async def get_latest_comic() -> Comic:
    """Gets the latest xkcd comic

    Returns:
        Comic: The latest xkcd comic
    """
    latest = await get_latest_comic_num()
    return await Comic.fetch_comic(latest)


async def get_random_comic() -> Comic:
    """Gets a random xkcd comic

    Returns:
        Comic: A random xkcd comic
    """
    latest = await get_latest_comic_num()
    number = randint(1, latest)
    return await Comic.fetch_comic(number)


async def get_comic(number: int) -> Comic:
    """Gets an xkcd comic

    Parameters:
        number (int): The number of the comic to get

    Returns:
        Comic: The specified xkcd comic

    Raises:
        XkcdError -- The number is higher than the latest comic number
    """
    latest = await get_latest_comic_num()
    if number > latest:
        raise XkcdError("That comic does not exist. Number is too high.")
    return await Comic.fetch_comic(number)
