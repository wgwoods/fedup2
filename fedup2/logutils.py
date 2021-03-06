# logutils.py - logging utility functions
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

import logging, logging.config

levelsyms = {
    logging.DEBUG:   '(DD)',
    logging.INFO:    '(II)',
    logging.WARNING: '(WW)',
    logging.ERROR:   '(EE)',
    logging.CRITICAL:'(CC)',
    logging.FATAL:   '(FF)',
}

from dnf.logging import DDEBUG

class FedupFormatter(logging.Formatter):
    def format(self, record):
        record.reltime = float(record.relativeCreated)/1000
        record.levelsym = levelsyms.get(record.levelno, '(--)')
        return logging.Formatter.format(self, record)

def console_is_enabled_for(level):
    log = logging.getLogger("fedup2")
    for h in log.handlers:
        if h.name == 'console':
            return h.level <= level
    return False

def log_setup(debug_log="/var/log/fedup2.log", console_level='WARNING'):
    '''Set up logging:
        - Send copious debugging information to debug_log (/var/log/fedup2.log)
        - Messages of console_level (WARNING) or higher go to the console
    '''
    logging.config.dictConfig({
        'version':1,
        'loggers':{
          'fedup2':{
            'level':'DEBUG',
            'handlers':['debuglog','console'],
          },
          'dnf':{
            'level':DDEBUG,
            'handlers':['debuglog'],
          }
        },
        'handlers':{
          'debuglog':{
            'class':'logging.FileHandler',
            'level':DDEBUG,
            'formatter':'debuglog',
            'filename':debug_log,
          },
          'console':{
            'class':'logging.StreamHandler',
            'level': console_level,
            'formatter':'console',
          },
        },
        'formatters': {
          'debuglog': {
            '()':FedupFormatter,
            'format':"[%(reltime)10.3f] %(levelsym)s %(name)s:%(funcName)s() "
                     "%(message)s"
          },
          'console':{
            'format':"%(name)s %(levelname)s: %(message)s"
          },
        },
    })
