# False positives in tests: the guide, explained

## First, what all of this is

An automated test is a quality inspector that works on its own. You write it
once, and from then on it checks the program every time someone touches the code.
When the inspector approves, we say the test is "green". When it rejects, "red".

A false positive is the inspector who stamps "approved" without ever looking at
the product. The test goes green, hands you a feeling of safety, and protects
nothing. That is worse than having no test at all, because a lying test makes you
trust broken code and ship it to production.

This document collects the most common ways to build one of these lying
inspectors, explains each with a real-world example, shows why it fools you, and
teaches you how to confirm a test is worth keeping.

## The rule that holds for every case

A test is only useful if it fails when the code breaks. If you have never seen a
test go red, you do not know whether it tests anything. That is the foundation.
Everything below is a variation on tests that never fail, fail for the wrong
reason, or check the wrong thing.

The examples are in Python, but the trap is the same everywhere. What matters is
understanding the trap, not the code.

---

# Family A: the test never checks anything

These are the cases where the check simply does not happen. The inspector walks
into the factory and leaves without opening a single box.

## 1. The check sits inside an "if" that may not happen

In the real world: picture an airport guard who only searches a bag "if the
person looks suspicious". On a day when nobody looks suspicious, he opens no bags
and declares "all clear". He checked nothing, he just had no one to bother.

In code, the check (the `assert`) sits inside an `if` or a `for` loop. If the
condition is false, or the list comes back empty, the line that checks never
runs. The test passes without having verified a thing.

```python
# Lying: if "result" is empty, the assert never runs
if result:
    assert result.status == "ok"
```

How to confirm it works: drop the `if`. Check directly what you want to prove. If
the list can be empty, first confirm it has items, then check the items.

```python
assert result is not None
assert result.status == "ok"
```

## 1b. The check sits after a line that ends the test early

In the real world: the inspector writes "approved" at the bottom of the form, then
stamps "REJECTED" above it and walks out. Nobody ever reads the line below the
stamp. The bottom of the form might as well be blank.

In code, the `assert` comes after a `return`, a `raise`, or a `pytest.fail()` in
the same block. Those lines end the test on the spot, so the check below them is
dead code that never runs. The test passes because it stops before it would have
checked anything.

```python
# Lying: the test returns before it ever reaches the assert
def test_x():
    result = compute()
    return
    assert result == 42   # never runs
```

How to confirm it works: delete the early `return`/`raise`, or move the check
above it. If the early exit is on purpose (a guard), put it inside an `if` so it
only fires when it should, and keep the real check on the normal path.

```python
def test_x():
    result = compute()
    assert result == 42
```

## 1c. Every check in the test is behind an "if", so one might run, or none

In the real world: every inspection on the checklist starts with "if you have
time". On a busy day there is never time, so the whole checklist is skipped and
the form still comes back signed.

In code, the test has checks, but none of them runs on its own: each `assert` is
inside an `if`, and there is no check at the top level. If the condition is false
at runtime, the test reaches the end having verified nothing and still passes.
This is worse than a single guarded check (case 1), because the whole test can go
vacuous, not just one line.

```python
# Lying: if "cond" is false, no assert runs at all
def test_x(cond):
    if cond:
        assert a() == 1
    else:
        log("nothing checked here")
```

How to confirm it works: make at least one check run on every path. Put a real
assertion at the top level, or assert in both the `if` and the `else`, so a check
fires no matter which branch is taken.

```python
def test_x(cond):
    result = run(cond)
    assert result.ok          # runs no matter what
    if cond:
        assert result.value == 1
```

## 2. The test checks nothing (no assertion at all)

In the real world: the inspector turns the machine on, sees it powered up, and
stamps "approved". He never waited to see whether it makes the right product.
"Turns on" is not "works".

In code, the test calls the function and stops there. It only proves the program
did not blow up. A file converter could return a blank page and this test would
stay green all the same.

```python
# Lying: calls the function and does not check the result
def test_converts():
    convert_pdf(file)
```

How to confirm it works: every test ends by checking the result, the effect, or
the final state. If the function exists to produce a file, check the file.

## 3. The check sits inside a "try" that swallows the error

In the real world: a smoke detector with the speaker wire cut. Inside, the alarm
does go off, but no sound comes out. The house burns and nobody is warned.

In code, the check sits inside a `try`, and the `except` catches any error and
moves on in silence. When the check fails, it raises an error; the `except` grabs
that error and throws it away. The test never fails, no matter what happens.

```python
# Lying: the check's error is caught and discarded
try:
    assert response["code"] == "error"
except Exception:
    pass
```

How to confirm it works: tests do not wrap the check in `try/except`. If the goal
is to prove the code raises an error on purpose, say so directly, naming the exact
error type you expect:

```python
with pytest.raises(ValueError):
    parse(invalid_input)
```

## 4. The test never runs, because it was not collected

In the real world: the inspector was assigned to the wrong line. He spends the
whole day carefully checking a conveyor belt that is switched off. The report
reads "no defects found", and it is true: he saw no defect because he saw no
product.

In code, pytest only runs functions whose name starts with `test`. If you write
`def check_total()` instead of `def test_total()`, the function exists, has an
`assert` inside, looks like a test, but pytest never calls it. It vanishes from
the count without a sound. Variations on the same mistake: a test hidden inside
another function, or a `TestSomething` class with an `__init__` method (pytest
skips test classes that have a constructor). The ending is always the same: zero
failures, because zero execution.

```python
# Lying: the name does not start with "test", pytest never runs this
def check_total():
    assert total([1, 2]) == 3
```

How to confirm it works: look at the count of collected tests before and after you
write the new test (`pytest --collect-only`). If you add a test and the number
does not rise, it is not being run. Follow the tool's convention: `test_` at the
start of the function, `Test` at the start of the class, no `__init__` in the test
class.

---

# Family B: the check exists, but it is weak or always true

Here the inspector does open the box, but his check accepts almost anything.

## 5. The check is always true anyway

In the real world: the inspector asks "is the sky the sky?" and writes down "yes,
approved". The answer is always yes, no matter what is in the box. The check has
no relationship to the product.

In code, these are checks that do not depend on what the program did. They pass by
construction.

```python
# Lying: none of these lines depend on the code under test
assert True
assert result or True
```

How to confirm it works: the check compares against a concrete expected value.
The quick test: if you delete the program's logic and the test stays green, it was
not testing the logic.

## 6. The check only verifies that "something came back"

In the real world: checking that the box "has something inside" without looking at
what it is. A rock arrived instead of the phone? There is something inside, so it
is approved. Another version: searching for the word "ok" in the report and
accepting any text that contains it, including "not ok" or "broken token".

In code, the check accepts any value that is not empty, or searches for a fragment
of text that is too loose. A wrong output passes as long as it is not blank.

```python
# Lying: passes with anything that is not empty
assert result
assert len(output) > 0
assert "ok" in str(response)
```

How to confirm it works: check the exact content, not its presence. The more
specific the expected value, the less room for an error to slip through.

```python
assert response["status"] == "ok"
assert html.count("<p>") == 3
```

## 7. Comparing a thing to itself

In the real world: checking whether the scale is right by weighing the same sack
twice and comparing one weight to the other. Of course they match. You did not
compare against a reference weight, you compared the sack to itself.

In code, both sides of the `==` come from the same source. Sometimes it is obvious
(`assert x == x`), sometimes disguised: you call the same function on both sides,
or sort the same list on both sides and compare. The check is true by
construction, whatever the function returns.

```python
# Lying: both sides come from the same call
assert format(date) == format(date)
```

How to confirm it works: one side is the expected value, fixed, written by hand.
The other is what the code produced. If both sides run the same code, no
comparison happened.

```python
assert format(date) == "2026-06-02"
```

## 8. Comparing a fractional (decimal) number with exact equality

In the real world: demanding that the scale read exactly 0.3000000 when the sum of
the parts comes to 0.30000001 because of rounding. You reject (or approve) over
dust on the scale, not over the real weight.

In code, computers store decimal numbers with a tiny imprecision. That is why
0.1 + 0.2 is not exactly 0.3 to the machine. Comparing with exact equality makes
the test pass on one machine and fail on another, with no real bug involved.

```python
# Lying: fails on rounding, not on a calculation error
assert total == 0.3
```

How to confirm it works: compare decimals with a tolerance. Leave exact equality
for text, integers, and structure.

```python
assert total == pytest.approx(0.3, abs=1e-9)
```

## 9. Accepting any error instead of the right one

In the real world: a detector that goes off for anything, smoke, shower steam,
someone cooking. When it beeps, you do not know whether it is a fire or the rice
is done. The alarm loses its meaning.

In code, you want to prove the function rejects an invalid input and raises an
error. But you ask only for `Exception`, the most generic type there is. Now any
error satisfies the test, including one that has nothing to do with what you meant
to check: a misspelled variable name raises a `NameError`, the test catches that
`NameError` and goes green, without ever exercising the real validation.

```python
# Lying: accepts any error, even a typo in the test itself
with pytest.raises(Exception):
    withdraw(account, negative_amount)
```

How to confirm it works: name the exact error type you expect and, where it makes
sense, check the message too. That way the test only passes for the right reason.

```python
with pytest.raises(ValueError, match="amount must be positive"):
    withdraw(account, negative_amount)
```

---

# Family C: the test checks itself, not the program

These are the most treacherous. The test looks sophisticated, but deep down it is
talking to its own reflection.

## 10. Replacing the very part that should be tested with a stunt double

First, a definition: in tests, a "mock" (or stunt double) is a fake part we put in
place of something hard to use for real, like the network or a database. Useful at
the edges. A disaster in the core.

In the real world: testing a car by putting a dummy where the engine goes and
pushing the car by hand. Of course it "moves". But you tested your push, not the
engine.

In code, you replace with a double the very function the test should check. The
test confirms the double returns what you told it to return. When the real code
breaks, the test stays green.

```python
# Lying: the target of the test was swapped for a fake part
mocker.patch("store.calculate_total", return_value=100)
assert calculate_total(cart) == 100
```

How to confirm it works: use a double only at the edges (network, disk, clock,
external services). The part the test investigates runs for real. In an
integration test, not even at the edges: that level exists to hit the real
program.

## 11. Checking exactly the value you put into the double

In the real world: you write "the answer is 42" on a slip of paper, tuck it in
your pocket, and minutes later pull it out to check whether it says 42. It will
always match. You proved nothing about the actual calculation.

In code, this is the variation of the previous item. You configure the double to
return a value and then check that same value. The data never passed through any
of the program's logic.

How to confirm it works: the check lands on the result of something the code
actually transformed, not on the echo of what the double returned.

## 12. Re-implementing the program's logic inside the test

In the real world: to check the student's arithmetic, the grader redoes the sum
using the exact same wrong formula the student used. Both reach the same crooked
number, and the grader approves. The formula was never questioned, only repeated.

In code, instead of writing the expected result by hand, you compute it in the
test with the same logic that lives inside the program. If the program's formula
has a bug, your copy in the test has the same bug, and the two agree on the wrong
value. The test goes green defending the bug.

```python
# Lying: the expected value is computed with the same math the program does
expected = price * 1.1
assert calculate_with_tax(price) == expected
```

How to confirm it works: the expected value is a concrete number, chosen by
someone who knows what the answer should be. For a price of 100 with 10% tax,
write 110 by hand. If the program returns 110, it passes. If the internal formula
turns into something wrong, the test fails right away.

```python
assert calculate_with_tax(100) == 110
```

## 13. A typo in the assertion command

In the real world: a test button wired to nothing. You press it, the red light
does not come on, and you conclude it passed. Except the button was never
connected to anything.

In code, this one is specific to mocking tools. When you misspell the name of the
command that checks a call, the tool does not complain: it creates an empty command
that accepts everything and verifies nothing. The test always passes.

```python
# Lying: the real method is assert_called_once_with; misspelled, it becomes
# an empty attribute that checks nothing
mock_send.assert_called_once   # no parentheses and incomplete name
```

How to confirm it works: create the double with the `autospec` option. Then any
wrong command name raises an error on the spot, instead of becoming a silent pass.

```python
mock_send = mocker.patch("notification.send", autospec=True)
mock_send.assert_called_once_with(recipient, body)
```

## 14. The answer key is generated by the code itself

In the real world: the student takes the exam and, at grading time, writes the
answer key by copying their own answers. The grade comes out 100% every time,
including the questions they got wrong.

In code, this happens two ways. First: you take the expected result from the same
function you are testing, so the two err together. Second, the "snapshot" case: the
test compares the output against a reference file, but if that file does not exist,
the test itself creates it with the current output and passes on the same run.
Today's bug becomes tomorrow's "correct".

```python
# Lying: creates the reference from the current output and passes right away
if not golden.exists():
    golden.write(output)
```

How to confirm it works: the expected value comes from an independent source,
written by hand or reviewed by a person. Creating the reference file is a separate,
manual step, and the reference is saved and reviewed. When it changes, the diff
shows up for someone to look at before approving.

---

# Family D: green depends on outside factors

Here the test does check the program, but the result depends on things that should
not matter.

## 15. The test only passes if another test ran first

In the real world: a magic trick that only works because the assistant hid the card
before the show. Alone on stage, without the assistant, the magician fails. The
success was borrowed.

In code, the test depends on state another test left behind (a file, a database
record, a shared variable). Running the whole suite in a certain order, it passes.
Running alone, or in a different order, it fails.

How to confirm it works: each test builds its own scenario from scratch and cleans
up what it dirtied at the end. Run the test in isolation and run the suite in a
shuffled order. If it only passes with company, there is a hidden dependency that
needs to go.

## 16. The result depends on time, chance, or waiting

In the real world: a stopped clock is right twice a day. A test like this passes
sometimes and fails other times, depending on the hour, the luck of a draw, or a
pause that gave enough time or did not.

In code, the test reads the current time, uses an uncontrolled random number,
depends on the order items show up in, or uses a fixed pause ("wait 2 seconds")
hoping the work has finished. When it passes by luck, it is a false positive. When
it fails with no bug, it is a false alarm, and the team learns to ignore the red.
Both corrode trust in the suite.

How to confirm it works: control these factors. Freeze the time instead of reading
the system clock. Set the seed for the draw. Sort the list before comparing. Swap
the fixed pause for a wait that ends when the condition is met.

## 17. "Skipping" the test to hide a real failure

In the real world: sweeping the dirt under the rug. The floor looks clean, but the
dirt is still there, now invisible.

In code, "skipping" a test (`skip`) exists for a legitimate reason: the environment
lacks what it needs (a program not installed, an operating system that does not
support the feature). The abuse is using the skip to hide a real error. The test
leaves red and turns yellow, and the defect drops off the radar.

```python
# Lying: uses "skip" to sweep away a real error
try:
    run()
except Exception:
    pytest.skip("broke")
```

How to confirm it works: skip only on a clear, visible environment condition, with
a written reason (for example, "Linux only" or "needs Tesseract installed"). Never
skip because of a runtime error. And make sure there is at least one environment
where the test actually runs, otherwise nobody ever runs it.

---

# Family E: the test passes, but checks the wrong thing

This is the most dangerous case of all, and the hardest to see. The test runs,
fails when the code breaks, checks a real result, and is still wrong. The problem
is not the mechanics. It is the answer key. Someone wrote down as "correct" a
result that is not what the program should deliver.

## 18. The expected value contradicts what the code should do

In the real world: the student memorizes the wrong answer and writes that same
answer on the key. On the exam they "get right" what they agreed with themselves,
score full marks, and go through life believing 7 times 8 is 54. The exam passed,
but it measures the wrong belief, not the right arithmetic.

In code, the test was written by looking at what the function returns today, not at
what it should return. If the function was born with a bug, the test records that
bug as the expected value. From then on the test protects the defect: the day
someone fixes the function, the test fails, and the person is pushed to undo the fix
so the green comes back. The test, which should expose the error, ends up defending
it.

```python
# Lying: calculate_freight(150) returns 14.9 due to a rounding bug;
# the rule says it should return 15.0, but the test records the bug as expected
assert calculate_freight(150) == 14.9
```

How to confirm it works: the expected value comes from the rule, not from the run.
Before writing the `assert`, answer outside the code how much that should be, from
the requirement, the spec, or someone who knows the domain. Compare that number
with what the function returns. If they match, good. If they do not, either the code
is wrong or the test is, and you just found a real problem instead of burying it.
The source of truth is the business rule. The test exists to hold the code
accountable, never to copy what it already does.

---

# Additional patterns: beyond the eighteen cases

The eighteen cases above cover the core families. The patterns below are extensions — tighter or more specific versions of the same traps — that falsegreen checks for separately because they appear often enough in real test suites to warrant their own code.

## CC. The commented-out assert

An `assert` that was disabled by commenting it out. The test still runs and still passes, but the check is gone. This is usually the mark of a test that was silenced because it started failing, rather than being fixed.

```python
# assert response.status_code == 200   # was failing, silenced
assert response is not None  # only this runs
```

Fix it or delete the comment. A commented-out assertion is a decision to stop checking something — make that decision explicitly.

## Sensitive equality (C18)

Comparing `str(x)`, `repr(x)`, or an f-string of a value to a fixed string. This checks how the object prints, not what the object holds. If the `__repr__` method changes, the test breaks even though the behavior is correct. If it stays the same while the value changes, the test keeps passing.

```python
# Lying: only checks the formatted string, not the actual value
assert str(result) == "User(id=42, name='Alice')"
```

Check the attributes directly:

```python
assert result.id == 42
assert result.name == "Alice"
```

## pytest.raises wraps too much (C19)

A `with pytest.raises(SomeError):` block that contains more than one statement. If the first statement raises, the rest never run, and the test passes even though the intended call was never reached.

```python
# Lying: if setup() raises, target() is never called
with pytest.raises(ValueError):
    setup()     # if this raises, target() is skipped
    target()    # the line under test
```

Move everything out of the block except the one call that should raise:

```python
setup()
with pytest.raises(ValueError):
    target()
```

## Async test that never awaits the unit (C22)

An `async def test_*` that calls the unit under test but never `await`s it. The call returns a coroutine object. The assertion runs against that object, not the result, and it passes because a non-None object is truthy and coroutine objects compare as equal to themselves.

```python
# Lying: fetch() is never awaited; coro is a coroutine object, not a result
async def test_fetch():
    coro = fetch(url)
    assert coro is not None   # always true
```

Await the call:

```python
async def test_fetch():
    result = await fetch(url)
    assert result.status == 200
```

## Hard-coded file path (C23)

A test that opens a file at a literal path: `open("data/fixtures/users.csv")` or `Path("config.json").read_text()`. The test is tied to a specific directory layout and will fail anywhere else. Worse, that literal is often a credential file, a private fixture, or something that lives outside the repo.

Use `tmp_path` (pytest fixture) for temporary files, or store fixtures in the test directory and access them relative to `__file__`.

## xfail without strict (C25)

`@pytest.mark.xfail` marks a test as expected to fail. Without `strict=True`, when the test unexpectedly passes (XPASS), pytest treats it as a success and moves on. The bug was fixed, but the test is never promoted back to a normal test — it just silently goes from "expected to fail" to "passes by exception".

Add `strict=True` or set `xfail_strict = true` in `[tool.pytest.ini_options]`. An XPASS then becomes a test failure, which forces you to remove the marker.

## try/except/pass instead of pytest.raises (C27)

A try block whose only handler is a bare pass or a variable assignment with no assertion. The test passes whether the exception is raised or not.

```python
# Lying: passes regardless of whether ValueError is raised
def test_parse_error():
    try:
        parse("bad input")
    except ValueError:
        pass   # exception swallowed, test passes either way
    assert True
```

Use `pytest.raises`:

```python
def test_parse_error():
    with pytest.raises(ValueError, match="invalid"):
        parse("bad input")
```

## pytest.raises binding never read (C28)

`with pytest.raises(E) as exc:` declares a binding to inspect the exception, but `exc` is never read after the block. The exception type is verified, but its message, args, and attributes are not. A wrong exception with the right type passes undetected.

```python
# Lying: exc.value is never checked
with pytest.raises(ValueError) as exc:
    parse("bad input")
# exc is never read — any ValueError passes
```

Add `assert "invalid" in str(exc.value)` or use the `match=` argument.

## Direct env mutation (C29)

`os.environ["KEY"] = value` (or `os.environ.update(...)` / `os.putenv(...)`) directly in a test. The mutation survives the test function and leaks to every test that runs after. Tests that depend on that variable passing or missing can then fail or pass unpredictably depending on order.

Use `monkeypatch.setenv()`, which restores the original value automatically when the test ends.

## HTTP mock not activated (C30)

`responses.add()` or `httpretty.register_uri()` registers a mock response, but the HTTP interceptor is never activated (`@responses.activate`, `with responses.RequestsMock()`, or `@httpretty.activate`). Without activation the mock is registered but HTTP calls bypass it and reach the real network. The test passes only when the real server is up and returns the expected data.

## capsys result never asserted (C31)

`capsys.readouterr()` is called but its return value is never asserted. The test captures stdout/stderr, but nothing verifies the content. The capture has no effect on pass/fail — the test passes whether the output is correct or empty.

```python
# Lying: captured is assigned but never checked
def test_output(capsys):
    print_report()
    captured = capsys.readouterr()
```

Check the captured content:

```python
def test_output(capsys):
    print_report()
    captured = capsys.readouterr()
    assert "PASS" in captured.out
```

## skip without reason (C32)

`@pytest.mark.skip` with no `reason=` argument. There is no record of why the test was skipped, when it should be re-enabled, or whether the skip was intentional. A permanently skipped test looks the same as a temporarily skipped one.

Always add `reason="<why>"`. If there is no good reason, remove the skip.

## ML metric computed but not asserted (C33)

Calling `model.score()`, `accuracy_score()`, `f1_score()`, or any other sklearn metric function without asserting on the return value. The test passes regardless of actual model performance — a model with 10% accuracy is treated the same as one with 95%.

```python
# Lying: score is computed and discarded
def test_model():
    model.fit(X_train, y_train)
    model.score(X_test, y_test)   # result thrown away
    assert model is not None
```

Assert that the metric meets a minimum threshold:

```python
def test_model():
    model.fit(X_train, y_train)
    acc = model.score(X_test, y_test)
    assert acc >= 0.80
```

## Suboptimal assert form (C34)

Python and pytest provide clearer, more idiomatic alternatives to some common patterns. The alternatives produce better failure messages and signal intent more directly.

| Avoid | Prefer |
|---|---|
| `assert not x in y` | `assert x not in y` |
| `assert len(x) == 0` | `assert not x` |
| `assert x == True` | `assert x` |
| `assert x == False` | `assert not x` |
| `assert x == None` | `assert x is None` |
| `assert x != None` | `assert x is not None` |

## Retry decorator masks flakiness (C35)

`@pytest.mark.flaky`, `@pytest.mark.repeat`, or `@pytest.mark.retry` on a test makes it pass on the Nth attempt. The test reports green by chance, hiding a non-deterministic SUT instead of surfacing the root cause. Remove the decorator and fix the flakiness: isolate the random factor, remove the test-order dependency, or mock the external service.

## pytest.fail without a reason (C36)

`pytest.fail()` with no message. The build log shows a failure with no explanation of what invariant was violated. Pass a descriptive string: `pytest.fail("expected status to be DONE, got PENDING")`.

## Duplicate parametrize case (C37)

The same argument set appears more than once in a `@pytest.mark.parametrize` list: `parametrize("x", [1, 2, 1])`. The repeated case runs the exact same scenario twice, adding CI time without adding coverage.

---

# Opt-in diagnostic codes

The codes below surface smells that do not make a test a false positive but reduce observability and maintainability. They are disabled by default. Enable with `CODE = "info"` in `[tool.falsegreen.severity]`.

## D1. Assertion Roulette

Two or more assertions in one test, all without a `msg` argument. When the test fails, pytest shows the failure for one assertion but cannot tell you which one triggered it without re-running or reading the traceback line numbers.

Add a message to each assertion: `assert result == expected, f"expected {expected}, got {result}"`. Or split the test into one assertion per test function.

## D3. Duplicate Assert

The same assertion appears twice in the same test body. The duplicate adds no coverage and suggests the test was edited carelessly.

## D4. Unnamed Parametrize

`@pytest.mark.parametrize` with three or more cases and no `ids=` argument. Without ids, pytest names each case `test_foo[0]`, `test_foo[1]`, etc. When one fails, you cannot tell from the name alone which scenario failed.

Add `ids=["name1", "name2", ...]` or a callable. Short, descriptive ids make failures immediately readable in CI output.

## D5. Too many inline setup statements

More than five assignments and bare function calls before the first `assert` in a test body. When a test arranges everything in its own body rather than delegating to a fixture, the "arrange" phase buries the "act" and "assert" phases and makes the test hard to read and maintain.

Move the shared setup into a pytest fixture or a helper function. Threshold is configurable with `inline_setup_threshold` in the project config.

## D6. Debug print

A `print()` call in a test body. Print statements left after debugging pass CI noise through without checking anything. Remove them or replace with an assertion.

## M2. Long test method

The test body exceeds `long_test_threshold` lines (default 50). A very long test almost always does more than one thing, which makes failures hard to pinpoint and refactoring costly. Split it into focused tests, each with one clear scenario.

---

# Before you trust the green

## What not to use in a unit test

If one of your tests lands on one of these lines, it probably does not protect what you think it protects.

| Do not use | Why it fools you | Do this instead |
|---|---|---|
| `assert` inside `if`/`for` that may not run | the check vanishes when the condition is false | check the condition first, then the content |
| test that calls the function and checks nothing | only proves it did not blow up | always end by checking result, effect, or state |
| `assert` inside `try/except` that swallows the error | the check's failure is discarded | use `pytest.raises` for the expected error |
| test named outside the `test_` convention | the runner never executes it | check the count with `--collect-only` |
| `assert True`, `assert x or True` | passes by construction | compare against a concrete expected value |
| `assert result`, `len(x) > 0`, `"ok" in text` | accepts almost anything that is not empty | check the exact content |
| `assert x == x` or both sides from the same source | true by construction | one side is a fixed value written by hand |
| `==` on a decimal number | fails on rounding, not on a bug | compare with a tolerance (`pytest.approx`) |
| `pytest.raises(Exception)` | accepts any error, even the wrong one | name the exact type and the message |
| a double in place of the part the test investigates | tests the double, not the code | doubles only at the edges |
| checking the value you put into the double yourself | it is an echo, not a result | check what the code transformed |
| expected value computed with the program's formula | both err together | write the expected value by hand |
| a misspelled mock command name | becomes an empty command that accepts all | create the mock with `autospec` |
| answer key generated from the output itself | records today's error as correct | independent reference, reviewed by a person |
| a test that depends on another running first | passes only in the right order | each test builds and cleans its own scenario |
| a result that depends on time, chance, or a pause | passes or fails by luck | freeze time and seed, wait on a condition |
| `skip` to hide a real error | sweeps the defect under the rug | skip only on a declared environment condition |
| expected value that contradicts the business rule | freezes the bug as if it were correct | take the expected from the rule, not the run |
| commented-out `assert` | check silently disabled | fix the test or delete the assertion |
| `str(x) == "..."` or `repr(x) == "..."` | checks formatting, not value | check the attribute directly |
| `pytest.raises` block with multiple calls | first line may raise; target never reached | one call per `raises` block |
| `async def test_*` that never `await`s the unit | the assertion runs on a coroutine, not a result | `await` the call |
| literal hard-coded file path | binds test to one layout, often a credential | use `tmp_path` or a relative fixture path |
| `@pytest.mark.xfail` without `strict=True` | fixed bugs silently stay marked as expected failures | add `strict=True` |
| `try/except/pass` instead of `pytest.raises` | passes whether the exception is raised or not | use `with pytest.raises(E, match=...)` |
| `pytest.raises` binding never read after the block | exception type checked, message/args not | read `exc.value` or use `match=` |
| `os.environ["KEY"] = ...` in a test | env mutation leaks between tests | use `monkeypatch.setenv()` |
| HTTP mock without activating the interceptor | mock registered but calls reach the real network | activate `@responses.activate` or equivalent |
| `capsys.readouterr()` result never asserted | capture has no effect on pass/fail | assert on `captured.out` / `captured.err` |
| `@pytest.mark.skip` without `reason=` | no record of why or when to re-enable | always add `reason="..."` |
| ML metric computed but not asserted | any accuracy passes the test | assert a minimum threshold |
| retry decorator on a flaky test | masks the root cause, passes by luck | fix the flakiness; remove the decorator |
| `pytest.fail()` with no message | build log shows failure with no explanation | add a descriptive reason string |
| duplicate values in `parametrize` | same scenario runs twice with no extra coverage | remove the duplicate case |

## Four checks that catch the rest

Before trusting any green test, do these four things once.

Watch the test go red. Break the program on purpose (change a result, flip a
condition) and run the test. If it does not fail, it protects nothing. Undo the
break afterward. It is the cheapest control there is, and the most ignored.

Read what the test claims. If you delete the program's logic and the test stays
green, the check is looking in the wrong place.

Run the test alone and in a shuffled order. Passed in isolation? Good. Only passes
together with the others? There is a hidden dependency.

In an integration test, no doubles. Real input, real program, real output. A double
in the wrong place is the biggest false-positive factory there is.

There is also a tool that does this work automatically, called mutation testing (in
Python, `mutmut` or `cosmic-ray`). It changes the program on purpose at many points
and checks whether any test fails. Each change that slips by unnoticed reveals a
stretch covered on paper but not actually verified. It is the most honest measure of
your tests' quality.

One question is enough. Before celebrating a green test, ask: is there any way
for the code to be wrong and this test to keep passing? If the answer is yes,
fix the test first.
