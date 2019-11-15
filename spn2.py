"""
Sopel plugin using Internet Archive’s Save Page Now (SPN) API to archive
requested urls.
"""

import requests, time, json
from threading import Lock

from sopel import module
from sopel.config.types import StaticSection, ValidatedAttribute, FilenameAttribute

class SPN2Section (StaticSection):
    access = ValidatedAttribute ('access', str)
    secret = ValidatedAttribute ('secret', str)
    logfile = FilenameAttribute ('logfile')

def setup(bot):
    bot.config.define_section('spn2', SPN2Section)
    bot.memory['spn2loglock'] = Lock ()

def configure(config):
    config.define_section('spn2', SPN2Section, validate=False)
    config.spn2.configure_setting('access', 'IA access token')
    config.spn2.configure_setting('secret', 'IA access token secret')
    config.spn2.configure_setting('logfile', 'JSON log file')

@module.nickname_commands('spn')
@module.require_chanmsg ()
@module.thread (True)
def spn (bot, trigger):
    config = bot.config
    cmd = trigger.groups ()
    url = cmd[2]
    args = list (filter (lambda x: x is not None, cmd[3:]))
    data = {'url': url}
    for a in args:
        if a == 'screenshot':
            data['capture_screenshot'] = '1'
        elif a == 'outlinks':
            data['capture_outlinks'] = '1'
        elif a == 'errors':
            data['capture_all'] = '1'

    headers = {
        'Authorization': f'LOW {config.spn2.access}:{config.spn2.secret}',
        # API will send HTML otherwise
        'Accept': 'application/json',
        }
    try:
        resp = requests.post ('https://web.archive.org/save', data=data, headers=headers)
        o = resp.json ()
    except Exception as e:
        print (e)
        bot.reply ('I’m sorry, but I can’t process your request right now, try again later.')
        return
    jobid = o['job_id']

    # reply when job has been queued
    ret = [f'Queued {url} as {jobid}']
    if args:
        ret.append (f' with {",".join(args)}')
    bot.reply (''.join (ret))

    while True:
        try:
            resp = requests.get (f'https://web.archive.org/save/status/{jobid}', headers=headers)
            o = resp.json ()
        except Exception as e:
            print (e)
            bot.reply (f'Can’t check status of job {jobid}.')
            return
        if o['status'] in {'success', 'error'}:
            break
        time.sleep (5)

    if config.spn2.logfile:
        # each command runs in its own thread, thus we need to serialize
        # logfile access
        lock = bot.memory['spn2loglock']
        with lock:
            with open (config.spn2.logfile, 'a') as fd:
                json.dump (o, fd)
                fd.write ('\n')

    # build response message
    ret = [f'Job {jobid}']
    if o['status'] == 'success':
        ret.append (' finished')
    elif o['status'] == 'error':
        ret.append (' failed')
        if 'message' in o:
            ret.append (f' ({o["message"]})')
    if 'resources' in o:
        ret.append (f', {len(o["resources"])} resources')
    if 'outlinks' in o:
        ret.append (f', {len(o["outlinks"])} outlinks')
    if 'seconds_ago' in o:
        ret.append (f', cached {o["seconds_ago"]} seconds ago')
    if 'duration_sec' in o:
        ret.append (f', took {o["duration_sec"]} seconds')
    if 'timestamp' in o and 'original_url' in o:
        ret.append (f' -- https://web.archive.org/web/{o["timestamp"]}/{o["original_url"]}')

    bot.reply (''.join (ret))

