# Simple chanserv helper script for Xchat
# (c) 2006-2010 Dennis Kaarsemaker
#
# Latest version can be found on http://github.com/seveas/chanserv.py
# 
# This script is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# version 3, as published by the Free Software Foundation.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

# Usage instructions:
# - Place in ~/.xchat2 for it to be autoloaded
# - Use /py load chanserv.py if you already started X-chat
# - Connect to freenode, if not connected (other networks will not work)
#
# It adds one command to xchat: /cs
# /cs understands the following arguments
#
# To give/take ops/voice:
#
# o  or op      - Let chanserv op you/others (/cs op, /cs op nick)
# v  or voice   - Let chanserv give you/others voice
# d  or deop    - Let chanserv deop you/others (/cs deop, /cs deop nick)
# dv or devoice - Let chanserv decoice you/others (/cs devoice, /cs devoice nick)
#
# To op yourself, perform an action, and deop:
#
# k  or kick    - Kick a user, possibly with comment (/cs kick nick [comment])
# b  or ban     - Ban a user (/cs ban [-nihar] nick)
# kb or kickban - Kick and ban a user (/cs ban [-nihar] nick)
# f  or forward - Ban a user with a forward (/cs forward [-nihar] nick chan)
# kf or kickforward - Kickban a user with a forward (/cs forward [-nihar] nick chan)
# m  or mute    - Mute a user (/cs mute [-nihar] nick)
# l  or lart    - A combination of kick and ban on all fields
# u  or unban   - Remove all bans for a user (/cs u nick)
# t  or topic   - Set channel topic (/cs t New topic here)
# m  or mode    - Change channel mode (/cs mode modes here)
# i  or invite  - Invite yourself or someone else (/cs invite [nick])
# bans          - Show bans that apply to someone without removing them (/cs bans nick)
#
# * Bans, forwards and mute take an extra optional argument that specifies 
#   what should be banned: nickname, ident, host, account and/or realname.
#   /cs ban -nah nick -- Ban nick, account and host
#   /cs forward -nihra nick #somewhere -- Forward all
#
# * These commands also take an extra argument to specify when bans/mutes
#   should be lifted automatically.
#   /cs ban -t600 nick -- Ban nick for 10 minutes
#   /cs ban -nah -t3600 -- Ban nick, account and hostname for an hour
#
# * Unban will remove all bans matching the nick or mask you give as argument
#   (*  and ? wildcards work)
# * It won't actually kick, but use the /remove command
#
# The following additional features are implemented
# - Autorejoin for /remove
# - Auto-unmute when muted
# - Auto-unban via chanserv
# - Auto-invite via chanserv
# - Auto-getkey via chanserv

__module_name__        = "chanserv"
__module_version__     = "2.2.2"
__module_description__ = "Chanserv helper"

import collections
import xchat
import time
import re

# Event queue
pending = []
# /whois cache
users = {}
# /mode bq 'cache'
bans = collections.defaultdict(list)
quiets = collections.defaultdict(list)
collecting_bans = []

abbreviations = {'kick': 'k', 'ban': 'b', 'kickban': 'kb', 'forward': 'f', 
                 'kickforward': 'kf', 'mute': 'm', 'topic': 't', 'unban': 'u',
                 'mode': 'm', 'invite': 'i', 'op': 'o', 'deop': 'd', 'lart': 'l',
                 'voice': 'v', 'devoice': 'dv', 'bans': 'bans'}
expansions = dict([x[::-1] for x in abbreviations.items()])
simple_commands = ['op', 'deop', 'voice', 'devoice']
kick_commands = ['kick', 'kickforward', 'kickban', 'lart']
forward_commands = ['kickforward', 'forward']
ban_commands = ['ban', 'forward', 'mute', 'lart', 'kickban', 'kickforward']
simple_commands += [abbreviations[x] for x in simple_commands]
kick_commands += [abbreviations[x] for x in kick_commands]
ban_commands += [abbreviations[x] for x in ban_commands]
forward_commands += [abbreviations[x] for x in forward_commands]
all_commands = abbreviations.keys() + abbreviations.values()
ban_sentinel = '!'

def cs(word, word_eol, userdata):
    """Main command dispatcher"""
    if len(word) == 1:
        return xchat.EAT_ALL
    command = word[1].lower()

    if command not in all_commands:
        return xchat.EAT_NONE

    args = dict(enumerate(word_eol[2:]))
    me = xchat.get_info('nick')

    action = Action(channel = xchat.get_info('channel'),
                    me = me,
                    context = xchat.get_context())

    # The simple ones: op/voice
    if command in simple_commands:
        action.target = args.get(0, me)
        action.deop = (action.target != me)
        action.needs_op = False
        command = expansions.get(command,command)
        action.actions.append('chanserv %s %%(channel)s %%(target_nick)s' % command)
        return action.schedule()

    # Usage check
    if len(word) < 3:
        if command in all_commands:
            xchat.emit_print("Server Error", "Not enough arguments for %s" % command)
            return xchat.EAT_ALL
        return xchat.EAT_NONE
        
    if command in ('t','topic'):
        action.actions.append('chanserv TOPIC %%(channel)s %s' % args[0])
        action.needs_op = False
        return action.schedule()

    if command in ('m','mode') and args[0][0] in '+=-':
        action.actions.append('MODE %%(channel)s %s' % args[0])
        return action.schedule()

    if command in ('i','invite'):
        target = args[0]
        if target.startswith('#'):
            action.needs_op = False
            action.actions.append('chanserv INVITE %s' % target)
        else:
            if target.lower() in [x.nick.lower() for x in action.context.get_list('users')]:
                xchat.emit_print("Server Error", "%s is already in %s" % (target, action.channel))
                return xchat.EAT_ALL
            action.actions.append('INVITE %s %%(channel)s' % target)
        return action.schedule()

    # Kick/ban/forward/mute handling
    if len(word) < 4 and command in forward_commands:
        xchat.emit_print("Server Error", "Not enough arguments for %s" % command)
        return xchat.EAT_ALL

    # Command dispatch
    # Check for -nihra argument
    if command in ban_commands:
        args_start = 3
        while args[0].startswith('-'):
            if args[0].startswith('-t'):
                try:
                    action.timer = int(args[0][2:].split(None, 1)[0])
                except ValueError:
                    pass
            else:
                action.bans = args[0][1:].split(None, 1)[0]
            args = dict(enumerate(word_eol[args_start:]))
            args_start += 1
    if command in ('lart','l'):
        action.bans = 'nihra'

    # Set target
    action.target = args[0].split(None,1)[0]

    if not valid_nickname(action.target) and not valid_mask(action.target):
        xchat.emit_print("Server Error", "Invalid target: %s" % action.target)
        return xchat.EAT_ALL

    if action.bans and not valid_nickname(action.target):
        xchat.emit_print("Server Error", "Ban types and lart can only be used with nicks, not with complete masks")
        return xchat.EAT_ALL

    if valid_mask(action.target):
        action.bans = 'f'
    
    if not action.bans:
        action.bans = 'h'

    # Find forward channel
    if command in forward_commands:
        action.forward_to = '$' + args[1].split(None,1)[0] # Kludge
        if not valid_channel(action.forward_to[1:]):
            xchat.emit_print("Server Error", "Invalid channel: %s" % action.forward_to[1:])
            return xchat.EAT_ALL

    # Check if target is there and schedule kick
    if command in kick_commands:
        if action.target.lower() not in [x.nick.lower() for x in action.context.get_list('users')]: 
            xchat.emit_print("Server Error", "%s is not in %s" % (action.target, action.channel)) 
            return xchat.EAT_ALL
        action.reason = args.get(1, 'Goodbye')
        action.actions.append('remove %(channel)s %(target_nick)s :%(reason)s')

    if command in ('m','mute'):
        action.banmode = 'q'

    if command in ban_commands:
        action.do_ban = True
        if 'n' in action.bans: action.actions.append('mode %(channel)s +%(banmode)s %(target_nick)s!*@*%(forward_to)s')
        if 'i' in action.bans: action.actions.append('mode %(channel)s +%(banmode)s *!%(target_ident)s@*%(forward_to)s')
        if 'h' in action.bans: action.actions.append('mode %(channel)s +%(banmode)s *!*@%(target_host)s%(forward_to)s')
        if 'r' in action.bans: action.actions.append('mode %(channel)s +%(banmode)s $r:%(target_name_bannable)s%(forward_to)s')
        if 'a' in action.bans: action.actions.append('mode %(channel)s +%(banmode)s $a:%(target_account)s%(forward_to)s')
        if 'f' in action.bans: action.actions.append('mode %(channel)s +%(banmode)s %(target)s%(forward_to)s')

    if command in ('u','unban'):
        action.do_unban = True

    if command == 'bans':
        action.do_bans = True
        action.needs_op = False

    return action.schedule()
xchat.hook_command('cs',cs,"For help with /cs, please read the comments in the script")

class Action(object):
    """A list of actions to do, and information needed for them"""
    def __init__(self, channel, me, context):
        self.channel = channel
        self.me = me
        self.context = context
        self.stamp = time.time()

        # Defaults
        self.deop = True
        self.needs_op = True
        self.do_ban = self.do_unban = self.do_bans = False
        self.banmode = 'b'
        self.reason = ''
        self.bans = ''
        self.actions = []
        self.resolved = True
        self.target = ''
        self.forward_to = ''
        self.timer = 0

    def schedule(self):
        """Request information and add ourselves to the queue"""
        pending.append(self)
        # Am I opped?
        self.am_op = False
        for user in self.context.get_list('users'):
            if user.nick == self.me and user.prefix == '@':
                self.am_op = True
                self.deop = False

        if self.needs_op and not self.am_op:
            self.context.command("chanserv op %s" % self.channel)

        # Find needed information
        if ('a' in self.bans or 'r' in self.bans) and valid_mask(self.target) and not self.target.startswith('$'):
            xchat.emit_print('Server Error', "Invalid argument %s for account/realname ban" % self.target)
            return xchat.EAT_ALL
        if self.do_ban or self.do_unban or self.do_bans:
            self.resolve_nick()
        else:
            self.target_nick = self.target

        if self.do_unban or self.do_bans:
            self.fetch_bans()

        run_pending()
        return xchat.EAT_ALL

    def resolve_nick(self, request=True):
        """Try to find nickname, ident and host"""
        self.target_nick = None
        self.target_ident = None
        self.target_host = None
        self.target_name = None
        self.target_account = None
        self.resolved = False

        if valid_mask(self.target):
            if self.target.startswith('$a:'):
                self.target_account = self.target[3:]
            elif self.target.startswith('$r:'):
                self.target_name = self.target[3:]
            else:
                self.target_nick, self.target_mask, self.target_host = re.split('[!@]', self.target)
            self.resolved = True
            return

        self.target_nick = self.target.lower()
        if self.target_nick in users:
            if users[self.target_nick].time < time.time() - 10:
                del users[self.target_nick]
                if request:
                    self.context.command('whois %s' % self.target_nick)
            else:
                self.target_ident = users[self.target_nick].ident
                self.target_host = users[self.target_nick].host
                self.target_name = users[self.target_nick].name
                self.target_name_bannable = re.sub('[^a-zA-Z0-9]', '?', self.target_name)
                self.target_account = users[self.target_nick].account
                self.resolved = True
                if 'gateway/' in self.target_host and self.bans == 'h' and self.do_ban:
                    # For gateway/* users, default to ident ban 
                    self.actions.append('mode %(channel)s +%(banmode)s *!%(target_ident)s@gateway/*%(forward_to)s')
                    self.actions.remove('mode %(channel)s +%(banmode)s *!*@%(target_host)s%(forward_to)s')
        else:
            if request:
                self.context.command('whois %s' % self.target_nick)

    def fetch_bans(self):
        """Read bans for a channel"""
        bans[self.channel] = []
        quiets[self.channel] = []
        collecting_bans.append(self.channel)
        self.context.command("mode %s +bq" % self.channel)

    def run(self):
        """Perform our actions"""
        kwargs = dict(self.__dict__.items())

        if self.do_bans:
            xchat.emit_print('Server Text', "Bans matching %s!%s@%s (r:%s, a:%s)" % 
                    (self.target_nick, self.target_ident, self.target_host, self.target_name, self.target_account))

        if self.do_unban or self.do_bans:

            for b in bans[self.channel]:
                if self.match(b):
                    if self.do_bans:
                        xchat.emit_print('Server Text', b)
                    else:
                        self.actions.append('mode %s -b %s' % (self.channel, b))

            for b in quiets[self.channel]:
                if self.match(b):
                    if self.do_bans:
                        xchat.emit_print('Server Text', b + ' (quiet)')
                    else:
                        self.actions.append('mode %s -q %s' % (self.channel, b))

        # Perform all registered actions
        for action in self.actions:
            self.context.command(action % kwargs)

        self.done()

    def done(self):
        """Finaliazation and cleanup"""
        # Done!
        pending.remove(self)

        # Deop?
        if not self.am_op or not self.needs_op:
            return

        for p in pending:
            if p.channel == self.channel and p.needs_op or not p.deop:
                self.deop = False
                break

        if self.deop:
            self.context.command("chanserv deop %s" % self.channel)

        # Schedule removal?
        if self.timer:
            action = Action(self.channel, self.me, self.context)
            action.deop = self.deop
            action.actions = [x.replace('+','-',1) for x in self.actions]
            action.target = action.target_nick = self.target_nick
            action.target_ident = self.target_ident
            action.target_host = self.target_host
            action.target_name = self.target_name
            action.target_name_bannable = self.target_name_bannable
            action.target_account = self.target_account
            action.resolved = True
            action.banmode = self.banmode
            xchat.hook_timer(self.timer * 1000, lambda x: x() and False, action.schedule)

    def match(self, ban):
        """Does a ban match this action"""
        if ban.startswith('$r:') and self.target_name:
            return ban2re(ban[3:]).match(self.target_name)
        elif ban.startswith('$a:') and self.target_account:
            return ban2re(ban[3:]).match(self.target_account)
        else:
            if '#' in ban:
                ban = ban[:ban.find('$#')]
            return ban2re(ban).match('%s!%s@%s' % (self.target_nick, self.target_ident, self.target_host))

def run_pending(just_opped = None):
    """Check all actions and run them if all information is there"""
    now = time.time()

    for p in pending:
        if p.channel == just_opped:
            p.am_op = True

        if p.target_nick in users and not p.resolved:
            p.resolve_nick(request = False)

        # Timeout?
        if p.stamp < now - 10:
            p.done()
            continue

        can_run = not (p.channel in collecting_bans and (p.do_unban or p.do_bans))
        if can_run and p.resolved and (p.am_op or not p.needs_op):
            p.run()

# Helper functions
def ban2re(data):
    return re.compile('^' + re.escape(data).replace(r'\*','.*').replace(r'\?','.') + '$')

_valid_nickname = re.compile(r'^[-a-zA-Z0-9\[\]{}`|_^\\]{0,30}$')
valid_nickname = lambda data: _valid_nickname.match(data)
_valid_channel = re.compile(r'^[#~].*') # OK, this is cheating
valid_channel = lambda data: _valid_channel.match(data)
_valid_mask = re.compile(r'^([-a-zA-Z0-9\[\]{}`|_^\\*?]{0,30}!.*?@.*?|\$[ar]:.*)$')
valid_mask = lambda data: _valid_mask.match(data)
 
# Data processing
def do_mode(word, word_eol, userdata):
    """Run pending actions when chanserv opped us"""
    ctx = xchat.get_context()
    if 'chanserv!' in word[0].lower() and '+o' in word[3] and ctx.get_info('nick') in word:
        run_pending(just_opped = ctx.get_info('channel'))
xchat.hook_server('MODE', do_mode)

class User(object):
    def __init__(self, nick, ident, host, name):
        self.nick = nick; self.ident = ident; self.host = host; self.name = name
        self.account = None
        self.time = time.time()
def do_whois(word, word_eol, userdata):
    """Store whois replies in global cache"""
    nick = word[3].lower()
    if word[1] == '330':
        users[nick].account = word[4]
    else:
        users[nick] = User(nick, word[4], word[5], word_eol[7][1:])
xchat.hook_server('311', do_whois)
xchat.hook_server('330', do_whois)
xchat.hook_server('314', do_whois) # This actually is a /whowas reply

def do_missing(word, word_eol, userdata):
    """Fall back to whowas if whois fails"""
    for p in pending:
        if p.target == word[3]:
            p.context.command('whowas %s' % word[3])
            break
xchat.hook_server('401', do_missing)

def do_endwas(word, word_eol, userdata):
    """Display error if nickname cannot be resolved"""
    for p in pending:
        if p.target == word[3]:
            xchat.emit_print("Server Error", "%s could not be found" % p.target)
            pending.remove(p)
xchat.hook_server('406', do_endwas)

def endofwhois(word, word_eol, userdata):
    """Process the queue after nickname resolution"""
    run_pending()
xchat.hook_server('318', endofwhois)
xchat.hook_server('369', endofwhois)

xchat.hook_server('482', lambda word, word_eol, userdata: xchat.emit_print('Server Error', '%s in %s' % (word_eol[4][1:], word[3])))

def do_ban(word, word_eol, userdata):
    """Process banlists"""
    channel, ban = word[3:5]
    if channel in collecting_bans:
        bans[channel].append(ban)
        return xchat.EAT_ALL
    return xchat.EAT_NONE
xchat.hook_server('367', do_ban)

def do_quiet(word, word_eol, userdata):
    """Process banlists"""
    channel, ban = word[3], word[5]
    if channel in collecting_bans:
        quiets[channel].append(ban)
        return xchat.EAT_ALL
    return xchat.EAT_NONE
xchat.hook_server('728', do_quiet)

def do_endban(word, word_eol, userdata):
    """Process end-of-ban markers"""
    channel = word[3]
    if channel in collecting_bans:
        return xchat.EAT_ALL
    return xchat.EAT_NONE
xchat.hook_server('368', do_endban)

def do_endquiet(word, word_eol, userdata):
    """Process end-of-quiet markers"""
    channel = word[3]
    if channel in collecting_bans:
        collecting_bans.remove(channel)
        run_pending()
        return xchat.EAT_ALL
    return xchat.EAT_NONE
xchat.hook_server('729', do_endquiet)

# Turn on autorejoin
xchat.command('SET -quiet irc_auto_rejoin ON')

def rejoin(word, word_eol, userdata):
    """Rejoin when /remove'd"""
    if word[0][1:word[0].find('!')] == xchat.get_info('nick') and len(word) > 3 and word[3][1:].lower() == 'requested':
        xchat.command('join %s' % word[2])
xchat.hook_server('PART', rejoin)

# Unban when muted
xchat.hook_server('404', lambda word, word_eol, userdata: xchat.command('quote cs unban %s' % word[3]))

# Convince chanserv to let me in when key/unban/invite is needed
xchat.hook_server('471', lambda word, word_eol, userdata: xchat.command('quote cs invite %s' % word[3])) # 471 = limit reached
xchat.hook_server('473', lambda word, word_eol, userdata: xchat.command('quote cs invite %s' % word[3]))
xchat.hook_server('474', lambda word, word_eol, userdata: xchat.command('quote cs unban %s' % word[3]))
xchat.hook_server('475', lambda word, word_eol, userdata: xchat.command('quote cs getkey %s' % word[3]))

def on_invite(word, word_eol, userdata):
    """Autojoin when chanserv invites us"""
    if word[0] == ':ChanServ!ChanServ@services.':
        xchat.command('join %s' % word[-1][1:])
xchat.hook_server('INVITE', on_invite)

def on_notice(word, word_eol, userdata):
    """Autojoin when chanserv unbans us or sent us a key"""
    if word[0] != ':ChanServ!ChanServ@services.':
        return
    if 'Unbanned' in word_eol[0]:
        xchat.command('JOIN %s' % word[6].strip()[1:-1])
    if 'key is' in word_eol[0]:
        xchat.command('JOIN %s %s' % (word[4][1:-1], word[-1]))
xchat.hook_server('NOTICE', on_notice)

xchat.emit_print('Server Text',"Loaded %s %s by Seveas <dennis@kaarsemaker.net>" % (__module_description__, __module_version__))
