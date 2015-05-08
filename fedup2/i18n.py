import gettext
t = gettext.translation("fedup2", "/usr/share/locale", fallback=True)
_ = t.lgettext
