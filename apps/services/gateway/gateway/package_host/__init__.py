"""The package host of WS-PKG (``project-docs/specs/package_host.md``).

``store.py`` is the one writer of ``package_install`` and
``package_contribution`` (PKG-5b part 1). ``sharing.py`` holds the one rule
of "another person sees this private install". The member purge and the
resolve of PKG-5b part 2 read that rule.

This package holds no route. PKG-8 adds the install route for Studio apps,
and PKG-12 adds it for apps made in chat. This file imports nothing, so a
caller can import ``sharing`` at no cost.
"""
