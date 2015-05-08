# cli.py - CLI for the Fedora Upgrade tool
#
# Copyright (C) 2015 Red Hat Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Author: Will Woods <wwoods@redhat.com>

import os, sys, time, argparse, libmount, platform

from .logutils import log_setup, console_is_enabled_for
from .version import version as fedupversion
from .state import State
from .lock import PidLock, PidLockError
from .dnf_wrapper import DNFWrapper
from .clean import Cleaner
from .reboot import Bootprep, reboot, MAGIC_SYMLINK
from .plymouth import PlymouthOutput

import dnf.exceptions
from dnf.cli.output import progressbar

from .i18n import _

import logging
log = logging.getLogger("fedup2")

DEFAULT_DATADIR = '/var/cache/system-upgrade'

def init_parser():
    # === toplevel parser ===
    p = argparse.ArgumentParser(
        usage='%(prog)s <status|download|media|reboot|clean> [OPTIONS]',
        description=_('Prepare system for upgrade.'),
        epilog=_("Use '%(prog)s <ACTION> --help' for more info."),
    )
    # === basic options ===
    p.add_argument('-v', '--verbose', action='store_const', dest='loglevel',
        const=logging.INFO, help=_('print more info'))
    p.add_argument('-d', '--debug', action='store_const', dest='loglevel',
        const=logging.DEBUG, help=_('print lots of debugging info'))
    p.set_defaults(loglevel=logging.WARNING)

    p.add_argument('--log', default='/var/log/fedup2.log',
        help=_('where to write detailed logs (default: %(default)s)'))

    # === hidden options. FOR DEBUGGING ONLY. ===
    p.add_argument('--logtraceback', action='store_true', default=False,
        help=argparse.SUPPRESS)
    p.add_argument('--sleep-forever',action='store_const', const='sleep',
        dest='action', help=argparse.SUPPRESS)

    # === subparsers for commands ===
    # TODO: i18n
    cmds = p.add_subparsers(dest='action',
        title='Actions', metavar='', prog='fedup2',
    )
    cmds.add_parser('status',
        help='show upgrade status',
        description='Show the upgrade preparation status.',
    )
    d = cmds.add_parser('download',
        usage='%(prog)s <VERSION> [OPTIONS]',
        help='download data for upgrade',
        description='Download data and boot images for upgrade.',
    )
    cmds.add_parser('resume', aliases=('retry','refresh'),
        help='resume or retry download',
        description='Resume a previously-started download.',
    )
    cn = cmds.add_parser('cancel',
        help='cancel download',
        description='Cancel a previously-started download.',
    )
    cmds.add_parser('reboot',
        help='reboot and start upgrade',
        description='Reboot system and start upgrade.',
    )
    c = cmds.add_parser('clean',
        help='clean up data',
        description='Clean up data written by this program.',
    )

    # === options for 'fedup2 download' ===
    # Translators: This is for '--network [VERSION]' in --help output
    d.add_argument("version", metavar=_('VERSION'), type=VERSION,
        help=_('version to upgrade to (a number or "rawhide")'))
    d.add_argument('--datadir', type=valid_datadir, default=DEFAULT_DATADIR,
        help=_('set download dir (default: %(default)s)'))
    d.add_argument('--distro-sync', action='store_true', default=False,
        help=_('install packages from new release even if they are older'))

    d.add_argument('--nogpgcheck', action='store_true', default=False,
        help=_('disable GPG signature checking (not recommended!)'))
    d.add_argument('--add-install', metavar='<PKG-PATTERN|@GROUP-ID>',
        action='append', dest='add_install', default=[],
        help=_('extra item to be installed during upgrade'))

    # === options for 'fedup2 cancel' ===
    cn.add_argument('--no-clean', action='store_true', default=False,
        help="keep all downloaded data")

    # === options for 'fedup2 clean' ===
    c.add_argument('clean',
        help=_('what to clean up')+' (%(choices)s)',
        choices=('packages','metadata','misc','all'),
    )

    # === hidden 'system-upgrade' command that actually does the upgrade
    u = cmds.add_parser('system-upgrade', help=argparse.SUPPRESS)
    u.add_argument('--testing', action='store_true', default=False,
        help=argparse.SUPPRESS)
    u.add_argument('--reboot', action='store_true', default=False,
        help=argparse.SUPPRESS)
    u.add_argument('--no-plymouth', action='store_false', default=True,
        dest='plymouth', help=argparse.SUPPRESS)

    return p

def get_distro():
    dists = ('fedora',)
    distro, version, _ = platform.linux_distribution(supported_dists=dists)
    return distro, version

def VERSION(arg):
    if arg.lower() == 'rawhide':
        return 'rawhide'

    distro, version = get_distro()
    if not distro:
        raise argparse.ArgumentTypeError(_("unsupported distro %r") % distro)
    try:
        floatver = float(version)
    except ValueError:
        raise argparse.ArgumentTypeError(_("can't determine system version"))
    if float(arg) <= floatver:
        raise argparse.ArgumentTypeError(_("version must be higher than %s")
                                         % version)
    return arg

def valid_datadir(datadir):
    '''Check the location of --datadir to make sure it's usable for upgrades.'''
    def err(msg): raise argparse.ArgumentTypeError(" ".join((datadir,msg)))
    fs = libmount.Table('/proc/self/mountinfo').find_mountpoint(datadir)
    if fs.is_netfs():
        err(_("is on a network filesystem"))
    if fs.is_pseudofs():
        err(_("is on a temporary filesystem"))
    if datadir == DEFAULT_DATADIR:
        return datadir
    if not os.path.exists(datadir):
        err(_("does not exist"))
    if not os.path.isdir(datadir):
        err(_("is not a directory"))
    if os.listdir(datadir):
        err(_("is not empty"))
    # looks good!
    return datadir

class Cli(object):
    """The main CLI object."""
    def __init__(self):
        self.parser = init_parser()
        self.pidfile = None
        self.args = None
        self.state = None
        self.exittype = "cleanly"
        self.has_lock = False
        self.resumed = False
        self.plymouth = None

    def error(self, msg, *args):
        log.error(msg, *args)
        raise SystemExit(2)

    def message(self, msg, *args):
        log.info("message:"+msg, *args)
        if console_is_enabled_for(logging.INFO):
            return
        if args: msg = msg % args
        print(msg)
        if self.plymouth:
            self.plymouth.message(msg)

    @staticmethod
    def progressbar(count, total, name=None):
        progressbar(count, total, name)

    def parse_args(self):
        assert self.parser
        self.args = self.parser.parse_args()

    def read_state(self):
        self.state = State()

    def check_args(self):
        """Check (and fix up) the args we got from parse_args."""
        assert self.args

        # An action is required
        if not self.args.action:
            self.parser.error(_('no action given.'))

    def check_state(self):
        """Check the system state to see if it's compatible with this action"""
        assert self.args
        assert self.state

        # Can't reboot if we're not actually ready to upgrade
        if self.args.action == 'reboot' and not self.state.upgrade_ready:
            if self.state.upgrade_target:
                self.error(_("download incomplete"))
            else:
                self.error(_("system not prepared for upgrade"))

        # Can't resume/cancel unless something is in progress
        if self.args.action == 'resume' and not self.state.cmdline:
            self.parser.error(_("no upgrade to resume"))
        if self.args.action == 'cancel' and not self.state.cmdline:
            self.parser.error(_("no upgrade to cancel"))

        # Can't start a new download if there's one in progress
        if self.args.action == 'download' and self.state.cmdline:
            # Oh, you just repeated the last command.. let's resume.
            if sys.argv[1:] == self.state.cmdline:
                self.args.action = 'resume'
            else:
                self.message(_("ERROR: interrupted upgrade detected."))
                self.status()
                raise SystemExit(2)

    def check_perms(self):
        if os.getuid() != 0:
            self.error(_("you must be root to do this."))

    def open_logs(self):
        try:
            log_setup(self.args.log, self.args.loglevel)
        except IOError as e:
            self.error(_("Can't open logfile '%s': %s"), self.args.log, e)
        log.info("fedup2 %s starting at %s", fedupversion, time.asctime())
        log.info("argv: %s", str(sys.argv))

    def get_lock(self):
        try:
            self.pidfile = PidLock("/var/run/fedup2.pid")
            self.has_lock = True
        except PidLockError as e:
            self.error(_("already running as PID %s") % e.pid)

    def free_lock(self):
        assert self.pidfile
        self.pidfile.remove()
        self.has_lock = False

    def status(self):
        self.message(self.state.summarize())

    def download(self):
        if not self.resumed:
            # new run - write initial state
            with self.state as state:
                distro, version = get_distro()
                state.current_system = "%s %s" % (distro, version)
                state.upgrade_target = "%s %s" % (distro, self.args.version)
                state.releasever = self.args.version
                state.datadir = self.args.datadir
                state.cmdline = sys.argv[1:]

        # set up downloader
        dl = DNFWrapper(self)
        dl.setup()
        with self.state as state:
            state.cachedir = dl.cachedir

        self.message(_("setting up package repos..."))
        enabled_repos = dl.read_metadata()
        with self.state as state:
            state.enabled_repos = ' '.join(enabled_repos)

        self.message(_("looking for upgrades..."))
        pkglist = dl.find_upgrade_packages(distro_sync=self.args.distro_sync)
        with self.state as state:
            state.pkgs_total = len(pkglist)
            state.size_total = sum(p.size for p in pkglist)
            state.write_packagelist(p.localPkg() for p in pkglist)
            state.clean_datadir()
        # TODO: sanity-check pkglist - does something provide kernel?

        self.message(_("starting download..."))
        dl.download_packages(pkglist)

        self.message(_("testing upgrade transaction..."))
        # FIXME: handle and print problems
        dl.do_transaction(test=True)

        # we're done! mark it, dude!
        with self.state as state:
            state.upgrade_ready = 1

    def upgrade(self):
        # avoid looping - remove magic symlink
        self.clean("misc")
        # set up plymouth output, if requested
        if self.args.plymouth:
            self.plymouth = PlymouthOutput()
            self.plymouth.set_mode("updates")
            self.plymouth.progress(0)
        self.message(_("Starting upgrade to %s; this could take a while."),
                      self.state.upgrade_target)
        # reset self.args to what they were during download
        testing = self.args.testing
        reboot = self.args.reboot
        self.resume()
        try:
            # create transaction using cached metadata and saved args
            upg = DNFWrapper(self)
            upg.setup(cacheonly=True)
            upg.read_metadata()
            upg.find_upgrade_packages(distro_sync=self.args.distro_sync)
            upg.do_transaction(test=testing)
        except Exception as e:
            self.message(_("Upgrade failed: %s", str(e)))
            time.sleep(5) # let the user see the error
            raise
        else:
            self.message(_("Upgrade finished! Cleaning up and rebooting."))
            self.plymouth = None
            if not testing:
                self.clean("all")
        finally:
            if reboot:
                reboot()

    def reboot(self):
        r = Bootprep(self)
        r.prep_mounts()
        r.prep_boot()
        reboot()

    def clean(self, what):
        cleaner = Cleaner(self)
        if what == 'all':
            # NOTE: metadata is system-owned, so leave it alone by default
            self.clean('packages')
            self.clean('misc')
        elif what == 'packages':
            self.message(_("Removing downloaded packages..."))
            cleaner.clean_packages()
        elif what == 'metadata':
            self.message(_("Removing metadata..."))
            cleaner.clean_metadata()
        elif what == 'misc':
            cleaner.clean_misc()
        else:
            raise AssertionError("invalid 'clean' arg")

    def sleep(self):
        print("pid %u, now going to sleep forever!" % os.getpid())
        while True:
            time.sleep(31337)

    def cancel(self):
        log.info("cancelling upgrade")
        if self.args.no_clean:
            self.clean('misc')
        else:
            self.clean('all')
        with self.state as state:
            state.clear()

    def resume(self):
        log.info("resuming with argv: %s", self.state.cmdline)
        self.args = self.parser.parse_args(self.state.cmdline)
        self.resumed = True

    def main(self):
        self.parse_args()
        self.check_args()
        self.read_state()
        self.check_state()

        if self.args.action == 'status':
            self.status()
            return

        self.check_perms()
        self.open_logs()
        self.get_lock()

        try:
            log.info("doing action %r", self.args.action)
            if self.args.action in ('resume', 'retry', 'refresh'):
                self.resume() # updates self.args
            if self.args.action == 'download':
                self.download()
            elif self.args.action == 'clean':
                self.clean(self.args.clean)
            elif self.args.action == 'reboot':
                self.reboot()
            elif self.args.action == 'sleep':
                self.sleep()
            elif self.args.action == 'cancel':
                self.cancel()
            elif self.args.action == 'system-upgrade':
                self.upgrade()
        except KeyboardInterrupt:
            self.message(_("exiting on keyboard interrupt"))
            raise SystemExit(1)
        except dnf.exceptions.DownloadError as e:
            self.error(_("Download failed: %s"), e)
        except Exception:
            log.info("Exception:", exc_info=True)
            self.exittype = "with unhandled exception"
            raise
        finally:
            self.free_lock()
            self.status()
            log.info("fedup2 %s exiting %s at %s",
                     fedupversion, self.exittype, time.asctime())
