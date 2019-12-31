"""
Copy url into pastebin
"""

import asyncio, os, logging
import aiohttp
from yarl import URL

from sopel import module
from sopel.config.types import StaticSection, ValidatedAttribute, FilenameAttribute

async def copyurl (source, destination):
    async with aiohttp.ClientSession() as session:
        async with session.get (source) as resp:
            async with session.put (destination, data=resp.content) as postresp:
                if postresp.status == 200:
                    newurl = await postresp.text ()
                    return newurl
                else:
                    raise Exception (f'Server returned status {postresp.status}')

def setup(bot):
    bot.memory['loop'] = asyncio.get_event_loop ()

@module.nickname_commands('cp')
@module.example ('cp https://pastebin.com/raw/dWfFu2bQ test.txt')
@module.require_chanmsg ()
@module.thread (True)
def spn (bot, trigger):
    config = bot.config
    cmd = trigger.groups ()
    print (cmd)
    source = URL (cmd[2])
    destfname = cmd[3] or os.path.basename (cmd[2])
    destination = URL (f'https://transfer.notkiska.pw/{destfname}')

    if source.scheme not in {'http', 'https'}:
        bot.reply ('unsupported URL')
        return

    try:
        actualDest = bot.memory['loop'].run_until_complete (copyurl (source, destination))

        bot.reply (actualDest)
    except Exception as e:
        bot.reply (e.args[0])


