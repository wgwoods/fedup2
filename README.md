# fedup2 - DNF-based Fedora upgrader

This is fedup2, a (prototype) successor to [fedup][] based on [DNF][].

It uses [systemd][]'s [Offline System Updates][systemupdates] hook to run the
upgrade with your system _mostly_ offline, rather than using special boot
images to run the upgrade in a special upgrade environment.

This is just a prototype - it was mostly written as a proof-of-concept and as
a way to flesh out what needs to be added to the DNF API to make this work.

## Requirements

Fedora 21 or newer with `python3`, `python3-libmount`, `python3-dnf`.

(It might work with python2 but there's no `python-libmount` in Fedora yet..)

## Example use

### downloading a new version

    $ fedup2 download 22

### checking the progress of an interrupted upgrade

    $ fedup2 status
    Download of Fedora 22 is 50.0% complete (987 M/1.9 G)
    Use 'fedup2 resume' to resume downloading.
    Use 'fedup2 cancel' to cancel the upgrade.

### starting the upgrade

    $ fedup2 reboot

(The system will start the upgrade after the reboot, then reboot again when
the upgrade is finished.)

## `fedup2 --help`
```
usage: fedup2.py <status|download|media|reboot|clean> [OPTIONS]

Prepare system for upgrade.

optional arguments:
  -h, --help            show this help message and exit
  -v, --verbose         print more info
  -d, --debug           print lots of debugging info
  --log LOG             where to write detailed logs (default:
                        /var/log/fedup2.log)

Actions:

    status              show upgrade status
    download            download data for upgrade
    resume (retry, refresh)
                        resume or retry download
    cancel              cancel download
    reboot              reboot and start upgrade
    clean               clean up data
    system-upgrade      ==SUPPRESS==

Use 'fedup2.py <ACTION> --help' for more info.
```

(Yeah, I know about the `==SUPPRESS==` thing. You aren't supposed to use the
`system-upgrade` action manually; use `reboot` and let your system handle it.)

[fedup]: https://github.com/rhinstaller/fedup
[dnf]: https://github.com/rpm-software-management/dnf
[systemd]: http://www.freedesktop.org/wiki/Software/systemd/
[systemupdates]: http://www.freedesktop.org/wiki/Software/systemd/SystemUpdates/
