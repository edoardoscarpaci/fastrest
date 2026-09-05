"""
varco_beanie._bson_time
=======================
BSON datetime-resolution helpers shared by the Beanie retention surfaces.

WHY THIS MODULE EXISTS
----------------------
BSON's ``UTCDateTime`` is **millisecond**-precision, while Python's
``datetime`` is microsecond-precision. pymongo truncates (floors) the
sub-millisecond remainder in **both** directions:

* on **write**, the entry stored for ``12:00:00.003100`` is ``12:00:00.003``;
* on **read of a query operand**, ``{"$lt": 12:00:00.003900}`` is sent to the
  server as ``{"$lt": 12:00:00.003}``.

The second truncation is the dangerous one. A strict ``$lt`` cutoff therefore
evaluates as ``stored_ms < floor_ms(cutoff)``, which **excludes every entry
sharing a millisecond with the cutoff** — even though the value the store
itself reports for that entry (``.003``) is strictly less than the cutoff
(``.0039``). A retention sweep that re-passes a fixed cutoff then loops until
``delete_where()`` returns 0 while a matching entry is still present: the
sweep reports "done" and is not.

DESIGN: widen an exclusive upper bound to the next whole millisecond
    ✅ Restores the ABC contract *as observed through this store*: every entry
       whose **stored** ``last_failed_at`` is strictly before the cutoff is
       matched, exactly as ``InMemoryDeadLetterQueue``/``SADeadLetterQueue``
       (lossless stores, where floor and ceiling coincide) behave.
    ✅ Cannot delete an entry the store would report as *not* older: an entry
       written at ``.003900`` is stored — and returned by ``get()``,
       ``list_entries()`` — as ``.003``, which genuinely is ``< .0039``. The
       widening only reaches values the store has already collapsed onto the
       matching side.
    ❌ Sub-millisecond ordering is unrecoverable once written, so a caller
       cannot distinguish two entries inside one millisecond. That is a
       property of BSON, not of this helper.

DESIGN: lower bounds (``$gt``/``$gte``) and inclusive upper bounds (``$lte``)
        are deliberately NOT adjusted
    ✅ pymongo's floor is already the correct rounding for those three: for
       ``{"$gt": .0039}`` a stored ``.003`` must not match, and ``floor``
       gives ``.003 > .003 → false``. Widening them would be a real
       over-match. The asymmetry is intended — only an *exclusive upper*
       bound rounds up.

Thread safety:  ✅ Pure functions over immutable values.
Async safety:   ✅ No I/O.
"""

from __future__ import annotations

from datetime import datetime, timedelta

_MICROS_PER_MILLI = 1000


def ceil_to_bson_millisecond(moment: datetime) -> datetime:
    """
    Round ``moment`` up to the next whole millisecond, for use as an
    **exclusive upper bound** (``$lt``) against a BSON-stored datetime.

    Args:
        moment: The caller-supplied cutoff, at full Python microsecond
            precision. May be naive or aware — the tzinfo is preserved
            untouched.

    Returns:
        ``moment`` itself when it is already millisecond-aligned, otherwise
        the next millisecond boundary after it. The result is always
        ``>= moment`` and ``< moment + 1ms``.

    Never raises — every input is representable.

    Edge cases:
        - Already aligned (``.003000``) → returned unchanged, so an aligned
          cutoff keeps exact ``$lt`` semantics with no widening at all.
        - ``.003001`` → ``.004000`` (a full millisecond of widening is the
          minimum the store can express).
        - Rolls over seconds/minutes/days naturally via ``timedelta``.
    """
    remainder = moment.microsecond % _MICROS_PER_MILLI
    if remainder == 0:
        return moment
    return moment + timedelta(microseconds=_MICROS_PER_MILLI - remainder)


__all__ = ["ceil_to_bson_millisecond"]
