# The files under examples/python/ are illustrative scan targets, not a runnable
# test suite. Each is named test_*.py so that `falsegreen examples/` treats it as
# a collected pytest file and flags the BAD samples (the scanner reads the syntax
# tree, it never imports or runs them). Their bodies call helpers that do not
# exist on purpose, so pytest must not try to collect or import them - otherwise
# `pytest` at the repo root would fail on the missing names.
#
# This conftest keeps pytest away from the samples while leaving them fully
# visible to the falsegreen scanner.
collect_ignore_glob = ["*"]
