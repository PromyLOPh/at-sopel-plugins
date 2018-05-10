"""
Announce recent changes to a MediaWiki via /notice’s

Depends on python-dateutil, requests and sopel
"""

import requests
import dateutil.parser
from dateutil.tz import tzutc
from datetime import datetime, timedelta
from urllib.parse import quote

import sopel.module
from sopel.config.types import StaticSection, ValidatedAttribute, NO_DEFAULT

class Mwrc:
    def __init__ (self, url, initialBackoff=timedelta (minutes=30), maxpages=5, maxHold=timedelta (hours=2)):
        self.url = url
        self.newest = None
        self.pages = {}

        # wait 30 min for another edit before posting this one, immediately after we posted one
        self.initialBackoff = initialBackoff
        # max pages per post
        self.maxpages = maxpages
        # if an edit is this old, post it anyway
        self.maxHold = maxHold

    @staticmethod
    def plural (n):
        if n != 1:
            return 's'
        else:
            return ''

    def humantimedelta (self, t):
        s = t.total_seconds ()
        s, seconds = divmod (s, 60)
        s, minutes = divmod (s, 60)
        s, hours = divmod (s, 24)
        weeks, days = divmod (s, 7)
        if weeks > 0:
            weeks = int (weeks)
            return '{:d} week{} ago'.format (weeks, self.plural (weeks))
        elif days > 0:
            days = int (days)
            return '{:d} day{} ago'.format (days, self.plural (days))
        elif hours > 0:
            hours = int (hours)
            return '{:d} hour{} ago'.format (hours, self.plural (hours))
        elif minutes > 0:
            minutes = int (minutes)
            return '{:d} minute{} ago'.format (minutes, self.plural (minutes))
        else:
            return 'just now'

    @staticmethod
    def changeToVerb (c):
        eventtype = c['type']
        if eventtype == 'new':
            return 'create'
        elif eventtype == 'log':
            logaction = c['logaction']
            if logaction in ['delete', 'block', 'create', 'restore', 'overwrite', 'move', 'upload', 'autopromote', 'tag', 'interwiki', 'protect']:
                return logaction
            elif logaction == 'reviewed':
                return 'review'
            elif logaction == 'event':
                logtype = c['logtype']
                if logtype in ['delete']:
                    return logtype
            elif logaction == 'revision':
                logtype = c['logtype']
                if logtype in ['delete']:
                    return logtype
        elif eventtype in ('categorize', 'edit'):
            return 'edit'
        raise NotImplementedError (c)

    @staticmethod
    def verbToPast (v):
        irregular = {'overwrite': 'overwritten', 'tag': 'tagged'}
        if v in irregular:
            return irregular[v]
        if v.endswith ('e'):
            return v + 'd'
        else:
            return v + 'ed'

    @staticmethod
    def trunc (s, maxlen):
        """
        Truncate string to a little more than maxlen characters, keeping words
        intact
        """
        s = s.split (' ')
        t = []
        while len (' '.join (t)) < maxlen and len (s) > 0:
            t.append (s.pop (0))
        if s:
            t.append ('…')
        return ' '.join (t)

    def formatChanges (self, changes):
        """
        Print condensed changes for one item (i.e. page)
        """
        firstc = changes[0]
        lastc = changes[-1]
        timeago = self.humantimedelta (datetime.now (tzutc ()) - lastc['timestamp'])
        s = '{title} '.format (title=lastc['title'])
        sChanges = []
        lastverb = None
        for c in changes:
            verb = self.verbToPast (self.changeToVerb (c))
            t = ''
            if lastverb != verb:
                t += verb + ' by '
                lastverb = verb
            t += '{user}'.format (**c)
            if 'newlen' in c and 'oldlen' in c:
                t += ' ({:+d}'.format (c['newlen']-c['oldlen'])
                if 'comment' in c and c['comment']:
                    t += ', ' + self.trunc (c['comment'], 30)
                t += ')'
            sChanges.append (t)
        s += ', '.join (sChanges)
        s += ' {ago}'.format (ago=timeago)
        if lastc['revid']:
            s += ' -- {url}?diff={revid}&oldid={old_revid}'.format (url=self.url, revid=lastc['revid'], old_revid=firstc['old_revid'])
        else:
            s += ' -- {url}?title={title}'.format (url=self.url, title=quote (lastc['title']))
        return s

    def refresh (self):
        delta = (timedelta () - self.initialBackoff)/self.maxHold
        holdfunc = lambda x: self.initialBackoff+delta*x
        msgs = []

        url = self.url + '/api.php'
        # pages and files only
        namespaces = [0, 6]
        params = {
                'action': 'query',
                'list': 'recentchanges',
                'rcdir': 'older',
                'format': 'json',
                'rcprop': 'user|comment|timestamp|sizes|title|flags|ids|loginfo',
                'continue': '',
                'rclimit': '500',
                'rcnamespace': '|'.join (map (str, namespaces)),
                }
        if self.newest:
            # XXX we can miss a change on high-traffic wikis, but there’s no other way, I think
            params['rcend'] = int (self.newest.timestamp ()+1)
        try:
            r = requests.get(url, params=params, timeout=30)
            changes = r.json ()['query']['recentchanges']
        except KeyError:
            return msgs
        except:
            return msgs

        # group changes by page
        for c in changes:
            # fix a few datatypes
            c['timestamp'] = dateutil.parser.parse (c['timestamp'])

            i = c['ns'], c['title']
            self.pages.setdefault (i, {'posted': None, 'pending': []})
            p = self.pages[i]['pending']
            p.append (c)

            if self.newest is None or self.newest < c['timestamp']:
                self.newest = c['timestamp']
        
        pages = list (filter (lambda x: len (x['pending']) > 0, self.pages.values ()))
        # sort by post date
        pages.sort (key=lambda x: max (map (lambda y: y['timestamp'], x['pending'])))

        now = datetime.now (tzutc ())

        # first get all pages with pending changes
        pending = []
        for p in pages:
            p['pending'].sort (key=lambda x: x['timestamp'])

            # notify if there was nothing posted for this page yet or if we waited long enough
            if p['posted'] is None or \
                    holdfunc (now - p['posted']) < (now - p['pending'][-1]['timestamp']):
                pending.append (p)

        # sorted ascending by timestamp, show only newest edits if too many arrived
        if len (pending) > self.maxpages:
            msgs.append ('{} edits not shown'.format (len (pages)-self.maxpages))
        # ignore them
        for p in pending[:-self.maxpages]:
            p['posted'] = now
            p['pending'] = []

        # post remaining
        for p in pending[-self.maxpages:]:
            msgs.append (self.formatChanges (p['pending']))
            p['posted'] = now
            p['pending'] = []

        # clean up state
        delkeys = []
        for i in self.pages.keys ():
            if len (self.pages[i]['pending']) == 0 and \
                    now - self.pages[i]['posted'] > timedelta (hours=24):
                delkeys.append (i)
        for k in delkeys:
            del self.pages[k]

        return msgs

class MwrcSection(StaticSection):
    channel = ValidatedAttribute('channel', default=NO_DEFAULT)
    url = ValidatedAttribute('url', default=NO_DEFAULT)

def configure(config):
    # XXX: can we support multiple channels and wikis?
    config.define_section('mwrc', MwrcSection, validate=False)
    config.mwrc.configure_setting(
        'channel', 'Channel to announce to')
    config.mwrc.configure_setting(
        'url', 'MediaWiki base URL')

def setup(bot):
    bot.config.define_section('mwrc', MwrcSection)
    if 'mwrc' not in bot.memory:
        o = Mwrc (bot.config.mwrc.url)
        # ignore everything up until this point
        o.refresh ()
        bot.memory['mwrc'] = o

@sopel.module.interval(60)
def update (bot):
    channel = bot.config.mwrc.channel
    if channel in bot.channels:
        for l in bot.memory['mwrc'].refresh ():
            bot.notice (l, channel)

