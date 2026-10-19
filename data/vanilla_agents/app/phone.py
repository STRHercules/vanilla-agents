"""V-040: Phone access. Off by default: the server answers this PC only (127.0.0.1). When the owner switches "Phone access" on, a second
listener opens on this PC's home-network address (never 0.0.0.0) so his phone can open phone.html. Over the network EVERY call needs the
window key (the link the Phone section shows carries it); without it the phone page shows nothing. Anyone with that link can approve,
pause and verify, so the window says so. Saved in your settings file (settings.py EXTRA "phone")."""
import ipaddress
import socket

from . import settings as settings_mod


def lan_ip(probe=None):
    """This PC's address on the home network, or None. Uses a UDP 'connect' (sends nothing) to learn which interface is used."""
    try:
        if probe is not None:
            ip = probe()
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("10.255.255.255", 1))
                ip = s.getsockname()[0]
            finally:
                s.close()
    except (OSError, ValueError):
        return None
    return ip if lan_ok(ip) else None


def lan_ok(ip):
    """Only a private (home/office network) IPv4 address, never loopback, 'any' (0.0.0.0) or a public address."""
    try:
        a = ipaddress.ip_address(str(ip))
    except ValueError:
        return False
    return a.version == 4 and a.is_private and not a.is_loopback and not a.is_unspecified and not a.is_link_local


def bind_hosts(enabled, ip):
    """Where the server listens: always 127.0.0.1, plus the home-network address only when Phone access is on and one was found."""
    return ["127.0.0.1"] + ([ip] if enabled and lan_ok(ip) else [])


def enabled(settings_file):
    return settings_mod.load_extra(settings_file, "phone").get("enabled") is True


def set_enabled(settings_file, on):
    if not isinstance(on, bool):
        raise ValueError("enabled must be true or false")
    settings_mod.save_extra(settings_file, "phone", {"enabled": on})
