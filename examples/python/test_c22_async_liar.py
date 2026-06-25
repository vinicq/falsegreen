# falsegreen examples - C22: the async liar (opt-in, OFF by default).
#
# C22 flags an `async def test_*` that makes calls and asserts but never awaits
# the unit and never drives an event loop. Nothing actually runs, so the
# assertion checks an un-awaited coroutine and the test passes vacuously.
#
# C22 is off by default (it has a higher false-positive rate than the blocking
# codes). Enable it per project:
#
#     [severity]
#     C22 = "low"
#
# With C22 disabled (the default), this file reports nothing.
import asyncio


# BAD: the async test asserts but never awaits and never drives the loop.
async def test_c22_never_awaits():
    result = fetch()                   # C22 - coroutine created, never awaited
    assert result == 1

# CLEAN: the unit is awaited before the assertion.
async def test_c22_awaits_clean():
    result = await fetch()
    assert result == 1

# CLEAN: the test drives the loop itself with asyncio.run.
async def test_c22_drives_loop_clean():
    results = asyncio.run(gather())
    assert results == [1, 2]

# CLEAN: a plain synchronous test is never an async liar.
def test_c22_sync_clean():
    assert fetch() == 1
